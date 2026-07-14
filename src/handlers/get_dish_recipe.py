"""Tool: get_dish_recipe — return one complete recipe."""

from ..repositories import dish_repo
from ._common import normalize_dish_name, require_arg, tool_handler

NAME = "get_dish_recipe"

SCHEMA = {
    "description": (
        "Return one complete recipe, including ingredients and optional cooking "
        "instructions. Use before editing a recipe or when the user asks how to cook it."
    ),
    "type": "object",
    "properties": {
        "dish_name": {
            "type": "string",
            "description": "exact dish name to read",
        },
    },
    "required": ["dish_name"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    raw_name = require_arg(args, "dish_name")
    name = normalize_dish_name(raw_name)
    dish = next((item for item in dish_repo.load() if item.name == name), None)
    if dish is None:
        raise LookupError(f"'{raw_name}' not found in the catalog.")
    return dish.to_dict() | {"instructions": dish.instructions}
