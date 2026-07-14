"""Tool: set_dish_instructions — set or clear cooking instructions."""

from ..dish import Dish
from ..repositories import dish_repo
from ._common import normalize_dish_name, require_arg, tool_handler

NAME = "set_dish_instructions"

SCHEMA = {
    "description": (
        "Set, replace, or clear the 'how to cook' instructions for one recipe. "
        "Pass null or a blank string to remove the instructions."
    ),
    "type": "object",
    "properties": {
        "dish_name": {
            "type": "string",
            "description": "exact dish name to update",
        },
        "instructions": {
            "type": ["string", "null"],
            "description": (
                "new cooking instructions (maximum 20,000 characters after trimming), "
                "or null/blank to clear them"
            ),
        },
    },
    "required": ["dish_name", "instructions"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    raw_name = require_arg(args, "dish_name")
    if "instructions" not in args:
        raise ValueError("Missing required argument: 'instructions'")
    name = normalize_dish_name(raw_name)

    with dish_repo.lock:
        dishes = dish_repo.load()
        dish = next((item for item in dishes if item.name == name), None)
        if dish is None:
            raise LookupError(f"'{raw_name}' not found in the catalog.")
        candidate = Dish(
            name=dish.name,
            ingredients=dish.ingredients,
            prep_depends=dish.prep_depends,
            instructions=args["instructions"],
        )
        dish.instructions = candidate.instructions
        dish_repo.save(dishes)

    return {
        "dish_name": dish.name,
        "instructions": dish.instructions,
        "cleared": dish.instructions is None,
    }
