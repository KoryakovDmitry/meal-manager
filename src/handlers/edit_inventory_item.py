"""Tool: edit_inventory_item — patch one structured inventory record."""

from ..repositories import fridge_repo
from ._common import reject_unknown_args, require_arg, tool_handler

NAME = "edit_inventory_item"
_NULLABLE_STRING = {"oneOf": [{"type": "string"}, {"type": "null"}]}
SCHEMA = {
    "description": "Edit fields of one structured kitchen-inventory item by stable item_id. Pass null to clear nullable metadata.",
    "type": "object",
    "properties": {
        "item_id": {"type": "string", "maxLength": 100},
        "name": {"type": "string", "maxLength": 200},
        "quantity": {"oneOf": [{"type": "number"}, {"type": "string"}, {"type": "null"}]},
        "unit": {"oneOf": [{"type": "string", "enum": ["g", "kg", "ml", "l", "pcs", "pack", "can", "jar", "bottle", "portion"]}, {"type": "null"}]},
        "package_count": {"oneOf": [{"type": "integer", "minimum": 1, "maximum": 10000}, {"type": "null"}]},
        "storage": {"oneOf": [{"type": "string", "enum": ["fridge", "freezer", "pantry", "counter"]}, {"type": "null"}]},
        "expires_on": _NULLABLE_STRING,
        "comment": {"oneOf": [{"type": "string", "maxLength": 1000}, {"type": "null"}]},
    },
    "required": ["item_id"],
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    item_id = require_arg(args, "item_id")
    patch = {key: args[key] for key in (
        "name", "quantity", "unit", "package_count", "storage", "expires_on", "comment"
    ) if key in args}
    return fridge_repo.edit_item(item_id, patch).to_public_dict()
