"""Tool: delete_prep_item — remove a prep item definition from the catalog."""

from ..repositories import prep_repo
from ._common import (
    normalize_dish_name,
    require_arg,
    tool_handler,
)

NAME = "delete_prep_item"

SCHEMA = {
    "description": (
        "Remove a prep item definition from the catalog. Use when a prep "
        "item is no longer needed or was created by mistake."
    ),
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "name of the prep item to remove",
        },
    },
    "required": ["name"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    name = normalize_dish_name(require_arg(args, "name"))

    with prep_repo.lock:
        items = prep_repo.load()
        new_items = [it for it in items if it.name != name]
        if len(new_items) == len(items):
            raise LookupError(f"prep item '{name}' not found")
        prep_repo.save(new_items)

    return f"Removed prep item '{name}'."
