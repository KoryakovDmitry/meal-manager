"""Tool: list_prep_items — show all defined prep items with remaining quantities."""

from ..repositories import prep_repo
from ._common import tool_handler

NAME = "list_prep_items"

SCHEMA = {
    "description": (
        "List all prep items (semi-finished products) with their remaining "
        "quantities and storage zones. Use when checking what prep items "
        "are available before planning meals or during prep-day."
    ),
    "type": "object",
    "properties": {},
    "required": [],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    items = prep_repo.load()
    if not items:
        return {"prep_items": [], "message": "No prep items defined yet."}

    result = []
    for item in items:
        result.append({
            "name": item.name,
            "ingredients": item.ingredients,
            "yield": item.yield_qty,
            "yield_unit": item.yield_unit,
            "storage": item.storage,
            "remaining": item.remaining,
        })

    return {"prep_items": result}
