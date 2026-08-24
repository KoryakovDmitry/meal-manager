"""Canonical bounded identifiers shared by cooking history and plans."""

import re


_SAFE_SUFFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def validate_prefixed_id(value, prefix: str, label: str, *, optional=False):
    """Return a canonical persisted ID or raise ValueError.

    Public boundaries and current-format persistence share the same 100-character
    bound. Restricting the suffix to an inert token alphabet also prevents path,
    whitespace, and control-character ambiguity in logs and projections.
    """
    if value is None and optional:
        return None
    suffix = value[len(prefix):] if isinstance(value, str) and value.startswith(prefix) else ""
    if (
        not isinstance(value, str)
        or len(value) > 100
        or not suffix
        or _SAFE_SUFFIX_RE.fullmatch(suffix) is None
    ):
        raise ValueError(
            f"{label} must be {prefix}<safe-nonempty> with at most 100 characters"
        )
    return value


def validate_cook_event_id(value, label="cooking event id", *, optional=False):
    return validate_prefixed_id(value, "cook_", label, optional=optional)


def validate_meal_occurrence_id(value, label="meal occurrence id", *, optional=False):
    return validate_prefixed_id(value, "mealocc_", label, optional=optional)


def validate_leftover_lot_id(value, label="leftover lot id", *, optional=False):
    return validate_prefixed_id(value, "leftover_", label, optional=optional)
