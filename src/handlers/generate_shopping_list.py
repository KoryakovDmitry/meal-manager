"""Tool: generate_shopping_list — derive weekly needs from live sources."""

from ..plan_shopping import validate_shopping_snapshot
from ..repositories import (
    dish_repo,
    fridge_repo,
    plan_repo,
    prep_repo,
    shopping_request_repo,
)
from ..shopping import build_current_shopping, persistable_shopping
from ._common import require_arg, tool_handler
from ._plan_common import normalize_week_id, require_plan

NAME = "generate_shopping_list"
SCHEMA = {
    "description": (
        "Generate and persist the whole-week shopping list. Aggregates one "
        "ingredient use per cooking occurrence, includes source ingredients "
        "for planned/depleted prep items and manual requests, and subtracts "
        "current kitchen inventory."
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

    with plan_repo.lock:
        plan = require_plan(week_id)
        if plan.status == "archived":
            raise ValueError("archived plans cannot be edited")
        current = build_current_shopping(
            plan=plan,
            dishes=dish_repo.load_strict(),
            prep_items=prep_repo.load_strict(),
            catalog_items=fridge_repo.load_catalog_items(),
            manual_requests=shopping_request_repo.load(week=week_id),
        )
        shopping = persistable_shopping(current)
        validate_shopping_snapshot(shopping)
        plan.shopping = shopping
        plan_repo.save(plan)

    return shopping
