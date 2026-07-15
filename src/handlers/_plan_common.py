"""Shared validation helpers for weekly-plan handlers."""

from datetime import date

from ..plan import DAYS, WeekPlan
from ..repositories import (
    dish_repo,
    fridge_repo,
    plan_repo,
    prep_repo,
    shopping_request_repo,
)
from ..shopping import build_current_shopping, persisted_shopping_is_current


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
    plan = plan_repo.load_strict(week_id)
    if plan is None:
        raise LookupError(f"no weekly plan found for '{week_id}'")
    return plan


def require_current_shopping_snapshot(plan):
    current = build_current_shopping(
        plan=plan,
        dishes=dish_repo.load_strict(),
        prep_items=prep_repo.load_strict(),
        catalog_items=fridge_repo.load_catalog_items(),
        manual_requests=shopping_request_repo.load(week=plan.week_id),
    )
    if not persisted_shopping_is_current(plan.shopping, current):
        raise ValueError(
            "shopping list is stale; regenerate it before estimating or splitting"
        )
    return current
