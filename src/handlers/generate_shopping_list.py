"""Tool: generate_shopping_list — derive weekly needs from a plan."""

from ..plan_shopping import build_plan_shopping_list, validate_shopping_snapshot
from ..repositories import dish_repo, fridge_repo, plan_repo, prep_repo
from ._common import require_arg, tool_handler
from ._plan_common import normalize_week_id, require_plan

NAME = "generate_shopping_list"
SCHEMA = {
    "description": (
        "Generate and persist the whole-week shopping list. Aggregates one "
        "ingredient use per cooking occurrence, includes source ingredients "
        "for planned/depleted prep items, and subtracts current fridge presence."
    ),
    "type": "object",
    "properties": {
        "week": {"type": "string", "description": "ISO week YYYY-Www"},
    },
    "required": ["week"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    week_id = normalize_week_id(require_arg(args, "week"))
    dishes = dish_repo.load()
    prep_items = prep_repo.load()
    fridge = fridge_repo.load_set()

    with plan_repo.lock:
        plan = require_plan(week_id)
        if plan.status == "archived":
            raise ValueError("archived plans cannot be edited")
        shopping = build_plan_shopping_list(
            plan=plan,
            dishes=dishes,
            prep_items=prep_items,
            fridge=fridge,
        )
        validate_shopping_snapshot(shopping)
        plan.shopping = shopping
        plan_repo.save(plan)

    return shopping
