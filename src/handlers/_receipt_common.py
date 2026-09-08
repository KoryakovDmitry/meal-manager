"""Shared JSON schemas and coercion for purchase-receipt tools."""

from __future__ import annotations

from ..receipt import QUANTITY_PATTERN
from ._common import maybe_parse_json_arg


def nullable_text(max_length: int, *, nonblank: bool = False):
    string_schema = {"type": "string", "maxLength": max_length}
    if nonblank:
        string_schema.update({"minLength": 1, "pattern": r".*\S.*"})
    return {"oneOf": [string_schema, {"type": "null"}]}


NULLABLE_CENTS = {
    "oneOf": [
        {"type": "integer", "minimum": -(10**12), "maximum": 10**12},
        {"type": "null"},
    ]
}

LINKS_SCHEMA = {
    "type": "object",
    "properties": {
        "inventory_item_ids": {
            "type": "array",
            "items": {
                "type": "string", "minLength": 1, "maxLength": 200,
                "pattern": r".*\S.*",
            },
            "maxItems": 100,
        },
        "product_ids": {
            "type": "array",
            "items": {
                "type": "string", "minLength": 1, "maxLength": 200,
                "pattern": r".*\S.*",
            },
            "maxItems": 100,
        },
        "shopping_occurrence_ids": {
            "type": "array",
            "items": {
                "type": "string", "minLength": 1, "maxLength": 200,
                "pattern": r".*\S.*",
            },
            "maxItems": 100,
        },
    },
    "additionalProperties": False,
}

_RECEIPT_LINE_PROPERTIES = {
    "description_raw": {
        "type": "string", "minLength": 1, "maxLength": 2000,
        "pattern": r".*\S.*",
    },
    "normalized_label": nullable_text(500),
    "category": nullable_text(200),
    "kind": {
        "oneOf": [
            {
                "type": "string",
                "enum": [
                    "product",
                    "discount",
                    "coupon",
                    "return",
                    "deposit",
                    "fee",
                    "other",
                ],
            },
            {"type": "null"},
        ],
    },
    "quantity": {
        "description": "lossless positive decimal text, or null",
        "oneOf": [
            {
                "type": "string",
                "minLength": 1,
                "maxLength": 100,
                "pattern": QUANTITY_PATTERN,
            },
            {"type": "null"},
        ],
    },
    "unit": nullable_text(100),
    "package_description": nullable_text(500),
    "unit_price_cents": {**NULLABLE_CENTS},
    "price_per_base_unit_cents": {**NULLABLE_CENTS},
    "line_total_cents": {**NULLABLE_CENTS},
    "confidence": {
        "oneOf": [
            {"type": "number", "minimum": 0, "maximum": 1},
            {"type": "null"},
        ],
    },
    "ambiguity_note": nullable_text(2000),
}

CREATE_RECEIPT_LINE_SCHEMA = {
    "type": "object",
    "properties": dict(_RECEIPT_LINE_PROPERTIES),
    "required": ["description_raw"],
    "allOf": [{
        "if": {
            "properties": {
                "kind": {"enum": ["discount", "coupon", "return"]},
            },
            "required": ["kind"],
        },
        "then": {
            "properties": {
                "line_total_cents": {
                    "oneOf": [
                        {"type": "integer", "minimum": -(10**12), "maximum": 0},
                        {"type": "null"},
                    ]
                }
            }
        },
    }],
    "additionalProperties": False,
}

CORRECTION_RECEIPT_LINE_SCHEMA = {
    **CREATE_RECEIPT_LINE_SCHEMA,
    "properties": {
        "receipt_line_id": {
            "type": "string",
            "pattern": "^rline_[0-9a-f]{32}$",
        },
        **_RECEIPT_LINE_PROPERTIES,
    },
}

EVIDENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "source_kind": {
            "type": "string",
            "enum": ["image", "pdf", "text", "manual", "other"],
        },
        "source_reference": nullable_text(4096),
        "content_sha256": {
            "oneOf": [
                {"type": "string", "pattern": "^(?:[Ss][Hh][Aa]256:)?[0-9A-Fa-f]{64}$"},
                {"type": "null"},
            ],
        },
        "transcription_method": nullable_text(200),
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low", "mixed", "unknown"],
        },
        "notes": nullable_text(4000),
    },
    "required": ["source_kind"],
    "additionalProperties": False,
}

EVIDENCE_PATCH_SCHEMA = {
    **EVIDENCE_SCHEMA,
    "required": [],
    "minProperties": 1,
}

RECEIPT_PROPERTIES = {
    "merchant_name_raw": {
        "type": "string", "minLength": 1, "maxLength": 500,
        "pattern": r".*\S.*",
    },
    "merchant_name_normalized": nullable_text(500, nonblank=True),
    "branch": nullable_text(1000),
    "address": nullable_text(2000),
    "purchased_at": nullable_text(64),
    "time_precision": {
        "type": "string",
        "enum": ["date", "datetime", "unknown"],
    },
    "currency": {"type": "string", "pattern": "^[A-Za-z]{3}$"},
    "lines": {
        "description": (
            "ordered receipt lines; include discounts and fees as signed lines"
        ),
        "type": "array",
        "items": CREATE_RECEIPT_LINE_SCHEMA,
        "minItems": 1,
        "maxItems": 500,
    },
    "subtotal_cents": {**NULLABLE_CENTS},
    "total_cents": {**NULLABLE_CENTS},
    "evidence": EVIDENCE_SCHEMA,
}

CORRECTION_RECEIPT_PROPERTIES = {
    **RECEIPT_PROPERTIES,
    "lines": {
        **RECEIPT_PROPERTIES["lines"],
        "items": CORRECTION_RECEIPT_LINE_SCHEMA,
    },
}


def coerce_receipt_payload(value):
    value = maybe_parse_json_arg(value)
    if not isinstance(value, dict):
        raise ValueError("receipt payload must be an object")
    result = dict(value)
    if "lines" in result:
        result["lines"] = maybe_parse_json_arg(result["lines"])
    if "evidence" in result:
        result["evidence"] = maybe_parse_json_arg(result["evidence"])
    return result
