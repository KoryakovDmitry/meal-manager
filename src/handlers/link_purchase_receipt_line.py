"""Tool: link_purchase_receipt_line — add/remove analytical identity links."""

from ..receipt_commands import link_receipt_line
from ._common import reject_unknown_args, require_arg, tool_handler

NAME = "link_purchase_receipt_line"
SCHEMA = {
    "description": (
        "Append an analytical link/unlink revision for one receipt line. Links may point "
        "to inventory, product-catalog, or shopping identities but never mutate them."
    ),
    "type": "object",
    "properties": {
        "receipt_id": {"type": "string", "pattern": "^receipt_[0-9a-f]{32}$"},
        "receipt_line_id": {"type": "string", "pattern": "^rline_[0-9a-f]{32}$"},
        "expected_revision": {"type": "integer", "minimum": 1},
        "action": {"type": "string", "enum": ["link", "unlink"]},
        "inventory_item_id": {
            "type": "string", "minLength": 1, "maxLength": 200,
            "pattern": r".*\S.*",
        },
        "product_id": {
            "type": "string", "minLength": 1, "maxLength": 200,
            "pattern": r".*\S.*",
        },
        "shopping_occurrence_id": {
            "type": "string", "minLength": 1, "maxLength": 200,
            "pattern": r".*\S.*",
        },
    },
    "required": ["receipt_id", "receipt_line_id", "expected_revision", "action"],
    "oneOf": [
        {
            "required": ["inventory_item_id"],
            "not": {
                "anyOf": [
                    {"required": ["product_id"]},
                    {"required": ["shopping_occurrence_id"]},
                ]
            },
        },
        {
            "required": ["product_id"],
            "not": {
                "anyOf": [
                    {"required": ["inventory_item_id"]},
                    {"required": ["shopping_occurrence_id"]},
                ]
            },
        },
        {
            "required": ["shopping_occurrence_id"],
            "not": {
                "anyOf": [
                    {"required": ["inventory_item_id"]},
                    {"required": ["product_id"]},
                ]
            },
        },
    ],
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    for name in ("inventory_item_id", "product_id", "shopping_occurrence_id"):
        if name in args and args[name] is None:
            raise ValueError(f"{name} cannot be null; omit unused link identifiers")
    return link_receipt_line(
        require_arg(args, "receipt_id"),
        require_arg(args, "receipt_line_id"),
        expected_revision=require_arg(args, "expected_revision"),
        action=require_arg(args, "action"),
        inventory_item_id=args.get("inventory_item_id"),
        product_id=args.get("product_id"),
        shopping_occurrence_id=args.get("shopping_occurrence_id"),
    )
