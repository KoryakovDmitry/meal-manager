"""Tool: make_prep — produce a prep item, consuming source ingredients.

This is the prep-day action: take ingredients from the fridge, produce the
prep item, and store it. Essential ingredients of the prep item are removed
from the fridge. The prep item's ``remaining`` is set to ``yield_qty``.
"""

import logging

from ..repositories import fridge_repo, prep_repo
from ._common import (
    normalize_dish_name,
    require_arg,
    tool_handler,
)

logger = logging.getLogger(__name__)

NAME = "make_prep"

SCHEMA = {
    "description": (
        "Produce a prep item from its source ingredients. This is the "
        "prep-day action: essential ingredients are consumed from the "
        "fridge, and the prep item is stored with its full yield. "
        "Use during weekend prep to make meatballs, sauces, etc."
    ),
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "name of the prep item to produce",
        },
    },
    "required": ["name"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    name = normalize_dish_name(require_arg(args, "name"))

    with prep_repo.lock:
        items = prep_repo.load()
        item = next((it for it in items if it.name == name), None)
        if item is None:
            raise LookupError(f"prep item '{name}' is not defined")

        essentials = [
            ing for ing, is_essential in item.ingredients.items() if is_essential
        ]

        # Consume essential ingredients from the fridge
        removed = []
        with fridge_repo.lock:
            available = fridge_repo.load_set()
            removed = [ing for ing in essentials if ing in available]
            missing = [ing for ing in essentials if ing not in available]

            if missing:
                raise ValueError(
                    f"Cannot make '{name}': missing essential ingredients: "
                    f"{', '.join(missing)}"
                )

            fridge_repo.remove_items(removed)

        # Update remaining quantity
        item.remaining = item.yield_qty
        prep_repo.save(items)

    removed_msg = f" Consumed from fridge: {', '.join(removed)}." if removed else ""
    return (
        f"Made '{name}': {item.yield_qty} {item.yield_unit} ready "
        f"in {item.storage}.{removed_msg}"
    )
