"""Tool: replenish_product — return a catalog product to current stock."""

from ..repositories import fridge_repo
from ._common import normalize_ingredient_name, reject_unknown_args, tool_handler

NAME = "replenish_product"
SCHEMA = {
    "description": (
        "Mark one out-of-stock or recipe-only product as present in kitchen inventory. "
        "Use product_id for a previously stocked product or exact name for recipe-only."
    ),
    "type": "object",
    "properties": {
        "product_id": {"type": "string", "maxLength": 100},
        "name": {"type": "string", "maxLength": 200},
        "category": {"type": "string", "enum": ["product", "prep", "ready_meal"]},
        "quantity": {"description": "positive decimal amount or null", "oneOf": [{"type": "number"}, {"type": "string"}, {"type": "null"}]},
        "unit": {"oneOf": [{"type": "string", "enum": ["g", "kg", "ml", "l", "pcs", "pack", "can", "jar", "bottle", "portion"]}, {"type": "null"}]},
        "package_count": {"oneOf": [{"type": "integer", "minimum": 1, "maximum": 10000}, {"type": "null"}]},
        "storage": {"oneOf": [{"type": "string", "enum": ["fridge", "freezer", "pantry", "counter"]}, {"type": "null"}]},
        "expires_on": {"oneOf": [{"type": "string", "format": "date"}, {"type": "null"}]},
        "comment": {"oneOf": [{"type": "string", "maxLength": 1000}, {"type": "null"}]},
    },
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    product_id = args.get("product_id")
    name = args.get("name")
    if bool(product_id) == bool(name):
        raise ValueError("Provide exactly one of product_id or name")
    if name is not None:
        name = normalize_ingredient_name(name)
    fields = {
        key: args[key]
        for key in (
            "quantity", "unit", "package_count", "storage", "expires_on", "comment", "category"
        )
        if key in args
    }
    item = fridge_repo.replenish_item(
        item_id=product_id,
        name=name,
        **fields,
    )
    return item.to_public_dict()
