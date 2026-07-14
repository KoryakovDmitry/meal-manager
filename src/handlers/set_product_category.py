"""Tool: set_product_category — classify any visible catalog product."""

from ..product_catalog import build_product_catalog
from ..repositories import dish_repo, fridge_repo
from ._common import normalize_ingredient_name, reject_unknown_args, require_arg, tool_handler

NAME = "set_product_category"
SCHEMA = {
    "description": (
        "Set one product category without changing whether it is in stock. "
        "Works for current, out-of-stock, and recipe-only catalog products."
    ),
    "type": "object",
    "properties": {
        "name": {"type": "string", "maxLength": 200},
        "category": {
            "type": "string",
            "enum": ["product", "prep", "ready_meal"],
        },
    },
    "required": ["name", "category"],
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    name = normalize_ingredient_name(require_arg(args, "name"))
    category = require_arg(args, "category")
    rows = build_product_catalog(
        fridge_repo.load_catalog_items(),
        dish_repo.load(),
    )
    current = next((row for row in rows if row["name"] == name), None)
    if current is None:
        raise LookupError(f"Product '{name}' not found in product catalog")
    item = fridge_repo.set_product_category(
        name,
        category,
        allow_create=current["id"] is None,
    )
    refreshed = build_product_catalog(
        fridge_repo.load_catalog_items(),
        dish_repo.load(),
    )
    updated = next((row for row in refreshed if row["name"] == name), None)
    if updated is not None:
        return updated
    return item.to_public_dict() | {
        "status": current["status"],
        "recipe_count": 0,
        "in_recipes": False,
    }
