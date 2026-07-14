"""Tool: remove_inventory_item — remove one structured record by stable id."""

from ..repositories import fridge_repo
from ._common import reject_unknown_args, require_arg, tool_handler

NAME = "remove_inventory_item"
SCHEMA = {
    "description": "Remove exactly one structured kitchen-inventory item by stable item_id.",
    "type": "object",
    "properties": {"item_id": {"type": "string", "maxLength": 100}},
    "required": ["item_id"],
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    return fridge_repo.remove_item(require_arg(args, "item_id")).to_public_dict()
