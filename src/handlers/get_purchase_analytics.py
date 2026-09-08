"""Tool: get_purchase_analytics — derive spend, store, and price history."""

from ..receipt_analytics import build_purchase_analytics
from ..receipt_commands import load_receipts
from ._common import reject_unknown_args, tool_handler

NAME = "get_purchase_analytics"
SCHEMA = {
    "description": (
        "Derive purchase spend, trips by store/day/week/month, raw purchase lines, "
        "price history, discounts, reconciliation gaps, and normalization coverage. "
        "needs_review and retracted receipts are excluded by default."
    ),
    "type": "object",
    "properties": {
        "from_date": {"type": "string", "format": "date"},
        "to_date": {"type": "string", "format": "date"},
        "merchant_name": {
            "type": "string", "minLength": 1, "maxLength": 500,
            "pattern": r".*\S.*",
        },
        "include_needs_review": {"type": "boolean"},
        "currency": {"type": "string", "pattern": "^[A-Za-z]{3}$"},
    },
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    for name in ("from_date", "to_date", "merchant_name", "currency"):
        if name in args and args[name] is None:
            raise ValueError(f"{name} cannot be null; omit it to disable the filter")
    return build_purchase_analytics(
        load_receipts(),
        from_date=args.get("from_date"),
        to_date=args.get("to_date"),
        merchant_name=args.get("merchant_name"),
        include_needs_review=args.get("include_needs_review", False),
        currency=args.get("currency"),
    )
