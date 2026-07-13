"""Shared validation helpers for weekly-plan handlers."""

from datetime import date

from ..plan import DAYS, WeekPlan
from ..repositories import plan_repo


def current_week_id() -> str:
    year, week, _ = date.today().isocalendar()
    return f"{year}-W{week:02d}"


def normalize_week_id(value, *, default_current: bool = False) -> str:
    if value is None and default_current:
        value = current_week_id()
    return WeekPlan.normalize_week_id(value)


def normalize_day(value) -> str:
    if not isinstance(value, str):
        raise ValueError("day must be one of: " + ", ".join(DAYS))
    day = value.strip().lower()
    if day not in DAYS:
        raise ValueError("day must be one of: " + ", ".join(DAYS))
    return day


def require_plan(week_id: str):
    plan = plan_repo.load(week_id)
    if plan is None:
        raise LookupError(f"no weekly plan found for '{week_id}'")
    return plan
