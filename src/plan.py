"""Weekly meal plan domain model.

A plan covers one ISO week (Monday–Sunday). Each day has a flexible list
of meals — no fixed breakfast/lunch/dinner slots. Meals reference dishes
by name with a portion count. The plan tracks status lifecycle, prep-day
items, and leftovers.
"""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from .dish import Dish

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

VALID_STATUSES = ("draft", "approved", "active", "archived")
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


MEAL_STATUSES = ("planned", "cooked", "skipped", "cancelled", "moved", "substituted")
PLAN_SCHEMA_VERSION = 2


def _utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _legacy_occurrence_id(week_id, day_code, index, dish, portions):
    payload = json.dumps(
        [week_id, day_code, index, Dish.normalize_name(dish), portions],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "mealocc_" + hashlib.sha256(payload).hexdigest()[:24]


def _planned_date(week_id, day_code):
    match = _WEEK_RE.fullmatch(week_id)
    if match is None:
        raise ValueError("week must be normalized before deriving a planned date")
    weekday = DAYS.index(day_code) + 1
    return date.fromisocalendar(
        int(match.group(1)), int(match.group(2)), weekday
    ).isoformat()


@dataclass
class MealEntry:
    """One stable meal occurrence within a weekly plan."""

    dish: str
    portions: int = 2
    occurrence_id: str = field(default_factory=lambda: "mealocc_" + uuid.uuid4().hex)
    root_occurrence_id: str | None = None
    predecessor_occurrence_id: str | None = None
    status: str = "planned"
    planned_for: str | None = None
    revision: int = 1
    created_at: str | None = field(default_factory=_utc_now)
    updated_at: str | None = field(default_factory=_utc_now)
    status_changed_at: str | None = field(default_factory=_utc_now)
    cooked_at: str | None = None
    cooked_on: str | None = None
    cooked_time_precision: str | None = None
    actual_portions: int | None = None
    actual_yield_portions: int | None = None
    replacement_occurrence_id: str | None = None
    cook_event_id: str | None = None
    leftover_lot_ids: list[str] = field(default_factory=list)
    provenance: dict | None = None

    def __post_init__(self):
        self.dish = Dish.normalize_name(self.dish)
        if not self.dish:
            raise ValueError("dish reference cannot be empty")
        if not isinstance(self.portions, int) or isinstance(self.portions, bool):
            raise ValueError("portions must be an integer")
        if self.portions < 1:
            raise ValueError("portions must be >= 1")
        if (
            not isinstance(self.occurrence_id, str)
            or not self.occurrence_id.startswith("mealocc_")
        ):
            raise ValueError("occurrence_id must start with mealocc_")
        if self.root_occurrence_id is None:
            self.root_occurrence_id = self.occurrence_id
        if (
            not isinstance(self.root_occurrence_id, str)
            or not self.root_occurrence_id.startswith("mealocc_")
        ):
            raise ValueError("root_occurrence_id must start with mealocc_")
        if self.status not in MEAL_STATUSES:
            raise ValueError(f"meal status must be one of {MEAL_STATUSES}")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("meal revision must be a positive integer")
        if not isinstance(self.leftover_lot_ids, list) or not all(
            isinstance(item, str) and item for item in self.leftover_lot_ids
        ):
            raise ValueError("leftover_lot_ids must be a list of non-empty strings")
        for value, label in (
            (self.actual_portions, "actual_portions"),
            (self.actual_yield_portions, "actual_yield_portions"),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{label} must be a non-negative integer or null")

    @property
    def portions_planned(self):
        return self.portions

    def bind_to_plan(self, week_id, day_code):
        expected = _planned_date(week_id, day_code)
        if self.planned_for is None:
            self.planned_for = expected
        elif self.planned_for != expected:
            raise ValueError("meal planned_for does not match plan day")
        return self

    def revise(self, *, dish, portions, expected_revision=None):
        if expected_revision is not None and self.revision != expected_revision:
            raise ValueError(
                f"stale meal occurrence revision: expected {expected_revision}, current {self.revision}"
            )
        if self.status != "planned":
            raise ValueError(f"cannot edit a {self.status} meal occurrence")
        normalized = Dish.normalize_name(dish)
        if not normalized:
            raise ValueError("meal dish reference cannot be empty")
        if not isinstance(portions, int) or isinstance(portions, bool) or portions < 1:
            raise ValueError("meal portions must be a positive integer")
        if normalized == self.dish and portions == self.portions:
            return False
        self.dish = normalized
        self.portions = portions
        self.revision += 1
        self.updated_at = _utc_now()
        return True

    def transition_to(self, status, *, expected_revision=None):
        if expected_revision is not None and expected_revision != self.revision:
            raise ValueError("meal occurrence revision conflict")
        if self.status != "planned" or status not in MEAL_STATUSES[1:]:
            raise ValueError(f"cannot transition meal occurrence from {self.status} to {status}")
        now = _utc_now()
        self.status = status
        self.revision += 1
        self.updated_at = now
        self.status_changed_at = now
        return self

    def to_dict(self):
        return {
            "occurrence_id": self.occurrence_id,
            "root_occurrence_id": self.root_occurrence_id,
            "predecessor_occurrence_id": self.predecessor_occurrence_id,
            "dish": self.dish,
            "portions_planned": self.portions,
            "status": self.status,
            "planned_for": self.planned_for,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status_changed_at": self.status_changed_at,
            "cooked_at": self.cooked_at,
            "cooked_on": self.cooked_on,
            "cooked_time_precision": self.cooked_time_precision,
            "actual_portions": self.actual_portions,
            "actual_yield_portions": self.actual_yield_portions,
            "replacement_occurrence_id": self.replacement_occurrence_id,
            "cook_event_id": self.cook_event_id,
            "leftover_lot_ids": list(self.leftover_lot_ids),
            "provenance": self.provenance,
        }

    @classmethod
    def from_dict(cls, data, *, week_id=None, day_code=None, index=None, legacy=False):
        if not isinstance(data, dict):
            raise ValueError("meal entry must be a dict")
        portions = data.get("portions_planned", data.get("portions", 2))
        if legacy:
            if week_id is None or day_code is None or index is None:
                raise ValueError("legacy meal migration requires week/day/index context")
            occurrence_id = _legacy_occurrence_id(
                week_id, day_code, index, data["dish"], portions
            )
            return cls(
                dish=data["dish"],
                portions=portions,
                occurrence_id=occurrence_id,
                root_occurrence_id=occurrence_id,
                planned_for=_planned_date(week_id, day_code),
                created_at=None,
                updated_at=None,
                status_changed_at=None,
                provenance={"source": "legacy_plan_migration", "backfilled": True},
            )
        return cls(
            dish=data["dish"],
            portions=portions,
            occurrence_id=data["occurrence_id"],
            root_occurrence_id=data["root_occurrence_id"],
            predecessor_occurrence_id=data.get("predecessor_occurrence_id"),
            status=data["status"],
            planned_for=data.get("planned_for"),
            revision=data["revision"],
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
            status_changed_at=data.get("status_changed_at"),
            cooked_at=data.get("cooked_at"),
            cooked_on=data.get("cooked_on"),
            cooked_time_precision=data.get("cooked_time_precision"),
            actual_portions=data.get("actual_portions"),
            actual_yield_portions=data.get("actual_yield_portions"),
            replacement_occurrence_id=data.get("replacement_occurrence_id"),
            cook_event_id=data.get("cook_event_id"),
            leftover_lot_ids=data.get("leftover_lot_ids", []),
            provenance=data.get("provenance"),
        )


@dataclass
class DayPlan:
    """One day's meals: a list of MealEntry plus optional note."""

    meals: list[MealEntry] = field(default_factory=list)
    note: str = ""

    def to_dict(self):
        result: dict[str, object] = {"meals": [m.to_dict() for m in self.meals]}
        if self.note:
            result["note"] = self.note
        return result

    @classmethod
    def from_dict(cls, data, *, week_id=None, day_code=None, legacy=False):
        if not isinstance(data, dict):
            raise ValueError("day plan must be a dict")
        raw_meals = data.get("meals", [])
        if not isinstance(raw_meals, list):
            raise ValueError("day meals must be a list")
        return cls(
            meals=[
                MealEntry.from_dict(
                    meal,
                    week_id=week_id,
                    day_code=day_code,
                    index=index,
                    legacy=legacy,
                )
                for index, meal in enumerate(raw_meals)
            ],
            note=data.get("note", ""),
        )


@dataclass
class WeekPlan:
    """A weekly meal plan.

    Invariant: ``week_id`` is an ISO week string like ``2026-W03``.
    """

    week_id: str
    status: str = "draft"
    prep: list[str] = field(default_factory=list)
    days: dict[str, DayPlan] = field(default_factory=dict)
    leftovers: dict = field(default_factory=dict)
    shopping: dict = field(default_factory=dict)

    def __post_init__(self):
        self.week_id = self.normalize_week_id(self.week_id)
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"status must be one of {VALID_STATUSES}, got '{self.status}'"
            )
        self.prep = [self.normalize_prep_name(name) for name in self.prep]
        if not isinstance(self.shopping, dict):
            raise ValueError("shopping must be a dict")
        if self.shopping:
            from .plan_shopping import validate_shopping_snapshot
            validate_shopping_snapshot(self.shopping)
        # Ensure all days exist and bind new in-memory occurrences to dates.
        occurrence_ids = set()
        for day_code in DAYS:
            if day_code not in self.days:
                self.days[day_code] = DayPlan()
            for meal in self.days[day_code].meals:
                if meal.planned_for is None:
                    meal.planned_for = _planned_date(self.week_id, day_code)
                if meal.occurrence_id in occurrence_ids:
                    raise ValueError("meal occurrence IDs must be unique within a plan")
                occurrence_ids.add(meal.occurrence_id)

    def to_dict(self):
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "week": self.week_id,
            "status": self.status,
            "prep": list(self.prep),
            "days": {day: self.days[day].to_dict() for day in DAYS},
            "leftovers": dict(self.leftovers),
            "shopping": dict(self.shopping),
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("week plan must be a dict")

        week_id = cls.normalize_week_id(data["week"])
        schema_version = data.get("schema_version")
        if schema_version is None:
            legacy = True
        elif (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version != PLAN_SCHEMA_VERSION
        ):
            raise ValueError(f"unsupported plan schema_version {schema_version!r}")
        else:
            legacy = False
        status = data.get("status", "draft")
        prep = data.get("prep", [])
        if not isinstance(prep, list):
            prep = []

        raw_leftovers = data.get("leftovers", {})
        if not isinstance(raw_leftovers, dict):
            raw_leftovers = {}

        raw_shopping = data.get("shopping", {})
        if not isinstance(raw_shopping, dict):
            raise ValueError("shopping must be a dict")

        raw_days = data.get("days", {})
        if not isinstance(raw_days, dict) or set(raw_days) != set(DAYS):
            raise ValueError("days must contain exactly mon, tue, wed, thu, fri, sat, sun")
        days = {}
        for day_code in DAYS:
            days[day_code] = DayPlan.from_dict(
                raw_days[day_code],
                week_id=week_id,
                day_code=day_code,
                legacy=legacy,
            )

        return cls(
            week_id=week_id,
            status=status,
            prep=prep,
            days=days,
            leftovers=raw_leftovers,
            shopping=raw_shopping,
        )

    @staticmethod
    def normalize_prep_name(name):
        return Dish.normalize_name(name)

    @staticmethod
    def normalize_week_id(value) -> str:
        if not isinstance(value, str):
            raise ValueError("week must be an ISO week string, e.g. '2026-W03'")
        normalized = value.strip().upper()
        match = _WEEK_RE.fullmatch(normalized)
        if not match:
            raise ValueError("week must use ISO format YYYY-Www, e.g. '2026-W03'")
        try:
            date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
        except ValueError as exc:
            raise ValueError(f"invalid ISO week '{normalized}'") from exc
        return normalized
