"""Weekly meal plan domain model.

A plan covers one ISO week (Monday–Sunday). Each day has a flexible list
of meals — no fixed breakfast/lunch/dinner slots. Meals reference dishes
by name with a portion count. The plan tracks status lifecycle, prep-day
items, and leftovers.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from .dish import Dish

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

VALID_STATUSES = ("draft", "approved", "active", "archived")
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


@dataclass
class MealEntry:
    """One meal slot within a day: a dish reference + portions."""

    dish: str
    portions: int = 2

    def __post_init__(self):
        self.dish = Dish.normalize_name(self.dish)
        if not self.dish:
            raise ValueError("dish reference cannot be empty")
        if not isinstance(self.portions, int) or isinstance(self.portions, bool):
            raise ValueError("portions must be an integer")
        if self.portions < 1:
            raise ValueError("portions must be >= 1")

    def to_dict(self):
        return {"dish": self.dish, "portions": self.portions}

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("meal entry must be a dict")
        return cls(
            dish=data["dish"],
            portions=data.get("portions", 2),
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
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("day plan must be a dict")
        raw_meals = data.get("meals", [])
        if not isinstance(raw_meals, list):
            raw_meals = []
        return cls(
            meals=[MealEntry.from_dict(m) for m in raw_meals],
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

    def __post_init__(self):
        self.week_id = self.normalize_week_id(self.week_id)
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"status must be one of {VALID_STATUSES}, got '{self.status}'"
            )
        self.prep = [self.normalize_prep_name(name) for name in self.prep]
        # Ensure all days exist
        for day_code in DAYS:
            if day_code not in self.days:
                self.days[day_code] = DayPlan()

    def to_dict(self):
        return {
            "week": self.week_id,
            "status": self.status,
            "prep": list(self.prep),
            "days": {day: self.days[day].to_dict() for day in DAYS},
            "leftovers": dict(self.leftovers),
        }

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("week plan must be a dict")

        week_id = data["week"]
        status = data.get("status", "draft")
        prep = data.get("prep", [])
        if not isinstance(prep, list):
            prep = []

        raw_leftovers = data.get("leftovers", {})
        if not isinstance(raw_leftovers, dict):
            raw_leftovers = {}

        raw_days = data.get("days", {})
        if not isinstance(raw_days, dict) or set(raw_days) != set(DAYS):
            raise ValueError("days must contain exactly mon, tue, wed, thu, fri, sat, sun")
        days = {}
        for day_code in DAYS:
            if day_code in raw_days:
                days[day_code] = DayPlan.from_dict(raw_days[day_code])
            else:
                days[day_code] = DayPlan()

        return cls(
            week_id=week_id,
            status=status,
            prep=prep,
            days=days,
            leftovers=raw_leftovers,
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
