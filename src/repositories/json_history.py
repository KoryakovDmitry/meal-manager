"""Canonical cooking-occurrence history repository."""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .. import atomic_write_json, read_json_file
from ..dish import Dish
from ..identifiers import validate_cook_event_id, validate_meal_occurrence_id
from .file_lock import JsonFileLock


HISTORY_SCHEMA_VERSION = 2


class HistoryDataError(ValueError):
    """Canonical history storage is unreadable or violates its schema."""


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _legacy_id(source, dish, cooked_on, index):
    payload = json.dumps(
        [source, Dish.normalize_name(dish), cooked_on, index],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "cook_" + hashlib.sha256(payload).hexdigest()[:24]


def validate_event_lineage(events):
    """Validate global IDs and typed correction chains without row ordering."""
    by_id = {}
    active_by_occurrence = {}
    for event in events:
        if event.id in by_id:
            raise ValueError(f"duplicate cooking event id '{event.id}'")
        by_id[event.id] = event
        if event.plan_occurrence_id is not None and event.active:
            active_by_occurrence.setdefault(event.plan_occurrence_id, []).append(event)
    if any(len(active) > 1 for active in active_by_occurrence.values()):
        raise ValueError("linked plan occurrence has multiple active cooking events")

    children = {}
    parents = {}
    roots_by_occurrence = {}
    for event in events:
        provenance = event.provenance
        if not (
            isinstance(provenance, dict)
            and provenance.get("source") == "cook_event_correction"
        ):
            continue
        expected_fields = {
            "source",
            "replaces_event_id",
            "root_event_id",
            "effects_origin_event_id",
            "request_fingerprint",
            "acknowledged_legacy_event_ids",
        }
        if set(provenance) != expected_fields:
            raise ValueError("cooking correction provenance fields are invalid")
        acknowledged_ids = provenance["acknowledged_legacy_event_ids"]
        if (
            not isinstance(acknowledged_ids, list)
            or any(
                not isinstance(item, str)
                or re.fullmatch(r"cook_[0-9a-f]{24,32}", item) is None
                for item in acknowledged_ids
            )
            or len(set(acknowledged_ids)) != len(acknowledged_ids)
        ):
            raise ValueError(
                "cooking correction acknowledged legacy ids are invalid"
            )
        predecessor_id = provenance["replaces_event_id"]
        root_id = provenance["root_event_id"]
        effects_origin_id = provenance["effects_origin_event_id"]
        request_fingerprint = provenance["request_fingerprint"]
        for value, label in (
            (predecessor_id, "correction predecessor id"),
            (root_id, "correction root id"),
            (effects_origin_id, "correction effects-origin id"),
        ):
            validate_cook_event_id(value, label)
        if event.plan_occurrence_id is None:
            raise ValueError("cooking correction requires a linked plan occurrence")
        if (
            not isinstance(request_fingerprint, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", request_fingerprint) is None
        ):
            raise ValueError("cooking correction request fingerprint is invalid")
        predecessor = by_id.get(predecessor_id)
        if predecessor is None or predecessor is event:
            raise ValueError("cooking correction predecessor is missing or cyclic")
        if (
            predecessor.plan_occurrence_id != event.plan_occurrence_id
            or predecessor.dish_name_snapshot != event.dish_name_snapshot
        ):
            raise ValueError("cooking correction predecessor belongs to another chain")
        if predecessor_id in children:
            raise ValueError("cooking correction lineage forks")
        parent_provenance = predecessor.provenance
        if (
            isinstance(parent_provenance, dict)
            and parent_provenance.get("source") == "cook_event_correction"
        ):
            expected_root = parent_provenance["root_event_id"]
            expected_effects_origin = parent_provenance["effects_origin_event_id"]
        else:
            expected_root = predecessor.id
            expected_effects_origin = predecessor.id
        if root_id != expected_root or effects_origin_id != expected_effects_origin:
            raise ValueError("cooking correction root/effects lineage changed")
        if predecessor.active:
            raise ValueError("superseded cooking event must be retracted")
        children[predecessor_id] = event.id
        parents[event.id] = predecessor_id
        roots_by_occurrence.setdefault(event.plan_occurrence_id, set()).add(root_id)

    plain_rooted = {}
    acknowledged_by_occurrence = {}
    for event in events:
        provenance = event.provenance
        if (
            isinstance(provenance, dict)
            and provenance.get("source") == "cook_event_correction"
        ):
            occurrence = event.plan_occurrence_id
            if occurrence is not None:
                acknowledged_by_occurrence.setdefault(occurrence, set()).update(
                    provenance["acknowledged_legacy_event_ids"]
                )
            continue
        if event.plan_occurrence_id is None:
            continue
        plain_rooted.setdefault(event.plan_occurrence_id, set()).add(event.id)
    for occurrence_id, plain_roots in plain_rooted.items():
        correction_roots = roots_by_occurrence.get(occurrence_id, set())
        allowed = (
            correction_roots
            | acknowledged_by_occurrence.get(occurrence_id, set())
        )
        if correction_roots and not plain_roots <= allowed:
            raise ValueError(
                "linked occurrence has disconnected correction chains"
            )
    for event_id in parents:
        seen = set()
        current = event_id
        while current in parents:
            if current in seen:
                raise ValueError("cooking correction lineage contains a cycle")
            seen.add(current)
            current = parents[current]
    for event in events:
        if event.active and event.id in children:
            raise ValueError("active cooking event is not a correction-chain tip")
    return by_id, children


@dataclass
class CookingEvent:
    id: str
    dish_name_snapshot: str
    cooked_at: str | None = None
    cooked_on: str | None = None
    time_precision: str = "date"
    recorded_at: str | None = None
    plan_occurrence_id: str | None = None
    actual_portions: int | None = None
    actual_yield_portions: int | None = None
    retracted_at: str | None = None
    backfilled: bool = False
    provenance: dict | None = None

    def __post_init__(self):
        self.dish_name_snapshot = Dish.normalize_name(self.dish_name_snapshot)
        if not self.dish_name_snapshot:
            raise ValueError("cooking event dish name cannot be empty")
        validate_cook_event_id(self.id)
        validate_meal_occurrence_id(
            self.plan_occurrence_id, "plan_occurrence_id", optional=True
        )
        if self.time_precision not in {"date", "datetime"}:
            raise ValueError("cooking event time_precision must be date or datetime")
        if self.time_precision == "date":
            if self.cooked_at is not None or self.cooked_on is None:
                raise ValueError("date-precision cooking events require cooked_on only")
            date.fromisoformat(self.cooked_on)
        else:
            if self.cooked_at is None:
                raise ValueError("datetime-precision cooking events require cooked_at")
            parsed = datetime.fromisoformat(self.cooked_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("cooked_at must be timezone-aware")
            if self.cooked_on is None:
                self.cooked_on = parsed.date().isoformat()
            elif date.fromisoformat(self.cooked_on) != parsed.date():
                raise ValueError("cooked_on must match cooked_at calendar date")
        for value, label in (
            (self.actual_portions, "actual_portions"),
            (self.actual_yield_portions, "actual_yield_portions"),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{label} must be a non-negative integer or null")
        if (
            self.actual_portions is not None
            and self.actual_yield_portions is not None
            and self.actual_yield_portions < self.actual_portions
        ):
            raise ValueError(
                "actual_yield_portions cannot be below actual_portions served"
            )
        if not isinstance(self.backfilled, bool):
            raise ValueError("backfilled must be boolean")
        for value, label in (
            (self.recorded_at, "recorded_at"),
            (self.retracted_at, "retracted_at"),
        ):
            if value is None:
                if label == "recorded_at" and not self.backfilled:
                    raise ValueError("non-backfilled cooking events require recorded_at")
                continue
            if not isinstance(value, str) or not value.endswith("Z"):
                raise ValueError(f"{label} must be a canonical UTC timestamp")
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo != timezone.utc:
                raise ValueError(f"{label} must be UTC")
        if self.provenance is not None and (
            not isinstance(self.provenance, dict)
            or not isinstance(self.provenance.get("source"), str)
            or not self.provenance["source"]
        ):
            raise ValueError("cooking event provenance requires source")

    @property
    def active(self):
        return self.retracted_at is None

    def to_dict(self):
        return {
            "id": self.id,
            "dish_name_snapshot": self.dish_name_snapshot,
            "cooked_at": self.cooked_at,
            "cooked_on": self.cooked_on,
            "time_precision": self.time_precision,
            "recorded_at": self.recorded_at,
            "plan_occurrence_id": self.plan_occurrence_id,
            "actual_portions": self.actual_portions,
            "actual_yield_portions": self.actual_yield_portions,
            "retracted_at": self.retracted_at,
            "backfilled": self.backfilled,
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("cooking history entries must be objects")
        expected = {
            "id", "dish_name_snapshot", "cooked_at", "cooked_on",
            "time_precision", "recorded_at", "plan_occurrence_id",
            "actual_portions", "actual_yield_portions", "retracted_at",
            "backfilled", "provenance",
        }
        if set(data) != expected:
            raise ValueError("cooking history entry fields do not match schema v2")
        return cls(**data)


class JsonHistoryRepository:
    """Stores stable cooking occurrences and derives latest-cook compatibility."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = JsonFileLock(lambda: self.path)

    def _load_raw(self):
        return read_json_file(self.path, missing=None)

    def _migrate(self, raw):
        if raw is None:
            return []
        if not isinstance(raw, dict):
            raise ValueError("cooking history must be a JSON object")
        if "schema_version" in raw:
            version = raw.get("schema_version")
            if (
                not isinstance(version, int)
                or isinstance(version, bool)
                or version != HISTORY_SCHEMA_VERSION
            ):
                raise ValueError(f"unsupported history schema_version {version!r}")
            if set(raw) != {"schema_version", "entries"} or not isinstance(raw["entries"], list):
                raise ValueError("history schema v2 must contain an entries list")
            events = [CookingEvent.from_dict(item) for item in raw["entries"]]
            validate_event_lineage(events)
            return events
        if set(raw) == {"history"}:
            rows = raw["history"]
            if not isinstance(rows, list):
                raise ValueError("legacy Web history must contain a list")
            events = []
            for index, row in enumerate(rows):
                if not isinstance(row, dict) or not isinstance(row.get("dish"), str):
                    raise ValueError("legacy Web history row is invalid")
                raw_date = row.get("date")
                if not isinstance(raw_date, str):
                    raise ValueError("legacy Web history date is invalid")
                parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                if parsed.tzinfo is None and "T" in raw_date:
                    raise ValueError("legacy Web history datetime must be timezone-aware")
                if "T" in raw_date:
                    cooked_at = parsed.isoformat().replace("+00:00", "Z")
                    cooked_on = parsed.date().isoformat()
                    precision = "datetime"
                else:
                    cooked_at = None
                    cooked_on = date.fromisoformat(raw_date).isoformat()
                    precision = "date"
                events.append(CookingEvent(
                    id=_legacy_id("web_v1", row["dish"], raw_date, index),
                    dish_name_snapshot=row["dish"],
                    cooked_at=cooked_at,
                    cooked_on=cooked_on,
                    time_precision=precision,
                    recorded_at=None,
                    backfilled=True,
                    provenance={"source": "legacy_web_history"},
                ))
            validate_event_lineage(events)
            return events
        events = []
        for index, (name, cooked_on) in enumerate(sorted(raw.items())):
            if not isinstance(name, str) or not isinstance(cooked_on, str):
                raise ValueError("legacy native history entries must map names to dates")
            cooked_on = date.fromisoformat(cooked_on).isoformat()
            events.append(CookingEvent(
                id=_legacy_id("native_v1", name, cooked_on, index),
                dish_name_snapshot=name,
                cooked_on=cooked_on,
                time_precision="date",
                recorded_at=None,
                backfilled=True,
                provenance={"source": "legacy_native_history"},
            ))
        validate_event_lineage(events)
        return events

    def load_events(self, *, strict=False):
        try:
            return self._migrate(self._load_raw())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            if strict:
                raise HistoryDataError("cooking history storage is corrupt") from exc
            return []

    def load(self, *, strict=False) -> dict[str, str]:
        latest = {}
        for event in self.load_events(strict=strict):
            if not event.active:
                continue
            cooked_on = event.cooked_on
            current = latest.get(event.dish_name_snapshot)
            if current is None or date.fromisoformat(cooked_on) > date.fromisoformat(current):
                latest[event.dish_name_snapshot] = cooked_on
        return latest

    def _save_events_unlocked(self, events):
        atomic_write_json(self.path, {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "entries": [event.to_dict() for event in events],
        })

    def save_events(self, events):
        with self.lock:
            self._save_events_unlocked(events)

    def append_event(
        self,
        *,
        dish_name,
        cooked_on=None,
        cooked_at=None,
        plan_occurrence_id=None,
        actual_portions=None,
        actual_yield_portions=None,
    ):
        if (cooked_on is None) == (cooked_at is None):
            raise ValueError("provide exactly one of cooked_on or cooked_at")
        with self.lock:
            events = self.load_events(strict=True)
            event = CookingEvent(
                id="cook_" + uuid.uuid4().hex,
                dish_name_snapshot=dish_name,
                cooked_at=cooked_at,
                cooked_on=cooked_on,
                time_precision="datetime" if cooked_at is not None else "date",
                recorded_at=_utc_now(),
                plan_occurrence_id=plan_occurrence_id,
                actual_portions=actual_portions,
                actual_yield_portions=actual_yield_portions,
            )
            events.append(event)
            self._save_events_unlocked(events)
            return event

    def retract_event(self, event_id):
        with self.lock:
            events = self.load_events(strict=True)
            event = next((item for item in events if item.id == event_id), None)
            if event is None or not event.active:
                return False
            event.retracted_at = _utc_now()
            self._save_events_unlocked(events)
            return True

    def set_entry(self, dish_name: str, date_str: str) -> str | None:
        previous = self.load().get(Dish.normalize_name(dish_name))
        self.append_event(dish_name=dish_name, cooked_on=date_str)
        return previous

    def remove_entry(self, dish_name: str) -> bool:
        normalized = Dish.normalize_name(dish_name)
        with self.lock:
            events = self.load_events(strict=True)
            active = [
                event for event in events
                if event.dish_name_snapshot == normalized and event.active
            ]
            if not active:
                return False
            now = _utc_now()
            for event in active:
                event.retracted_at = now
            self._save_events_unlocked(events)
            return True

    def revert_entry(
        self,
        dish_name: str,
        expected_value: str,
        previous_value: str | None,
    ) -> bool:
        normalized = Dish.normalize_name(dish_name)
        with self.lock:
            events = self.load_events(strict=True)
            candidates = [
                event for event in events
                if event.dish_name_snapshot == normalized
                and event.active
                and event.cooked_on == expected_value
            ]
            if not candidates:
                return False
            candidates[-1].retracted_at = _utc_now()
            self._save_events_unlocked(events)
            return True
