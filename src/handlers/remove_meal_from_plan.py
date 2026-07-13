"""Tool: remove_meal_from_plan — remove one indexed meal from a day."""

from ..repositories import plan_repo
from ._common import require_arg, tool_handler
from ._plan_common import normalize_day, normalize_week_id, require_plan

NAME = "remove_meal_from_plan"

SCHEMA = {
    "description": (
        "Remove a meal from a day in a weekly plan by its zero-based index. "
        "Use get_week_plan first when the index is not known."
    ),
    "type": "object",
    "properties": {
        "week": {"type": "string", "description": "ISO week YYYY-Www"},
        "day": {
            "type": "string",
            "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        },
        "meal_index": {
            "type": "integer",
            "minimum": 0,
            "description": "zero-based meal index within the day",
        },
    },
    "required": ["week", "day", "meal_index"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    week_id = normalize_week_id(require_arg(args, "week"))
    day = normalize_day(require_arg(args, "day"))
    index = require_arg(args, "meal_index")
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise ValueError("meal_index must be a non-negative integer")

    with plan_repo.lock:
        plan = require_plan(week_id)
        if plan.status == "archived":
            raise ValueError("archived plans cannot be edited")
        meals = plan.days[day].meals
        if index >= len(meals):
            raise LookupError(
                f"meal_index {index} is out of range for {day} ({len(meals)} meals)"
            )
        removed = meals.pop(index)
        plan_repo.save(plan)

    return {
        "week": week_id,
        "day": day,
        "removed": removed.to_dict(),
        "remaining_meals": len(plan.days[day].meals),
    }
