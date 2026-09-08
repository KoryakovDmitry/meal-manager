"""Read-only purchase analytics derived from canonical receipt revisions."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from .receipt import PurchaseReceipt


def _date_bound(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO date or null")
    return date.fromisoformat(value).isoformat()


def _new_currency_bucket() -> dict[str, int]:
    return {
        "known_spend_cents": 0,
        "receipt_count": 0,
        "known_total_count": 0,
        "unknown_total_count": 0,
    }


def _add_currency_bucket(bucket: dict[str, int], spend: int | None) -> None:
    bucket["receipt_count"] += 1
    if spend is None:
        bucket["unknown_total_count"] += 1
    else:
        bucket["known_total_count"] += 1
        bucket["known_spend_cents"] += spend


def _public_currency_bucket(bucket: dict[str, int]) -> dict[str, Any]:
    complete = bucket["unknown_total_count"] == 0
    return {
        "spend_cents": bucket["known_spend_cents"] if complete else None,
        "known_spend_cents": bucket["known_spend_cents"],
        "receipt_count": bucket["receipt_count"],
        "known_total_count": bucket["known_total_count"],
        "unknown_total_count": bucket["unknown_total_count"],
        "complete": complete,
    }


def _new_adjustment_bucket() -> dict[str, int]:
    return {
        "signed_adjustments_cents": 0,
        "discount_savings_cents": 0,
        "return_credits_cents": 0,
        "deposit_and_fee_cents": 0,
    }


def _new_aggregate() -> dict[str, Any]:
    return {"receipt_count": 0, "by_currency": {}}


def _add_aggregate(row: dict[str, Any], currency: str, spend: int | None) -> None:
    row["receipt_count"] += 1
    bucket = row["by_currency"].setdefault(currency, _new_currency_bucket())
    _add_currency_bucket(bucket, spend)


def _public_aggregate(row: dict[str, Any]) -> dict[str, Any]:
    by_currency = {
        code: _public_currency_bucket(bucket)
        for code, bucket in sorted(row["by_currency"].items())
    }
    currencies = list(by_currency)
    complete = (
        len(currencies) == 1
        and by_currency[currencies[0]]["complete"]
    )
    return {
        "spend_cents": (
            by_currency[currencies[0]]["spend_cents"] if complete else None
        ),
        "currency": currencies[0] if len(currencies) == 1 else None,
        "receipt_count": row["receipt_count"],
        "known_total_count": sum(
            item["known_total_count"] for item in by_currency.values()
        ),
        "unknown_total_count": sum(
            item["unknown_total_count"] for item in by_currency.values()
        ),
        "complete": complete,
        "by_currency": by_currency,
    }


def build_purchase_analytics(
    receipts: list[PurchaseReceipt],
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    merchant_name: str | None = None,
    include_needs_review: bool = False,
    currency: str | None = None,
) -> dict[str, Any]:
    start = _date_bound(from_date, label="from_date")
    end = _date_bound(to_date, label="to_date")
    if start is not None and end is not None and start > end:
        raise ValueError("from_date cannot be after to_date")
    if merchant_name is not None:
        if (
            not isinstance(merchant_name, str)
            or not merchant_name.strip()
            or len(merchant_name) > 500
        ):
            raise ValueError("merchant_name must be a non-empty string up to 500 chars")
        merchant_filter = " ".join(merchant_name.casefold().split())
    else:
        merchant_filter = None
    if not isinstance(include_needs_review, bool):
        raise ValueError("include_needs_review must be boolean")
    if currency is not None:
        normalized_currency = currency if isinstance(currency, str) else ""
        if (
            len(normalized_currency) != 3
            or not normalized_currency.isascii()
            or not normalized_currency.isalpha()
        ):
            raise ValueError("currency must be a three-letter code")
        currency = normalized_currency.upper()

    included: list[PurchaseReceipt] = []
    excluded_by_status: dict[str, int] = defaultdict(int)
    for receipt in receipts:
        current = receipt.current
        allowed = {"confirmed", "corrected"}
        if include_needs_review:
            allowed.add("needs_review")
        if current.status not in allowed:
            excluded_by_status[current.status] += 1
            continue
        purchased_on = current.purchased_on
        if start is not None and (purchased_on is None or purchased_on < start):
            continue
        if end is not None and (purchased_on is None or purchased_on > end):
            continue
        if merchant_filter is not None and current.merchant_name_normalized != merchant_filter:
            continue
        if currency is not None and current.currency != currency:
            continue
        included.append(receipt)

    by_currency: dict[str, dict[str, int]] = defaultdict(_new_currency_bucket)
    by_merchant: dict[str, dict[str, Any]] = {}
    by_day: dict[str, dict[str, Any]] = defaultdict(_new_aggregate)
    by_week: dict[str, dict[str, Any]] = defaultdict(_new_aggregate)
    by_month: dict[str, dict[str, Any]] = defaultdict(_new_aggregate)
    purchases: list[dict[str, Any]] = []
    price_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reconciliation_gaps: list[dict[str, Any]] = []
    known_line_amounts = 0
    normalized_product_lines = 0
    product_lines = 0
    adjustments_by_currency: dict[str, dict[str, int]] = defaultdict(
        _new_adjustment_bucket
    )
    known_confidence_count = 0
    confidence_total = 0.0
    low_confidence_line_count = 0
    evidence_confidence: dict[str, int] = defaultdict(int)

    for receipt in included:
        current = receipt.current
        spend = current.total_cents
        evidence_confidence[current.evidence["confidence"]] += 1
        _add_currency_bucket(by_currency[current.currency], spend)
        adjustment_bucket = adjustments_by_currency[current.currency]

        merchant = by_merchant.setdefault(current.merchant_name_normalized, {
            "merchant_names_raw": [],
            "aggregate": _new_aggregate(),
        })
        if current.merchant_name_raw not in merchant["merchant_names_raw"]:
            merchant["merchant_names_raw"].append(current.merchant_name_raw)
        _add_aggregate(merchant["aggregate"], current.currency, spend)

        purchased_on = current.purchased_on
        if purchased_on is not None:
            parsed = date.fromisoformat(purchased_on)
            iso = parsed.isocalendar()
            period_rows = (
                by_day[purchased_on],
                by_week[f"{iso.year}-W{iso.week:02d}"],
                by_month[purchased_on[:7]],
            )
            for row in period_rows:
                _add_aggregate(row, current.currency, spend)

        if current.reconciliation_delta_cents not in (None, 0):
            reconciliation_gaps.append({
                "receipt_id": receipt.receipt_id,
                "merchant_name_raw": current.merchant_name_raw,
                "purchased_on": purchased_on,
                "currency": current.currency,
                "delta_cents": current.reconciliation_delta_cents,
            })

        for line in current.lines:
            if line.line_total_cents is not None:
                known_line_amounts += 1
            if line.confidence is not None:
                known_confidence_count += 1
                confidence_total += line.confidence
                if line.confidence < 0.8:
                    low_confidence_line_count += 1
            if line.kind == "product":
                product_lines += 1
                if line.normalized_label is not None:
                    normalized_product_lines += 1
                identity_kind = (
                    "normalized" if line.normalized_label is not None else "raw"
                )
                identity_key = line.normalized_label or line.description_raw
                history_key = f"{identity_kind}:{identity_key}"
                price_history[history_key].append({
                    "identity_kind": identity_kind,
                    "identity_key": identity_key,
                    "receipt_id": receipt.receipt_id,
                    "receipt_line_id": line.receipt_line_id,
                    "merchant_name_raw": current.merchant_name_raw,
                    "purchased_on": purchased_on,
                    "currency": current.currency,
                    "description_raw": line.description_raw,
                    "quantity": line.quantity,
                    "unit": line.unit,
                    "unit_price_cents": line.unit_price_cents,
                    "price_per_base_unit_cents": (
                        line.price_per_base_unit_cents
                        if line.quantity is not None and line.unit is not None
                        else None
                    ),
                    "line_total_cents": line.line_total_cents,
                })
            elif (
                line.kind in {"discount", "coupon", "return", "deposit", "fee"}
                and line.line_total_cents is not None
            ):
                adjustment_bucket["signed_adjustments_cents"] += line.line_total_cents
                if line.kind in {"discount", "coupon"}:
                    adjustment_bucket["discount_savings_cents"] += -line.line_total_cents
                elif line.kind == "return":
                    adjustment_bucket["return_credits_cents"] += -line.line_total_cents
                elif line.kind in {"deposit", "fee"}:
                    adjustment_bucket["deposit_and_fee_cents"] += line.line_total_cents
            purchases.append({
                "receipt_id": receipt.receipt_id,
                "receipt_line_id": line.receipt_line_id,
                "merchant_name_raw": current.merchant_name_raw,
                "purchased_on": purchased_on,
                "currency": current.currency,
                "position": line.position,
                "kind": line.kind,
                "description_raw": line.description_raw,
                "normalized_label": line.normalized_label,
                "quantity": line.quantity,
                "unit": line.unit,
                "line_total_cents": line.line_total_cents,
            })

    public_by_currency = {
        code: _public_currency_bucket(bucket)
        for code, bucket in sorted(by_currency.items())
    }
    currencies = sorted(public_by_currency)
    if len(currencies) == 1:
        total_spend_cents: int | None = public_by_currency[currencies[0]]["spend_cents"]
        result_currency: str | None = currencies[0]
    elif not currencies:
        total_spend_cents = 0
        result_currency = currency
    else:
        total_spend_cents = None
        result_currency = None

    public_adjustments_by_currency = {
        code: dict(adjustments_by_currency[code]) for code in currencies
    }
    if len(currencies) <= 1:
        combined_adjustments = (
            public_adjustments_by_currency[currencies[0]]
            if currencies else _new_adjustment_bucket()
        )
    else:
        combined_adjustments = {
            key: None for key in _new_adjustment_bucket()
        }

    purchases.sort(key=lambda row: (
        row["purchased_on"] or "",
        row["receipt_id"],
        row["position"],
    ))
    for rows in price_history.values():
        rows.sort(key=lambda row: (row["purchased_on"] or "", row["receipt_id"]))

    total_lines = len(purchases)
    public_by_merchant = {}
    for normalized, merchant in sorted(by_merchant.items()):
        row = _public_aggregate(merchant["aggregate"])
        row["merchant_name_normalized"] = normalized
        row["merchant_names_raw"] = sorted(merchant["merchant_names_raw"])
        public_by_merchant[normalized] = row
    return {
        "from_date": start,
        "to_date": end,
        "merchant_filter": merchant_name,
        "currency": result_currency,
        "receipt_count": len(included),
        "total_spend_cents": total_spend_cents,
        "by_currency": public_by_currency,
        "by_merchant": public_by_merchant,
        "by_day": {
            key: _public_aggregate(row) for key, row in sorted(by_day.items())
        },
        "by_week": {
            key: _public_aggregate(row) for key, row in sorted(by_week.items())
        },
        "by_month": {
            key: _public_aggregate(row) for key, row in sorted(by_month.items())
        },
        **combined_adjustments,
        "adjustments_by_currency": public_adjustments_by_currency,
        "reconciliation_gaps": reconciliation_gaps,
        "coverage": {
            "line_count": total_lines,
            "known_line_amount_count": known_line_amounts,
            "known_line_amount_ratio": (
                known_line_amounts / total_lines if total_lines else None
            ),
            "product_line_count": product_lines,
            "normalized_product_line_count": normalized_product_lines,
            "normalized_product_ratio": (
                normalized_product_lines / product_lines if product_lines else None
            ),
            "known_line_confidence_count": known_confidence_count,
            "average_line_confidence": (
                confidence_total / known_confidence_count
                if known_confidence_count else None
            ),
            "low_confidence_line_count": low_confidence_line_count,
            "evidence_confidence": dict(sorted(evidence_confidence.items())),
        },
        "excluded_by_status": dict(sorted(excluded_by_status.items())),
        "purchases": purchases,
        "price_history": dict(sorted(price_history.items())),
    }
