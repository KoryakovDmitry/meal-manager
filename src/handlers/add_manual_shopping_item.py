"""Tool: add one persistent manual shopping request."""

from ..repositories import shopping_request_repo
from ._common import reject_unknown_args, require_arg, tool_handler
from ._plan_common import normalize_week_id

NAME = "add_manual_shopping_item"
SCHEMA = {
    "description": (
        "Add an abstract item to the current week's shopping list. "
        "This is a shopping request only and does not change kitchen inventory."
    ),
    "type": "object",
    "properties": {
        "ingredient": {"type": "string", "maxLength": 200},
        "week": {"type": "string", "description": "ISO week YYYY-Www; defaults to current week"},
    },
    "required": ["ingredient"],
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    ingredient = require_arg(args, "ingredient")
    week_id = normalize_week_id(args.get("week"), default_current=True)
    request = shopping_request_repo.add(
        week=week_id,
        requested_name=ingredient,
    )
    return request.to_dict() | {
        "status": "shopping_request_added",
        "inventory_changed": False,
    }
