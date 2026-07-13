"""Tool: split_shopping_list — group priced items into shopping trips."""

from ..plan_shopping import split_shopping_trips, validate_shopping_snapshot
from ..repositories import plan_repo
from ._common import require_arg, tool_handler
from ._plan_common import normalize_week_id, require_plan

NAME = "split_shopping_list"
SCHEMA = {
    "description": (
        "After estimate_plan_cost, split priced shopping items into deterministic "
        "soft cost-limited trips. Unpriced items remain explicit and oversized "
        "single items warn, not block."
    ),
    "type": "object",
    "properties": {
        "week": {"type": "string", "description": "ISO week YYYY-Www"},
        "trip_limit": {
            "type": "number",
            "exclusiveMinimum": 0,
            "default": 100.0,
        },
    },
    "required": ["week"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    week_id = normalize_week_id(require_arg(args, "week"))
    trip_limit = args.get("trip_limit", 100.0)

    with plan_repo.lock:
        plan = require_plan(week_id)
        if plan.status == "archived":
            raise ValueError("archived plans cannot be edited")
        if not isinstance(plan.shopping, dict) or not isinstance(
            plan.shopping.get("items"), list
        ):
            raise ValueError("generate the shopping list before splitting trips")
        if "complete" not in plan.shopping:
            raise ValueError("estimate plan cost before splitting shopping trips")
        split = split_shopping_trips(plan.shopping, trip_limit=trip_limit)
        plan.shopping["trips"] = split["trips"]
        plan.shopping["trip_limit"] = split["trip_limit"]
        plan.shopping["trip_warnings"] = split["warnings"]
        plan.shopping["unpriced_trip_items"] = split["unpriced_items"]
        validate_shopping_snapshot(plan.shopping)
        plan_repo.save(plan)

    return split
