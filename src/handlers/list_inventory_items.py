"""Tool: list_inventory_items — return structured kitchen inventory records."""

from ..repositories import fridge_repo
from ._common import reject_unknown_args, tool_handler

NAME = "list_inventory_items"
SCHEMA = {
    "description": "List complete structured kitchen-inventory items including quantity, storage, expiry status, and comments.",
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set())
    return [item.to_public_dict() for item in fridge_repo.load_items()]
