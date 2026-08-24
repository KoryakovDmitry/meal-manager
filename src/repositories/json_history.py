"""Canonical cooking-occurrence history repository."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .. import atomic_write_json
from ..dish import Dish
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
        if not isinstance(self.id, str) or not self.id.startswith("cook_"):
            raise ValueError("cooking event id must start with cook_")
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
        for value, label in (
            (self.actual_portions, "actual_portions"),
            (self.actual_yield_portions, "actual_yield_portions"),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{label} must be a non-negative integer or null")
        if not isinstance(self.backfilled, bool):
            raise ValueError("backfilled must be boolean")

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
        if not self.path.exists():
            return None
        with open(self.path, "r", encoding="utf-8") as handle:
            return json.load(handle)

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
            return [CookingEvent.from_dict(item) for item in raw["entries"]]
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
        return events

    def load_events(self, *, strict=False):
        try:
            return self._migrate(self._load_raw())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            if strict:
                raise HistoryDataError("cooking history storage is corrupt") from exc
            return []

    def load(self) -> dict[str, str]:
        latest = {}
        for event in self.load_events():
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
