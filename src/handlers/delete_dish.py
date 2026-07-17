"""Tool: delete_dish — remove a recipe from the catalog."""

from ..repositories import dish_repo
from ._common import normalize_dish_name, require_arg, tool_handler

NAME = "delete_dish"

SCHEMA = {
    "description": (
        "Remove a recipe from the catalog. Use when the user wants to "
        "delete a dish they no longer cook or that was added by mistake."
    ),
    "type": "object",
    "properties": {
        "dish_name": {
            "type": "string",
            "description": "exact dish name to delete from catalog",
        },
    },
    "required": ["dish_name"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    raw_name = require_arg(args, "dish_name")
    name = normalize_dish_name(raw_name)

    with dish_repo.lock:
        dishes = dish_repo.load()
        if not any(d.name == name for d in dishes):
            raise LookupError(f"'{raw_name}' not found in the catalog.")
        remaining = [d for d in dishes if d.name != name]
        dish_repo.save(remaining)

    return f"Deleted '{name}' from the catalog."
