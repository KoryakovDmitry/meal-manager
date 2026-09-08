"""Canonical purchase-receipt domain model.

Receipt history is deliberately independent from inventory and shopping state.
Every correction is a complete append-only revision; raw printed labels and
integer minor-unit amounts are preserved exactly.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, cast


RECEIPT_SCHEMA_VERSION = 1
RECEIPT_STATUSES = {"needs_review", "confirmed", "corrected", "retracted"}
RECEIPT_LINE_KINDS = {
    "product", "discount", "coupon", "return", "deposit", "fee", "other",
}
TIME_PRECISIONS = {"date", "datetime", "unknown"}
EVIDENCE_KINDS = {"image", "pdf", "text", "manual", "other"}
EVIDENCE_CONFIDENCE = {"high", "medium", "low", "mixed", "unknown"}
MAX_RECEIPT_LINES = 500
QUANTITY_PATTERN = (
    r"^\+?(?=[^eE]*[1-9])(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)"
    r"(?:[eE][+-]?[0-9]+)?$"
)

_REVISION_FIELDS = {
    "revision", "recorded_at", "reason", "status", "merchant_name_raw",
    "merchant_name_normalized", "branch", "address", "purchased_at",
    "time_precision", "currency", "lines", "subtotal_cents", "total_cents",
    "lines_total_cents", "reconciliation_delta_cents", "evidence",
    "semantic_fingerprint", "provenance",
}
_LINE_FIELDS = {
    "receipt_line_id", "position", "description_raw", "normalized_label",
    "category", "kind", "quantity", "unit", "package_description",
    "unit_price_cents", "price_per_base_unit_cents", "line_total_cents",
    "confidence", "ambiguity_note", "links",
}
_LINK_FIELDS = {
    "inventory_item_ids", "product_ids", "shopping_occurrence_ids",
}
_EVIDENCE_FIELDS = {
    "source_kind", "source_reference", "content_sha256",
    "transcription_method", "confidence", "notes",
}
_PROVENANCE_FIELDS = {"actor_type", "surface_kind"}
_INPUT_FIELDS = {
    "merchant_name_raw", "merchant_name_normalized", "branch", "address",
    "purchased_at", "time_precision", "currency", "lines", "subtotal_cents",
    "total_cents", "evidence",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(
    value: Any,
    *,
    label: str,
    required: bool = False,
    max_length: int = 2000,
) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{label} is required")
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string or null")
    if len(value) > max_length:
        raise ValueError(f"{label} is too long (max {max_length} chars)")
    value = value.strip()
    if not value:
        if required:
            raise ValueError(f"{label} cannot be empty")
        return None
    return value


def _passes_luhn(value: str) -> bool:
    digits = [int(char) for char in value if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    parity = len(digits) % 2
    total = 0
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _privacy_text(
    value: Any,
    *,
    label: str,
    max_length: int,
    required: bool = False,
    strict: bool = True,
) -> str | None:
    text = _text(
        value, label=label, max_length=max_length, required=required
    )
    if text is None:
        return None
    folded = text.casefold()
    digit_groups = re.findall(r"(?:\d[\s-]*){13,19}", text)
    has_pan = any(_passes_luhn(candidate) for candidate in digit_groups)
    has_iban = re.search(
        r"\b[A-Z]{2}\d{2}(?:[ -]?[A-Z0-9]){11,30}\b", text.upper()
    )
    has_marked_identifier = re.search(
        r"\b(?:visa|mastercard|amex|card|carte|pan|loyalty|fidelity|member|"
        r"customer|client|club)\b.{0,40}(?:\D*\d){4,}",
        folded,
    )
    has_qr_payload = re.search(
        r"\bqr(?:\s*code)?\b\s*[:=#-]?\s*\S{8,}", folded
    )
    has_payment_identifier = re.search(
        r"\b(?:(?:payment\s+)?(?:auth(?:ori[sz]ation)?|authori[sz]ation|approval|"
        r"transaction|terminal|merchant)\s+(?:code|id|number|no\.?|reference|ref|token)|"
        r"payment\s+(?:reference|ref|token|id|number|no\.?))\b"
        r".{0,20}[a-z0-9][a-z0-9._:/-]{3,}",
        folded,
    )
    if has_iban or has_marked_identifier or has_qr_payload or has_payment_identifier or (
        strict and has_pan
    ):
        raise ValueError(
            f"{label} contains sensitive payment, loyalty, or QR data; redact it first"
        )
    return text


def _cents(value: Any, *, label: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{label} must be integer minor units or null")
    if abs(value) > 10**12:
        raise ValueError(f"{label} is outside the supported range")
    return value


def _confidence(value: Any, *, label: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a number from 0 to 1 or null")
    value = float(value)
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{label} must be a finite number from 0 to 1")
    return value


def normalize_merchant(value: str) -> str:
    return " ".join(value.casefold().split())


def normalize_currency(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z]{3}", value) is None:
        raise ValueError("currency must be a three-letter ISO code")
    return value.upper()


def normalize_purchase_time(value: Any, precision: Any) -> tuple[str | None, str]:
    if precision not in TIME_PRECISIONS:
        raise ValueError("time_precision must be date, datetime, or unknown")
    if precision == "unknown":
        if value is not None:
            raise ValueError("unknown time precision requires purchased_at=null")
        return None, "unknown"
    if not isinstance(value, str):
        raise ValueError("purchased_at must be a string for known time precision")
    if value != value.strip():
        raise ValueError("purchased_at cannot contain surrounding whitespace")
    if precision == "date":
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
            raise ValueError("date-precision purchased_at must use YYYY-MM-DD")
        return date.fromisoformat(value).isoformat(), precision
    if re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})",
        value,
    ) is None:
        raise ValueError("datetime-precision purchased_at must use RFC 3339 with timezone")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("datetime-precision purchased_at must be timezone-aware")
    return parsed.isoformat().replace("+00:00", "Z"), precision


def purchase_date(purchased_at: str | None, precision: str) -> str | None:
    if purchased_at is None:
        return None
    if precision == "date":
        return purchased_at
    return datetime.fromisoformat(purchased_at.replace("Z", "+00:00")).date().isoformat()


def normalize_quantity(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("receipt line quantity must be lossless decimal text or null")
    if not value or len(value) > 100 or re.fullmatch(QUANTITY_PATTERN, value) is None:
        raise ValueError("receipt line quantity is invalid")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("receipt line quantity is invalid decimal text") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError("receipt line quantity must be finite and positive")
    return value


def normalize_links(value: Any) -> dict[str, list[str]]:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - _LINK_FIELDS:
        raise ValueError("receipt line links contain unsupported fields")
    result: dict[str, list[str]] = {}
    for key in sorted(_LINK_FIELDS):
        raw = value.get(key, [])
        if not isinstance(raw, list):
            raise ValueError(f"receipt line links.{key} must be an array")
        unique: list[str] = []
        for item in raw:
            item = _text(item, label=f"receipt line links.{key}", required=True, max_length=200)
            item = cast(str, item)
            if item not in unique:
                unique.append(item)
        if len(unique) > 100:
            raise ValueError(f"receipt line links.{key} has too many entries")
        result[key] = unique
    return result


def normalize_evidence(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - _EVIDENCE_FIELDS:
        raise ValueError("evidence must be an object with supported fields")
    source_kind = value.get("source_kind")
    if source_kind not in EVIDENCE_KINDS:
        raise ValueError("evidence.source_kind is invalid")
    source_reference = _privacy_text(
        value.get("source_reference"),
        label="evidence.source_reference",
        max_length=4096,
        strict=True,
    )
    content_sha256 = value.get("content_sha256")
    if content_sha256 is not None:
        if not isinstance(content_sha256, str):
            raise ValueError("evidence.content_sha256 must be a hex string or null")
        content_sha256 = content_sha256.lower().removeprefix("sha256:")
        if re.fullmatch(r"[0-9a-f]{64}", content_sha256) is None:
            raise ValueError("evidence.content_sha256 must contain 64 hex characters")
    confidence = value.get("confidence", "unknown")
    if confidence not in EVIDENCE_CONFIDENCE:
        raise ValueError("evidence.confidence is invalid")
    return {
        "source_kind": source_kind,
        "source_reference": source_reference,
        "content_sha256": content_sha256,
        "transcription_method": _privacy_text(
            value.get("transcription_method"),
            label="evidence.transcription_method",
            max_length=200,
            strict=True,
        ),
        "confidence": confidence,
        "notes": _privacy_text(
            value.get("notes"), label="evidence.notes", max_length=4000, strict=True
        ),
    }


@dataclass
class ReceiptLine:
    receipt_line_id: str
    position: int
    description_raw: str
    normalized_label: str | None
    category: str | None
    kind: str | None
    quantity: str | None
    unit: str | None
    package_description: str | None
    unit_price_cents: int | None
    price_per_base_unit_cents: int | None
    line_total_cents: int | None
    confidence: float | None
    ambiguity_note: str | None
    links: dict[str, list[str]]

    @classmethod
    def from_input(
        cls,
        value: Any,
        *,
        position: int,
        previous: ReceiptLine | None = None,
    ) -> ReceiptLine:
        if not isinstance(value, dict):
            raise ValueError("receipt lines must be objects")
        allowed = _LINE_FIELDS - {"position"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"receipt line contains unsupported fields: {sorted(unknown)}")
        supplied_id = value.get("receipt_line_id")
        if supplied_id is None and previous is not None:
            supplied_id = previous.receipt_line_id
        if supplied_id is None:
            supplied_id = "rline_" + uuid.uuid4().hex
        if not isinstance(supplied_id, str) or re.fullmatch(r"rline_[0-9a-f]{32}", supplied_id) is None:
            raise ValueError("receipt_line_id must be a canonical rline_ identifier")
        kind = value.get("kind")
        if kind is not None and kind not in RECEIPT_LINE_KINDS:
            raise ValueError("receipt line kind is invalid")
        line_total = _cents(value.get("line_total_cents"), label="line_total_cents")
        if kind in {"discount", "coupon", "return"} and line_total is not None and line_total > 0:
            raise ValueError(f"{kind} line totals must be non-positive")
        return cls(
            receipt_line_id=supplied_id,
            position=position,
            description_raw=cast(str, _privacy_text(
                value.get("description_raw"),
                label="receipt line description_raw",
                max_length=2000,
                required=True,
            )),
            normalized_label=_privacy_text(
                value.get("normalized_label"),
                label="receipt line normalized_label",
                max_length=500,
            ),
            category=_privacy_text(
                value.get("category"), label="receipt line category", max_length=200
            ),
            kind=kind,
            quantity=normalize_quantity(value.get("quantity")),
            unit=_privacy_text(
                value.get("unit"), label="receipt line unit", max_length=100
            ),
            package_description=_privacy_text(
                value.get("package_description"),
                label="receipt line package_description",
                max_length=500,
            ),
            unit_price_cents=_cents(
                value.get("unit_price_cents"), label="unit_price_cents"
            ),
            price_per_base_unit_cents=_cents(
                value.get("price_per_base_unit_cents"),
                label="price_per_base_unit_cents",
            ),
            line_total_cents=line_total,
            confidence=_confidence(value.get("confidence"), label="receipt line confidence"),
            ambiguity_note=_privacy_text(
                value.get("ambiguity_note"),
                label="receipt line ambiguity_note",
                max_length=2000,
            ),
            links=normalize_links(value.get("links")),
        )

    @classmethod
    def from_dict(cls, value: Any) -> ReceiptLine:
        if not isinstance(value, dict) or set(value) != _LINE_FIELDS:
            raise ValueError("persisted receipt line fields do not match schema v1")
        position = value.get("position")
        if not isinstance(position, int) or isinstance(position, bool) or position < 1:
            raise ValueError("persisted receipt line position is invalid")
        input_value = {key: item for key, item in value.items() if key != "position"}
        result = cls.from_input(input_value, position=position)
        if result.position != position:
            raise ValueError("persisted receipt line position changed")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_line_id": self.receipt_line_id,
            "position": self.position,
            "description_raw": self.description_raw,
            "normalized_label": self.normalized_label,
            "category": self.category,
            "kind": self.kind,
            "quantity": self.quantity,
            "unit": self.unit,
            "package_description": self.package_description,
            "unit_price_cents": self.unit_price_cents,
            "price_per_base_unit_cents": self.price_per_base_unit_cents,
            "line_total_cents": self.line_total_cents,
            "confidence": self.confidence,
            "ambiguity_note": self.ambiguity_note,
            "links": {key: list(value) for key, value in self.links.items()},
        }

    def semantic_dict(self) -> dict[str, Any]:
        data = self.to_dict()
        data.pop("receipt_line_id")
        data.pop("position")
        data.pop("links")
        return data


def _line_source_identity(line: ReceiptLine) -> tuple[Any, ...]:
    """Fields that identify the same printed source line across a correction."""

    return (
        line.description_raw,
        line.kind,
        line.quantity,
        line.unit,
        line.package_description,
        line.unit_price_cents,
        line.price_per_base_unit_cents,
        line.line_total_cents,
    )


@dataclass
class ReceiptRevision:
    revision: int
    recorded_at: str
    reason: str | None
    status: str
    merchant_name_raw: str
    merchant_name_normalized: str
    branch: str | None
    address: str | None
    purchased_at: str | None
    time_precision: str
    currency: str
    lines: list[ReceiptLine]
    subtotal_cents: int | None
    total_cents: int | None
    lines_total_cents: int | None
    reconciliation_delta_cents: int | None
    evidence: dict[str, Any]
    semantic_fingerprint: str
    provenance: dict[str, str]

    @property
    def purchased_on(self) -> str | None:
        return purchase_date(self.purchased_at, self.time_precision)

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "recorded_at": self.recorded_at,
            "reason": self.reason,
            "status": self.status,
            "merchant_name_raw": self.merchant_name_raw,
            "merchant_name_normalized": self.merchant_name_normalized,
            "branch": self.branch,
            "address": self.address,
            "purchased_at": self.purchased_at,
            "time_precision": self.time_precision,
            "currency": self.currency,
            "lines": [line.to_dict() for line in self.lines],
            "subtotal_cents": self.subtotal_cents,
            "total_cents": self.total_cents,
            "lines_total_cents": self.lines_total_cents,
            "reconciliation_delta_cents": self.reconciliation_delta_cents,
            "evidence": dict(self.evidence),
            "semantic_fingerprint": self.semantic_fingerprint,
            "provenance": dict(self.provenance),
        }

    @classmethod
    def from_dict(cls, value: Any) -> ReceiptRevision:
        if not isinstance(value, dict) or set(value) != _REVISION_FIELDS:
            raise ValueError("persisted receipt revision fields do not match schema v1")
        revision = value.get("revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError("persisted receipt revision number is invalid")
        recorded_at = _timezone_datetime(value.get("recorded_at"), label="recorded_at")
        status = value.get("status")
        if status not in RECEIPT_STATUSES:
            raise ValueError("persisted receipt status is invalid")
        reason = _privacy_text(
            value.get("reason"), label="revision reason", max_length=2000, strict=True
        )
        if revision == 1:
            if reason is not None or status not in {"confirmed", "needs_review"}:
                raise ValueError("initial receipt revision has invalid status or reason")
        elif reason is None:
            raise ValueError("receipt correction/retraction revision requires a reason")
        merchant_raw = cast(str, _privacy_text(
            value.get("merchant_name_raw"),
            label="merchant_name_raw",
            required=True,
            max_length=500,
        ))
        merchant_normalized = cast(str, _privacy_text(
            value.get("merchant_name_normalized"),
            label="merchant_name_normalized",
            required=True,
            max_length=500,
        ))
        if merchant_normalized != normalize_merchant(merchant_normalized):
            raise ValueError("persisted merchant_name_normalized is not canonical")
        purchased_at, precision = normalize_purchase_time(
            value.get("purchased_at"), value.get("time_precision")
        )
        lines_raw = value.get("lines")
        if not isinstance(lines_raw, list) or not 1 <= len(lines_raw) <= MAX_RECEIPT_LINES:
            raise ValueError("persisted receipt lines are invalid")
        lines = [ReceiptLine.from_dict(item) for item in lines_raw]
        if [line.position for line in lines] != list(range(1, len(lines) + 1)):
            raise ValueError("persisted receipt line order is invalid")
        if len({line.receipt_line_id for line in lines}) != len(lines):
            raise ValueError("persisted receipt line IDs are duplicated")
        evidence = normalize_evidence(value.get("evidence"))
        provenance = value.get("provenance")
        if not isinstance(provenance, dict) or set(provenance) != _PROVENANCE_FIELDS:
            raise ValueError("persisted receipt provenance is invalid")
        provenance = {
            "actor_type": cast(str, _text(
                provenance.get("actor_type"), label="provenance.actor_type", required=True
            )),
            "surface_kind": cast(str, _text(
                provenance.get("surface_kind"), label="provenance.surface_kind", required=True
            )),
        }
        result = cls(
            revision=revision,
            recorded_at=recorded_at,
            reason=reason,
            status=status,
            merchant_name_raw=merchant_raw,
            merchant_name_normalized=merchant_normalized,
            branch=_privacy_text(value.get("branch"), label="branch", max_length=1000),
            address=_privacy_text(value.get("address"), label="address", max_length=2000),
            purchased_at=purchased_at,
            time_precision=precision,
            currency=normalize_currency(value.get("currency")),
            lines=lines,
            subtotal_cents=_cents(value.get("subtotal_cents"), label="subtotal_cents"),
            total_cents=_cents(value.get("total_cents"), label="total_cents"),
            lines_total_cents=_cents(
                value.get("lines_total_cents"), label="lines_total_cents"
            ),
            reconciliation_delta_cents=_cents(
                value.get("reconciliation_delta_cents"),
                label="reconciliation_delta_cents",
            ),
            evidence=evidence,
            semantic_fingerprint=_fingerprint_text(value.get("semantic_fingerprint")),
            provenance=provenance,
        )
        _verify_revision_computed_fields(result)
        return result


def _timezone_datetime(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a timezone-aware RFC3339 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed.isoformat().replace("+00:00", "Z")


def _fingerprint_text(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError("semantic_fingerprint is invalid")
    return value


def _semantic_payload(revision: ReceiptRevision) -> dict[str, Any]:
    return {
        "merchant_name_raw": revision.merchant_name_raw,
        "merchant_name_normalized": revision.merchant_name_normalized,
        "branch": revision.branch,
        "address": revision.address,
        "purchased_at": revision.purchased_at,
        "time_precision": revision.time_precision,
        "currency": revision.currency,
        "lines": [line.semantic_dict() for line in revision.lines],
        "subtotal_cents": revision.subtotal_cents,
        "total_cents": revision.total_cents,
    }


def semantic_fingerprint(revision: ReceiptRevision) -> str:
    raw = json.dumps(
        _semantic_payload(revision),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _computed_line_total(lines: list[ReceiptLine]) -> int | None:
    if any(line.line_total_cents is None for line in lines):
        return None
    return _cents(
        sum(
            line.line_total_cents
            for line in lines
            if line.line_total_cents is not None
        ),
        label="lines_total_cents",
    )


def _review_required(
    *,
    lines: list[ReceiptLine],
    total_cents: int | None,
    lines_total_cents: int | None,
    reconciliation_delta_cents: int | None,
) -> bool:
    if any(line.ambiguity_note is not None for line in lines):
        return True
    if total_cents is not None and lines_total_cents is None:
        return True
    return reconciliation_delta_cents not in (None, 0)


def _verify_revision_computed_fields(revision: ReceiptRevision) -> None:
    computed_total = _computed_line_total(revision.lines)
    computed_delta = None
    if computed_total is not None and revision.total_cents is not None:
        computed_delta = _cents(
            computed_total - revision.total_cents,
            label="reconciliation_delta_cents",
        )
    if revision.lines_total_cents != computed_total:
        raise ValueError("persisted lines_total_cents does not match receipt lines")
    if revision.reconciliation_delta_cents != computed_delta:
        raise ValueError("persisted reconciliation_delta_cents is invalid")
    if semantic_fingerprint(revision) != revision.semantic_fingerprint:
        raise ValueError("persisted semantic_fingerprint is invalid")
    if revision.status in {"confirmed", "corrected"} and _review_required(
        lines=revision.lines,
        total_cents=revision.total_cents,
        lines_total_cents=computed_total,
        reconciliation_delta_cents=computed_delta,
    ):
        raise ValueError("persisted confirmed receipt still requires review")


def revision_input(revision: ReceiptRevision) -> dict[str, Any]:
    lines = []
    for line in revision.lines:
        item = line.to_dict()
        item.pop("position")
        lines.append(item)
    return {
        "merchant_name_raw": revision.merchant_name_raw,
        "merchant_name_normalized": revision.merchant_name_normalized,
        "branch": revision.branch,
        "address": revision.address,
        "purchased_at": revision.purchased_at,
        "time_precision": revision.time_precision,
        "currency": revision.currency,
        "lines": lines,
        "subtotal_cents": revision.subtotal_cents,
        "total_cents": revision.total_cents,
        "evidence": dict(revision.evidence),
    }


def build_revision(
    value: Any,
    *,
    revision: int,
    requested_status: str,
    reason: str | None,
    provenance: dict[str, str],
    previous: ReceiptRevision | None = None,
    recorded_at: str | None = None,
) -> ReceiptRevision:
    if not isinstance(value, dict):
        raise ValueError("receipt payload must be an object")
    unknown = set(value) - _INPUT_FIELDS
    if unknown:
        raise ValueError(f"receipt payload contains unsupported fields: {sorted(unknown)}")
    if requested_status not in RECEIPT_STATUSES:
        raise ValueError("receipt status is invalid")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("receipt revision must be a positive integer")
    normalized_reason = _privacy_text(
        reason, label="revision reason", max_length=2000, strict=True
    )
    if revision == 1:
        if normalized_reason is not None or requested_status not in {"confirmed", "needs_review"}:
            raise ValueError("initial receipt revision has invalid status or reason")
    elif normalized_reason is None:
        raise ValueError("receipt correction/retraction revision requires a reason")
    merchant_raw = cast(str, _privacy_text(
        value.get("merchant_name_raw"),
        label="merchant_name_raw",
        required=True,
        max_length=500,
    ))
    merchant_normalized = value.get("merchant_name_normalized")
    if merchant_normalized is None:
        merchant_normalized = normalize_merchant(merchant_raw)
    else:
        merchant_normalized = normalize_merchant(cast(str, _privacy_text(
            merchant_normalized,
            label="merchant_name_normalized",
            required=True,
            max_length=500,
        )))
    purchased_at, precision = normalize_purchase_time(
        value.get("purchased_at"), value.get("time_precision")
    )
    raw_lines = value.get("lines")
    if not isinstance(raw_lines, list) or not 1 <= len(raw_lines) <= MAX_RECEIPT_LINES:
        raise ValueError(f"lines must contain from 1 to {MAX_RECEIPT_LINES} entries")
    if previous is None and any(
        isinstance(raw, dict) and "links" in raw for raw in raw_lines
    ):
        raise ValueError("initial receipt links are assigned through the dedicated link operation")
    previous_by_position = {
        line.position: line for line in previous.lines
    } if previous is not None else {}
    previous_by_id = {
        line.receipt_line_id: line for line in previous.lines
    } if previous is not None else {}
    supplied_ids = {
        raw.get("receipt_line_id")
        for raw in raw_lines
        if isinstance(raw, dict) and raw.get("receipt_line_id") is not None
    }
    if previous is None and supplied_ids:
        raise ValueError("initial receipt line IDs are assigned by the server")
    unknown_supplied = supplied_ids - set(previous_by_id)
    if unknown_supplied:
        raise ValueError("corrected receipt contains unknown receipt_line_id values")

    used_ids = set(supplied_ids)
    source_candidates: dict[tuple[Any, ...], list[ReceiptLine]] = {}
    for old_line in previous.lines if previous is not None else []:
        if old_line.receipt_line_id not in used_ids:
            source_candidates.setdefault(_line_source_identity(old_line), []).append(old_line)

    provisional: list[ReceiptLine] = []
    selected: dict[int, ReceiptLine] = {}
    for index, raw in enumerate(raw_lines, 1):
        if not isinstance(raw, dict):
            raise ValueError("receipt lines must be objects")
        provisional.append(ReceiptLine.from_input(raw, position=index))
        supplied_id = raw.get("receipt_line_id")
        if supplied_id is not None:
            selected[index] = previous_by_id[supplied_id]

    # Infer identity only from exact printed-line semantics. Unmatched rows get
    # fresh IDs; carrying an edited row's identity requires an explicit ID.
    # This prevents an unrelated same-position replacement from inheriting a
    # removed row's analytical links.
    same_length = previous is not None and len(raw_lines) == len(previous.lines)
    previous_identity_counts: dict[tuple[Any, ...], int] = {}
    for old_line in previous.lines if previous is not None else []:
        identity = _line_source_identity(old_line)
        previous_identity_counts[identity] = previous_identity_counts.get(identity, 0) + 1
    new_identity_counts: dict[tuple[Any, ...], int] = {}
    for candidate_line in provisional:
        identity = _line_source_identity(candidate_line)
        new_identity_counts[identity] = new_identity_counts.get(identity, 0) + 1
    if previous is not None:
        for index, candidate_line in enumerate(provisional, 1):
            if index in selected:
                continue
            identity = _line_source_identity(candidate_line)
            candidates = [
                candidate
                for candidate in source_candidates.get(identity, [])
                if candidate.receipt_line_id not in used_ids
            ]
            identity_cardinality_unchanged = (
                previous_identity_counts.get(identity, 0)
                == new_identity_counts.get(identity, 0)
            )
            if candidates and not identity_cardinality_unchanged:
                raise ValueError(
                    "ambiguous receipt line identity; provide receipt_line_id values"
                )
            if len(candidates) > 1:
                positional = previous_by_position.get(index)
                if (
                    same_length
                    and identity_cardinality_unchanged
                    and positional in candidates
                    and _line_source_identity(positional) == identity
                ):
                    match = positional
                else:
                    raise ValueError(
                        "ambiguous receipt line identity; provide receipt_line_id values"
                    )
            elif candidates:
                match = candidates[0]
            else:
                match = None
            if match is not None:
                selected[index] = match
                used_ids.add(match.receipt_line_id)

    lines: list[ReceiptLine] = []
    for index, raw in enumerate(raw_lines, 1):
        item = dict(raw)
        selected_previous = selected.get(index)
        if selected_previous is not None:
            item["receipt_line_id"] = selected_previous.receipt_line_id
            if "links" not in item:
                item["links"] = {
                    key: list(values)
                    for key, values in selected_previous.links.items()
                }
        lines.append(ReceiptLine.from_input(item, position=index))
    if len({line.receipt_line_id for line in lines}) != len(lines):
        raise ValueError("receipt line IDs must be unique")
    subtotal = _cents(value.get("subtotal_cents"), label="subtotal_cents")
    total = _cents(value.get("total_cents"), label="total_cents")
    lines_total = _computed_line_total(lines)
    delta = None
    if lines_total is not None and total is not None:
        delta = _cents(
            lines_total - total, label="reconciliation_delta_cents"
        )
    status = requested_status
    if status != "retracted" and _review_required(
        lines=lines,
        total_cents=total,
        lines_total_cents=lines_total,
        reconciliation_delta_cents=delta,
    ):
        status = "needs_review"
    if not isinstance(provenance, dict) or set(provenance) != _PROVENANCE_FIELDS:
        raise ValueError("receipt provenance requires actor_type and surface_kind")
    normalized_provenance = {
        "actor_type": cast(str, _text(
            provenance.get("actor_type"), label="provenance.actor_type", required=True
        )),
        "surface_kind": cast(str, _text(
            provenance.get("surface_kind"), label="provenance.surface_kind", required=True
        )),
    }
    result = ReceiptRevision(
        revision=revision,
        recorded_at=_timezone_datetime(recorded_at or utc_now(), label="recorded_at"),
        reason=normalized_reason,
        status=status,
        merchant_name_raw=merchant_raw,
        merchant_name_normalized=merchant_normalized,
        branch=_privacy_text(value.get("branch"), label="branch", max_length=1000),
        address=_privacy_text(value.get("address"), label="address", max_length=2000),
        purchased_at=purchased_at,
        time_precision=precision,
        currency=normalize_currency(value.get("currency")),
        lines=lines,
        subtotal_cents=subtotal,
        total_cents=total,
        lines_total_cents=lines_total,
        reconciliation_delta_cents=delta,
        evidence=normalize_evidence(value.get("evidence")),
        semantic_fingerprint="sha256:" + "0" * 64,
        provenance=normalized_provenance,
    )
    result.semantic_fingerprint = semantic_fingerprint(result)
    return result


def _revision_state_without_links(revision: ReceiptRevision) -> dict[str, Any]:
    state = revision.to_dict()
    for field in ("revision", "recorded_at", "reason", "status", "provenance"):
        state.pop(field)
    for line in state["lines"]:
        line.pop("links")
    return state


def _revision_link_state(revision: ReceiptRevision) -> list[dict[str, Any]]:
    return [
        {
            "receipt_line_id": line.receipt_line_id,
            "links": {key: list(values) for key, values in line.links.items()},
        }
        for line in revision.lines
    ]


def _existing_line_links_are_preserved(
    previous: ReceiptRevision, current: ReceiptRevision
) -> bool:
    previous_links = {
        line.receipt_line_id: line.links for line in previous.lines
    }
    for line in current.lines:
        prior = previous_links.get(line.receipt_line_id)
        if prior is not None and line.links != prior:
            return False
        if prior is None and any(line.links.values()):
            return False
    return True


def _validate_revision_history(
    created_at: str, revisions: list[ReceiptRevision]
) -> None:
    if [item.revision for item in revisions] != list(range(1, len(revisions) + 1)):
        raise ValueError("persisted receipt revision sequence is invalid")
    if created_at != revisions[0].recorded_at:
        raise ValueError("persisted receipt created_at does not match first revision")
    revision_times = [
        datetime.fromisoformat(item.recorded_at.replace("Z", "+00:00"))
        for item in revisions
    ]
    if any(
        current < previous
        for previous, current in zip(revision_times, revision_times[1:])
    ):
        raise ValueError("persisted receipt revisions are not chronologically ordered")
    if any(item.status == "retracted" for item in revisions[:-1]):
        raise ValueError("persisted receipt cannot contain revisions after retraction")
    if any(any(line.links.values()) for line in revisions[0].lines):
        raise ValueError("persisted initial receipt revision cannot contain links")

    active_ids = {line.receipt_line_id for line in revisions[0].lines}
    retired_ids: set[str] = set()
    for previous, current in zip(revisions, revisions[1:]):
        current_ids = {line.receipt_line_id for line in current.lines}
        if current_ids & retired_ids:
            raise ValueError("persisted receipt reused a retired receipt_line_id")

        previous_source_ids: dict[tuple[Any, ...], set[str]] = {}
        current_source_ids: dict[tuple[Any, ...], set[str]] = {}
        for line in previous.lines:
            previous_source_ids.setdefault(_line_source_identity(line), set()).add(
                line.receipt_line_id
            )
        for line in current.lines:
            current_source_ids.setdefault(_line_source_identity(line), set()).add(
                line.receipt_line_id
            )
        for identity in set(previous_source_ids) & set(current_source_ids):
            old_ids = previous_source_ids[identity]
            new_ids = current_source_ids[identity]
            if len(old_ids & new_ids) != min(len(old_ids), len(new_ids)):
                raise ValueError(
                    "persisted unchanged receipt line changed stable identity"
                )

        state_changed = (
            _revision_state_without_links(previous)
            != _revision_state_without_links(current)
        )
        links_changed = _revision_link_state(previous) != _revision_link_state(current)
        if current.status == "retracted":
            if state_changed or links_changed:
                raise ValueError(
                    "persisted retraction revision changed receipt contents"
                )
            continue
        if not state_changed and links_changed:
            if current.status != previous.status:
                raise ValueError(
                    "persisted analytical-link revision has invalid contents or status"
                )
            continue
        if not state_changed:
            raise ValueError("persisted receipt contains a no-op revision")
        if not _existing_line_links_are_preserved(previous, current):
            raise ValueError(
                "persisted receipt correction changed analytical links"
            )
        if current.status not in {"corrected", "needs_review"}:
            raise ValueError("persisted receipt correction has invalid status transition")
        retired_ids.update(active_ids - current_ids)
        active_ids = current_ids


@dataclass
class PurchaseReceipt:
    receipt_id: str
    created_at: str
    revisions: list[ReceiptRevision]

    @property
    def current(self) -> ReceiptRevision:
        return self.revisions[-1]

    @classmethod
    def from_dict(cls, value: Any) -> PurchaseReceipt:
        if not isinstance(value, dict) or set(value) != {"receipt_id", "created_at", "revisions"}:
            raise ValueError("persisted receipt fields do not match schema v1")
        receipt_id = value.get("receipt_id")
        if not isinstance(receipt_id, str) or re.fullmatch(r"receipt_[0-9a-f]{32}", receipt_id) is None:
            raise ValueError("persisted receipt_id is invalid")
        created_at = _timezone_datetime(value.get("created_at"), label="created_at")
        raw_revisions = value.get("revisions")
        if not isinstance(raw_revisions, list) or not raw_revisions:
            raise ValueError("persisted receipt revisions are invalid")
        revisions = [ReceiptRevision.from_dict(item) for item in raw_revisions]
        _validate_revision_history(created_at, revisions)
        return cls(receipt_id=receipt_id, created_at=created_at, revisions=revisions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "created_at": self.created_at,
            "revisions": [revision.to_dict() for revision in self.revisions],
        }

    def to_public(self, *, include_revisions: bool = False) -> dict[str, Any]:
        result = self.current.to_dict()
        result.update({
            "receipt_id": self.receipt_id,
            "created_at": self.created_at,
            "purchased_on": self.current.purchased_on,
        })
        if include_revisions:
            result["revisions"] = [revision.to_dict() for revision in self.revisions]
        return result

    def summary(self) -> dict[str, Any]:
        current = self.current
        return {
            "receipt_id": self.receipt_id,
            "revision": current.revision,
            "status": current.status,
            "merchant_name_raw": current.merchant_name_raw,
            "branch": current.branch,
            "purchased_at": current.purchased_at,
            "purchased_on": current.purchased_on,
            "time_precision": current.time_precision,
            "currency": current.currency,
            "line_count": len(current.lines),
            "subtotal_cents": current.subtotal_cents,
            "total_cents": current.total_cents,
            "lines_total_cents": current.lines_total_cents,
            "reconciliation_delta_cents": current.reconciliation_delta_cents,
            "recorded_at": current.recorded_at,
        }


def validate_receipt_collection(receipts: list[PurchaseReceipt]) -> None:
    """Reject cross-receipt identity collisions in a canonical ledger."""

    receipt_ids: set[str] = set()
    evidence_owners: dict[str, str] = {}
    semantic_owners: dict[str, str] = {}
    for receipt in receipts:
        if receipt.receipt_id in receipt_ids:
            raise ValueError("receipt ledger contains duplicate receipt IDs")
        receipt_ids.add(receipt.receipt_id)
        _validate_revision_history(receipt.created_at, receipt.revisions)
        for revision in receipt.revisions:
            evidence_hash = revision.evidence.get("content_sha256")
            if evidence_hash is not None:
                owner = evidence_owners.setdefault(evidence_hash, receipt.receipt_id)
                if owner != receipt.receipt_id:
                    raise ValueError("receipt evidence hash belongs to another receipt")
            fingerprint = revision.semantic_fingerprint
            owner = semantic_owners.setdefault(fingerprint, receipt.receipt_id)
            if owner != receipt.receipt_id:
                raise ValueError("receipt ledger contains duplicate canonical semantics")


def new_receipt(revision: ReceiptRevision) -> PurchaseReceipt:
    return PurchaseReceipt(
        receipt_id="receipt_" + uuid.uuid4().hex,
        created_at=revision.recorded_at,
        revisions=[revision],
    )
