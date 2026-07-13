"""Tool: get_week_plan — return one weekly plan."""

from ._common import tool_handler
from ._plan_common import normalize_week_id, require_plan

NAME = "get_week_plan"

SCHEMA = {
    "description": (
        "Return the meal plan for an ISO week. Defaults to the current week."
    ),
    "type": "object",
    "properties": {
        "week": {
            "type": "string",
            "description": "ISO week YYYY-Www; defaults to the current week",
        },
    },
    "required": [],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    week_id = normalize_week_id(args.get("week"), default_current=True)
    plan = require_plan(week_id)
    return plan.to_dict()
