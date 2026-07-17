"""Tool: delete_history_entry — append a correction for one cook occurrence."""

from ..cooking import retract_cooked
from ..repositories import history_repo
from ._common import normalize_dish_name, tool_handler

NAME = "delete_history_entry"

SCHEMA = {
    "description": (
        "Retract one cooking occurrence by stable event_id. dish_name remains a "
        "legacy selector for the latest active occurrence. The original row is retained."
    ),
    "type": "object",
    "properties": {
        "dish_name": {
            "type": "string",
            "description": "legacy exact dish-name selector",
        },
        "event_id": {
            "type": "string",
            "description": "stable cook event ID from list_cooking_history",
        },
    },
    "oneOf": [
        {"required": ["event_id"], "not": {"required": ["dish_name"]}},
        {"required": ["dish_name"], "not": {"required": ["event_id"]}},
    ],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    event_id = args.get("event_id")
    raw_name = args.get("dish_name")
    if (event_id is None) == (raw_name is None):
        raise ValueError("provide exactly one of event_id or dish_name")
    if event_id is not None:
        if not isinstance(event_id, str) or not event_id.startswith("cook_"):
            raise ValueError("event_id must start with cook_")
        event = next(
            (item for item in history_repo.load_events(strict=True) if item.id == event_id),
            None,
        )
        if event is None:
            raise LookupError(f"cooking event '{event_id}' not found")
        name = event.dish_name_snapshot
    else:
        if not isinstance(raw_name, str):
            raise ValueError("dish_name must be a string")
        name = normalize_dish_name(raw_name)
        candidates = [
            event
            for event in history_repo.load_events(strict=True)
            if event.dish_name_snapshot == name and event.active
        ]
        if not candidates:
            raise LookupError(f"'{raw_name}' not found in active cooking history.")
        event = candidates[-1]
    result = retract_cooked(event_id=event.id)
    plan_msg = " Linked plan occurrence reopened." if result["plan_reopened"] else ""
    return f"Retracted cooking event '{event.id}' for '{name}'.{plan_msg}"
