"""Tool: retract_purchase_receipt — exclude a receipt without deleting evidence."""

from ..receipt_commands import retract_receipt
from ._common import reject_unknown_args, require_arg, tool_handler

NAME = "retract_purchase_receipt"
SCHEMA = {
    "description": (
        "Append a retraction revision. The receipt and all original evidence remain "
        "queryable, while default purchase analytics exclude it."
    ),
    "type": "object",
    "properties": {
        "receipt_id": {"type": "string", "pattern": "^receipt_[0-9a-f]{32}$"},
        "expected_revision": {"type": "integer", "minimum": 1},
        "reason": {
            "type": "string", "minLength": 1, "maxLength": 2000,
            "pattern": r".*\S.*",
        },
    },
    "required": ["receipt_id", "expected_revision", "reason"],
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    return retract_receipt(
        require_arg(args, "receipt_id"),
        expected_revision=require_arg(args, "expected_revision"),
        reason=require_arg(args, "reason"),
    )
