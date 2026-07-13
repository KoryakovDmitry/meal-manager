"""Tool: repeat_week_plan — copy a past week into a new draft."""

from collections import Counter

from ..plan import DayPlan, MealEntry, WeekPlan
from ..repositories import dish_repo, fridge_repo, plan_repo, prep_repo
from ._common import require_arg, tool_handler
from ._plan_common import normalize_week_id, require_plan

NAME = "repeat_week_plan"

SCHEMA = {
    "description": (
        "Copy the meal structure and valid prep references from a past week "
        "into a new draft week. The target defaults to the current ISO week. "
        "Returns missing-fridge information for conversational adaptation."
    ),
    "type": "object",
    "properties": {
        "source_week": {
            "type": "string",
            "description": "past ISO week to copy, YYYY-Www",
        },
        "target_week": {
            "type": "string",
            "description": "new ISO week; defaults to current week",
        },
    },
    "required": ["source_week"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    source_week = normalize_week_id(require_arg(args, "source_week"))
    target_week = normalize_week_id(args.get("target_week"), default_current=True)
    if source_week == target_week:
        raise ValueError("source_week and target_week must be different")

    source = require_plan(source_week)
    dishes = {dish.name: dish for dish in dish_repo.load()}
    prep_items = {item.name: item for item in prep_repo.load()}
    valid_prep = set(prep_items)
    fridge = fridge_repo.load_set()

    skipped_dishes = []
    days = {}
    dish_occurrences = Counter()
    for day_code, source_day in source.days.items():
        meals = []
        for meal in source_day.meals:
            if meal.dish not in dishes:
                skipped_dishes.append(meal.dish)
                continue
            meals.append(MealEntry(dish=meal.dish, portions=meal.portions))
            dish_occurrences[meal.dish] += 1
        days[day_code] = DayPlan(meals=meals, note=source_day.note)

    prep = [name for name in source.prep if name in valid_prep]
    skipped_prep = [name for name in source.prep if name not in valid_prep]

    target = WeekPlan(
        week_id=target_week,
        status="draft",
        prep=prep,
        days=days,
        leftovers={},
    )

    with plan_repo.lock:
        if plan_repo.load(target_week) is not None:
            raise ValueError(f"a weekly plan for '{target_week}' already exists")
        plan_repo.save(target)

    missing_by_dish = {}
    prep_demand = Counter()
    prep_consumers = {}
    for dish_name in sorted(dish_occurrences):
        dish = dishes[dish_name]
        missing = [
            ingredient
            for ingredient, essential in dish.ingredients.items()
            if essential and ingredient not in fridge
        ]
        if missing:
            missing_by_dish[dish_name] = missing
        for dependency in dish.prep_depends:
            prep_demand[dependency] += dish_occurrences[dish_name]
            prep_consumers.setdefault(dependency, set()).add(dish_name)

    unavailable_prep_items = []
    unavailable_prep_by_dish = {}
    for dependency in sorted(prep_demand):
        required = prep_demand[dependency]
        item = prep_items.get(dependency)
        if item is None:
            detail = {
                "prep_item": dependency,
                "reason": "not_defined",
                "required_uses": required,
                "available_uses": 0,
                "consumer_dishes": sorted(prep_consumers[dependency]),
            }
        else:
            available = item.remaining
            if dependency in prep:
                available = max(available, item.yield_qty)
            if available >= required:
                continue
            detail = {
                "prep_item": dependency,
                "reason": "insufficient_quantity",
                "required_uses": required,
                "available_uses": available,
                "planned_for_week": dependency in prep,
                "consumer_dishes": sorted(prep_consumers[dependency]),
            }
        unavailable_prep_items.append(detail)
        for dish_name in prep_consumers[dependency]:
            unavailable_prep_by_dish.setdefault(dish_name, []).append(detail)

    missing_prep_sources = {}
    for prep_name in prep:
        item = prep_items[prep_name]
        missing = [
            ingredient
            for ingredient, essential in item.ingredients.items()
            if essential and ingredient not in fridge
        ]
        if missing:
            missing_prep_sources[prep_name] = missing

    return {
        "source_week": source_week,
        "target_plan": target.to_dict(),
        "adaptation": {
            "missing_essentials_by_dish": missing_by_dish,
            "unavailable_prep_items": unavailable_prep_items,
            "unavailable_prep_by_dish": unavailable_prep_by_dish,
            "missing_prep_source_essentials": missing_prep_sources,
            "skipped_missing_catalog_dishes": list(dict.fromkeys(skipped_dishes)),
            "skipped_missing_prep_items": list(dict.fromkeys(skipped_prep)),
            "note": "Draft copied; review missing ingredients and adjust conversationally.",
        },
    }
