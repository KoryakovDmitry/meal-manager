"""Tool: add_meal_to_plan — append a dish reference to a plan day."""

from ..plan import MealEntry
from ..repositories import dish_repo, plan_repo
from ._common import normalize_dish_name, require_arg, tool_handler
from ._plan_common import normalize_day, normalize_week_id, require_plan

NAME = "add_meal_to_plan"

SCHEMA = {
    "description": (
        "Add a catalog dish and portion count to a day in a weekly plan. "
        "Days are flexible lists with no breakfast/lunch/dinner binding."
    ),
    "type": "object",
    "properties": {
        "week": {"type": "string", "description": "ISO week YYYY-Www"},
        "day": {
            "type": "string",
            "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        },
        "dish": {"type": "string", "description": "exact catalog dish name"},
        "portions": {
            "type": "integer",
            "minimum": 1,
            "description": "planned portions; defaults to 2",
            "default": 2,
        },
    },
    "required": ["week", "day", "dish"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    week_id = normalize_week_id(require_arg(args, "week"))
    day = normalize_day(require_arg(args, "day"))
    dish_name = normalize_dish_name(require_arg(args, "dish"))
    portions = args.get("portions", 2)

    if not any(dish.name == dish_name for dish in dish_repo.load()):
        raise LookupError(f"dish '{dish_name}' is not in the recipe catalog")

    entry = MealEntry(dish=dish_name, portions=portions)
    with plan_repo.lock:
        plan = require_plan(week_id)
        if plan.status == "archived":
            raise ValueError("archived plans cannot be edited")
        plan.days[day].meals.append(entry)
        plan_repo.save(plan)
        index = len(plan.days[day].meals) - 1

    return {
        "week": week_id,
        "day": day,
        "meal_index": index,
        "meal": entry.to_dict(),
        "status": plan.status,
    }
