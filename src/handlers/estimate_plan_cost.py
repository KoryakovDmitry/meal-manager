"""Tool: estimate_plan_cost — attach optional unit-price estimates."""

from ..plan_shopping import estimate_shopping_cost, validate_shopping_snapshot
from ..repositories import plan_repo
from ._common import maybe_parse_json_arg, require_arg, tool_handler
from ._plan_common import normalize_week_id, require_plan

NAME = "estimate_plan_cost"
SCHEMA = {
    "description": (
        "Estimate weekly shopping cost from an ingredient-to-EUR price map. "
        "The €150 weekly limit is informational: incomplete prices return "
        "status unknown, and over-budget estimates warn but never block."
    ),
    "type": "object",
    "properties": {
        "week": {"type": "string", "description": "ISO week YYYY-Www"},
        "prices": {
            "type": "object",
            "additionalProperties": {"type": "number", "exclusiveMinimum": 0},
            "description": "ingredient name to EUR price per shopping unit",
        },
        "weekly_limit": {
            "type": "number",
            "exclusiveMinimum": 0,
            "default": 150.0,
        },
    },
    "required": ["week", "prices"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    week_id = normalize_week_id(require_arg(args, "week"))
    prices = maybe_parse_json_arg(require_arg(args, "prices"))
    weekly_limit = args.get("weekly_limit", 150.0)

    with plan_repo.lock:
        plan = require_plan(week_id)
        if plan.status == "archived":
            raise ValueError("archived plans cannot be edited")
        if not isinstance(plan.shopping, dict) or not isinstance(
            plan.shopping.get("items"), list
        ):
            raise ValueError("generate the shopping list before estimating cost")
        estimate = estimate_shopping_cost(
            plan.shopping,
            prices,
            weekly_limit=weekly_limit,
        )
        plan.shopping.update(estimate)
        plan.shopping.pop("trips", None)
        plan.shopping.pop("trip_limit", None)
        plan.shopping.pop("trip_warnings", None)
        plan.shopping.pop("unpriced_trip_items", None)
        validate_shopping_snapshot(plan.shopping)
        plan_repo.save(plan)
        result = dict(plan.shopping)

    return result
