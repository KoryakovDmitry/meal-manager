"""Tool: get_purchase_receipt — read one canonical receipt."""

from ..receipt_commands import load_receipts
from ._common import reject_unknown_args, require_arg, tool_handler

NAME = "get_purchase_receipt"
SCHEMA = {
    "description": (
        "Return one canonical purchase receipt with ordered raw lines, exact cents, "
        "evidence metadata, and optionally every append-preserved revision."
    ),
    "type": "object",
    "properties": {
        "receipt_id": {"type": "string", "pattern": "^receipt_[0-9a-f]{32}$"},
        "include_revisions": {"type": "boolean"},
    },
    "required": ["receipt_id"],
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    receipt_id = require_arg(args, "receipt_id")
    include_revisions = args.get("include_revisions", False)
    if not isinstance(include_revisions, bool):
        raise ValueError("include_revisions must be boolean")
    matches = [
        receipt for receipt in load_receipts()
        if receipt.receipt_id == receipt_id
    ]
    if len(matches) != 1:
        raise LookupError(f"purchase receipt '{receipt_id}' not found uniquely")
    return matches[0].to_public(include_revisions=include_revisions)
