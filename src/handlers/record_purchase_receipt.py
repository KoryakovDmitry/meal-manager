"""Tool: record_purchase_receipt — persist one standalone purchase receipt."""

from ..receipt_commands import record_receipt
from ._common import reject_unknown_args, require_arg, tool_handler
from ._receipt_common import RECEIPT_PROPERTIES, coerce_receipt_payload

NAME = "record_purchase_receipt"
SCHEMA = {
    "description": (
        "Persist a photographed, PDF, text, or manually transcribed purchase receipt "
        "as an append-preserving expense-ledger entity. This never mutates inventory, "
        "plans, or shopping requests. Monetary fields are integer minor units. Redact "
        "payment-card, loyalty, and QR identifiers before recording evidence metadata."
    ),
    "type": "object",
    "properties": {
        **RECEIPT_PROPERTIES,
        "status": {"type": "string", "enum": ["confirmed", "needs_review"]},
        "allow_similar": {
            "type": "boolean",
            "description": "explicitly allow a new receipt matching merchant/date/total",
        },
    },
    "required": [
        "merchant_name_raw", "purchased_at", "time_precision", "currency", "lines", "evidence",
    ],
    "allOf": [
        {
            "if": {
                "properties": {"time_precision": {"const": "date"}},
                "required": ["time_precision"],
            },
            "then": {
                "properties": {
                    "purchased_at": {
                        "type": "string",
                        "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$",
                        "format": "date",
                    }
                }
            },
        },
        {
            "if": {
                "properties": {"time_precision": {"const": "datetime"}},
                "required": ["time_precision"],
            },
            "then": {
                "properties": {
                    "purchased_at": {
                        "type": "string",
                        "pattern": (
                            "^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:"
                            "[0-9]{2}:[0-9]{2}(?:\\.[0-9]+)?"
                            "(?:Z|[+-][0-9]{2}:[0-9]{2})$"
                        ),
                        "format": "date-time",
                    }
                }
            },
        },
        {
            "if": {
                "properties": {"time_precision": {"const": "unknown"}},
                "required": ["time_precision"],
            },
            "then": {"properties": {"purchased_at": {"type": "null"}}},
        },
    ],
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    for name in SCHEMA["required"]:
        require_arg(args, name)
    payload = coerce_receipt_payload({
        key: value for key, value in args.items()
        if key in RECEIPT_PROPERTIES
    })
    allow_similar = args.get("allow_similar", False)
    if not isinstance(allow_similar, bool):
        raise ValueError("allow_similar must be boolean")
    return record_receipt(
        payload,
        status=args.get("status", "confirmed"),
        allow_similar=allow_similar,
    )
