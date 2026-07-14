"""Tool: add_inventory_item — add one structured kitchen inventory record."""

from ..repositories import fridge_repo
from ._common import reject_unknown_args, require_arg, tool_handler

NAME = "add_inventory_item"
SCHEMA = {
    "description": "Add one structured kitchen-inventory item with optional quantity, packages, storage, expiry, and comment.",
    "type": "object",
    "properties": {
        "name": {"type": "string", "maxLength": 200},
        "category": {"type": "string", "enum": ["product", "prep", "ready_meal"]},
        "quantity": {"description": "positive decimal amount or null", "oneOf": [{"type": "number"}, {"type": "string"}, {"type": "null"}]},
        "unit": {"oneOf": [{"type": "string", "enum": ["g", "kg", "ml", "l", "pcs", "pack", "can", "jar", "bottle", "portion"]}, {"type": "null"}]},
        "package_count": {"oneOf": [{"type": "integer", "minimum": 1, "maximum": 10000}, {"type": "null"}]},
        "storage": {"oneOf": [{"type": "string", "enum": ["fridge", "freezer", "pantry", "counter"]}, {"type": "null"}]},
        "expires_on": {"oneOf": [{"type": "string", "format": "date"}, {"type": "null"}]},
        "comment": {"oneOf": [{"type": "string", "maxLength": 1000}, {"type": "null"}]},
    },
    "required": ["name"],
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    fields = {key: args[key] for key in (
        "quantity", "unit", "package_count", "storage", "expires_on", "comment", "category"
    ) if key in args}
    item = fridge_repo.add_item(name=require_arg(args, "name"), **fields)
    return item.to_public_dict()
