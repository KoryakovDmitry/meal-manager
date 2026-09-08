"""Tool: correct_purchase_receipt — append a corrected receipt revision."""

from ..receipt_commands import correct_receipt
from ._common import maybe_parse_json_arg, reject_unknown_args, require_arg, tool_handler
from ._receipt_common import (
    CORRECTION_RECEIPT_PROPERTIES,
    EVIDENCE_PATCH_SCHEMA,
    coerce_receipt_payload,
)

NAME = "correct_purchase_receipt"
SCHEMA = {
    "description": (
        "Append a corrected revision of a purchase receipt without overwriting its "
        "original transcription. changes is a top-level patch; replacing lines requires "
        "the full ordered line array. expected_revision provides optimistic concurrency."
    ),
    "type": "object",
    "properties": {
        "receipt_id": {"type": "string", "pattern": "^receipt_[0-9a-f]{32}$"},
        "expected_revision": {"type": "integer", "minimum": 1},
        "reason": {
            "type": "string", "minLength": 1, "maxLength": 2000,
            "pattern": r".*\S.*",
        },
        "changes": {
            "type": "object",
            "properties": {
                **CORRECTION_RECEIPT_PROPERTIES,
                "evidence": EVIDENCE_PATCH_SCHEMA,
            },
            "minProperties": 1,
            "additionalProperties": False,
        },
    },
    "required": ["receipt_id", "expected_revision", "reason", "changes"],
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    changes = maybe_parse_json_arg(require_arg(args, "changes"))
    changes = coerce_receipt_payload(changes)
    return correct_receipt(
        require_arg(args, "receipt_id"),
        expected_revision=require_arg(args, "expected_revision"),
        reason=require_arg(args, "reason"),
        changes=changes,
    )
