"""Tool: list_purchase_receipts — query receipt summaries."""

from datetime import date

from ..receipt_commands import load_receipts
from ._common import reject_unknown_args, tool_handler

NAME = "list_purchase_receipts"
SCHEMA = {
    "description": (
        "List canonical purchase-receipt summaries newest first. Retractions remain "
        "available when include_retracted=true."
    ),
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["needs_review", "confirmed", "corrected", "retracted"],
        },
        "merchant_name": {
            "type": "string", "minLength": 1, "maxLength": 500,
            "pattern": r".*\S.*",
        },
        "from_date": {"type": "string", "format": "date"},
        "to_date": {"type": "string", "format": "date"},
        "include_retracted": {"type": "boolean"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
    },
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    include_retracted = args.get("include_retracted", False)
    if not isinstance(include_retracted, bool):
        raise ValueError("include_retracted must be boolean")
    limit = args.get("limit", 100)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer from 1 to 1000")
    status = args.get("status")
    if "status" in args and status is None:
        raise ValueError("status cannot be null; omit it to disable the filter")
    if status is not None and status not in {
        "needs_review", "confirmed", "corrected", "retracted",
    }:
        raise ValueError("status is invalid")
    merchant = args.get("merchant_name")
    if "merchant_name" in args and merchant is None:
        raise ValueError("merchant_name cannot be null; omit it to disable the filter")
    if merchant is not None:
        if (
            not isinstance(merchant, str)
            or not merchant.strip()
            or len(merchant) > 500
        ):
            raise ValueError("merchant_name must be a non-empty string up to 500 chars")
        merchant = " ".join(merchant.casefold().split())
    start = date.fromisoformat(args["from_date"]).isoformat() if "from_date" in args else None
    end = date.fromisoformat(args["to_date"]).isoformat() if "to_date" in args else None
    if start is not None and end is not None and start > end:
        raise ValueError("from_date cannot be after to_date")

    result = []
    receipts = sorted(
        load_receipts(),
        key=lambda receipt: (
            receipt.current.purchased_on or "",
            receipt.current.recorded_at,
            receipt.receipt_id,
        ),
        reverse=True,
    )
    for receipt in receipts:
        current = receipt.current
        if current.status == "retracted" and not include_retracted:
            continue
        if status is not None and current.status != status:
            continue
        if merchant is not None and current.merchant_name_normalized != merchant:
            continue
        purchased_on = current.purchased_on
        if start is not None and (purchased_on is None or purchased_on < start):
            continue
        if end is not None and (purchased_on is None or purchased_on > end):
            continue
        result.append(receipt.summary())
        if len(result) >= limit:
            break
    return result
