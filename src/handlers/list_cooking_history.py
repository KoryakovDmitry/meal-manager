"""Tool: list_cooking_history — return canonical cooking occurrences."""

from ..repositories import history_repo
from ._common import tool_handler

NAME = "list_cooking_history"

SCHEMA = {
    "description": (
        "List canonical cooking occurrences, including retracted corrections, "
        "stable IDs, plan links, actual dates, and actual yield."
    ),
    "type": "object",
    "properties": {
        "include_retracted": {
            "type": "boolean",
            "description": "include corrected/retracted occurrences; defaults true",
        },
        "dish_name": {
            "type": "string",
            "description": "optional exact normalized dish-name filter",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "description": "maximum newest occurrences; defaults 100",
        },
    },
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    include_retracted = args.get("include_retracted", True)
    dish_name = args.get("dish_name")
    limit = args.get("limit", 100)
    if not isinstance(include_retracted, bool):
        raise ValueError("include_retracted must be boolean")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer from 1 to 1000")
    if dish_name is not None:
        if not isinstance(dish_name, str) or not dish_name.strip():
            raise ValueError("dish_name must be a non-empty string")
        dish_name = dish_name.strip().lower()

    events = history_repo.load_events(strict=True)
    result = []
    for event in reversed(events):
        if not include_retracted and not event.active:
            continue
        if dish_name is not None and event.dish_name_snapshot != dish_name:
            continue
        item = event.to_dict()
        item["status"] = "active" if event.active else "retracted"
        result.append(item)
        if len(result) >= limit:
            break
    return result
