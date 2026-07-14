"""Tool: rename_fridge_item — atomically rename one inventory ingredient."""

from ..repositories import fridge_repo
from ._common import normalize_ingredient_name, require_arg, tool_handler

NAME = "rename_fridge_item"

SCHEMA = {
    "description": (
        "Rename one existing kitchen-inventory item atomically. Use to correct "
        "a product name, brand, pasta shape, or typo without remove-and-add."
    ),
    "type": "object",
    "properties": {
        "old_ingredient": {
            "type": "string",
            "maxLength": 200,
            "description": "current inventory item name",
        },
        "new_ingredient": {
            "type": "string",
            "maxLength": 200,
            "description": "replacement inventory item name",
        },
    },
    "required": ["old_ingredient", "new_ingredient"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    old_name = normalize_ingredient_name(require_arg(args, "old_ingredient"))
    new_name = normalize_ingredient_name(require_arg(args, "new_ingredient"))

    fridge_repo.rename_by_name(old_name, new_name)
    if old_name == new_name:
        return f"No changes — '{old_name}' already has that name."
    return f"Successfully renamed '{old_name}' to '{new_name}' in kitchen inventory."
