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

    with fridge_repo.lock:
        fridge = fridge_repo.load()
        if old_name not in fridge:
            raise LookupError(f"Ingredient '{old_name}' not found in kitchen inventory")
        if old_name == new_name:
            return f"No changes — '{old_name}' already has that name."
        if new_name in fridge:
            raise ValueError(f"Ingredient '{new_name}' already exists in kitchen inventory")

        renamed = [new_name if item == old_name else item for item in fridge]
        fridge_repo.save(renamed)

    return f"Successfully renamed '{old_name}' to '{new_name}' in kitchen inventory."
