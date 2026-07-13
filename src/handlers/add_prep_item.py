"""Tool: add_prep_item — define a semi-finished product (prep item).

Creates a prep item in the catalog (data/prep_items.json) with its own
ingredients, yield, and storage zone. Does NOT consume ingredients yet —
that happens when ``make_prep`` is called.
"""

from ..prep_item import PrepItem
from ..repositories import prep_repo
from ._common import (
    normalize_dish_name,
    normalize_ingredients,
    require_arg,
    tool_handler,
)

NAME = "add_prep_item"

SCHEMA = {
    "description": (
        "Define a semi-finished product (prep item) in the catalog. "
        "A prep item has its own ingredients, a yield quantity, and a "
        "storage zone (fridge/freezer/pantry). Use when planning prep-day "
        "items like hybrid meatballs, sauces, or marinated portions. "
        "This only creates the definition — use make_prep to actually "
        "produce it and consume source ingredients from the fridge."
    ),
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "name of the prep item, e.g. 'hybrid-meatballs'",
        },
        "ingredients": {
            "oneOf": [
                {
                    "type": "object",
                    "additionalProperties": {"type": "boolean"},
                    "description": "ingredient name -> true (essential) or false (optional)",
                },
                {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "list of ingredient names (all essential)",
                },
            ],
            "description": "Source ingredients needed to make this prep item.",
        },
        "yield_qty": {
            "type": "integer",
            "description": "how many units this recipe produces (e.g. 40 meatballs)",
            "default": 0,
        },
        "yield_unit": {
            "type": "string",
            "description": "unit for the yield (e.g. 'шт', 'порц', 'г')",
            "default": "шт",
        },
        "storage": {
            "type": "string",
            "enum": ["fridge", "freezer", "pantry"],
            "description": "where the finished prep item is stored",
            "default": "freezer",
        },
    },
    "required": ["name", "ingredients"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    name = normalize_dish_name(require_arg(args, "name"))
    ingredients = normalize_ingredients(require_arg(args, "ingredients"))
    yield_qty = args.get("yield_qty", 0)
    yield_unit = args.get("yield_unit", "шт")
    storage = args.get("storage", "freezer")

    if not isinstance(yield_qty, int) or isinstance(yield_qty, bool):
        raise ValueError("yield_qty must be an integer")

    with prep_repo.lock:
        items = prep_repo.load()
        if any(it.name == name for it in items):
            raise ValueError(f"a prep item called '{name}' already exists")

        new_item = PrepItem(
            name=name,
            yield_qty=yield_qty,
            yield_unit=yield_unit,
            storage=storage,
            remaining=0,
        )
        for ing, essential in ingredients.items():
            new_item.ingredients[ing] = essential
        items.append(new_item)
        prep_repo.save(items)

    ess_count = sum(1 for v in new_item.ingredients.values() if v)
    opt_count = len(new_item.ingredients) - ess_count
    return (
        f"Added prep item '{name}' ({ess_count} essential, {opt_count} optional). "
        f"Yield: {yield_qty} {yield_unit}, storage: {storage}. "
        f"Remaining: 0. Use make_prep to produce it."
    )
