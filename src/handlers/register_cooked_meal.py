"""Tool: register_cooked_meal — commit one canonical cooking occurrence."""

import logging

from .. import tuning
from ..cooking import UNSET, register_cooked
from ..identifiers import validate_cook_event_id, validate_meal_occurrence_id
from ..repositories import fridge_repo, tuning_repo
from ._common import (
    days_since_last_cook,
    normalize_dish_name,
    reject_unknown_args,
    require_arg,
    tool_handler,
)

logger = logging.getLogger(__name__)

NAME = "register_cooked_meal"

SCHEMA = {
    "description": (
        "Register a catalog dish as cooked. When occurrence_id is supplied, "
        "the planned row remains in place and becomes cooked. The operation "
        "also appends canonical cooking history and consumes essentials. To "
        "correct metadata for the same physical cook, pass replaces_event_id; "
        "that atomically supersedes the old event without consuming again."
    ),
    "type": "object",
    "properties": {
        "dish_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 200,
            "description": "exact dish name from the catalog",
        },
        "occurrence_id": {
            "type": ["string", "null"],
            "minLength": 9,
            "maxLength": 100,
            "pattern": "^mealocc_[A-Za-z0-9][A-Za-z0-9_-]*$",
            "description": "optional stable mealocc_* ID from a weekly plan",
        },
        "expected_revision": {
            "type": ["integer", "null"],
            "minimum": 1,
            "description": "required OCC revision when occurrence_id is supplied",
        },
        "cooked_at": {
            "type": ["string", "null"],
            "maxLength": 100,
            "description": (
                "optional ISO date or timezone-aware RFC3339 actual cook time; "
                "an omitted value is inherited during correction"
            ),
        },
        "actual_portions": {
            "type": ["integer", "null"],
            "minimum": 0,
            "description": "portions actually served",
        },
        "actual_yield_portions": {
            "type": ["integer", "null"],
            "minimum": 0,
            "description": "total portions produced, including leftovers",
        },
        "replaces_event_id": {
            "type": ["string", "null"],
            "minLength": 6,
            "maxLength": 100,
            "pattern": "^cook_[A-Za-z0-9][A-Za-z0-9_-]*$",
            "description": (
                "cook event being corrected for the same physical cook; the target "
                "may still be active or already retracted. The correction preserves "
                "omitted date/portion metadata and does not consume inventory or prep "
                "again; pass explicit null for portion fields to clear them"
            ),
        },
        "acknowledge_legacy_tombstones": {
            "type": ["array", "null"],
            "items": {
                "type": "string",
                "minLength": 6,
                "maxLength": 100,
                "pattern": "^cook_[A-Za-z0-9][A-Za-z0-9_-]*$",
            },
            "minItems": 1,
            "uniqueItems": True,
            "description": (
                "legacy retracted cook events on the same occurrence that are "
                "not part of this correction's verified lineage; list them "
                "explicitly to confirm they belong to the same physical cook"
            ),
        },
    },
    "required": ["dish_name"],
    "additionalProperties": False,
    "allOf": [
        {
            "if": {
                "required": ["occurrence_id"],
                "properties": {"occurrence_id": {"type": "string"}},
            },
            "then": {
                "required": ["expected_revision"],
                "properties": {"expected_revision": {"type": "integer", "minimum": 1}},
            },
        },
        {
            "if": {
                "required": ["replaces_event_id"],
                "properties": {"replaces_event_id": {"type": "string"}},
            },
            "then": {
                "required": ["occurrence_id", "expected_revision"],
                "properties": {
                    "occurrence_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 1},
                },
            },
        },
    ],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, {
        "dish_name",
        "occurrence_id",
        "expected_revision",
        "cooked_at",
        "actual_portions",
        "actual_yield_portions",
        "replaces_event_id",
        "acknowledge_legacy_tombstones",
    })
    raw_name = require_arg(args, "dish_name")
    name = normalize_dish_name(raw_name)
    occurrence_id = args.get("occurrence_id")
    expected_revision = args.get("expected_revision")
    cooked_at = args.get("cooked_at", UNSET)
    actual_portions = args.get("actual_portions", UNSET)
    actual_yield_portions = args.get("actual_yield_portions", UNSET)
    replaces_event_id = args.get("replaces_event_id")
    acknowledge_legacy_tombstones = args.get("acknowledge_legacy_tombstones")

    validate_meal_occurrence_id(
        occurrence_id, "occurrence_id", optional=True
    )
    validate_cook_event_id(
        replaces_event_id, "replaces_event_id", optional=True
    )
    if occurrence_id is not None and (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 1
    ):
        raise ValueError("expected_revision is required for occurrence_id")
    if replaces_event_id is not None and occurrence_id is None:
        raise ValueError(
            "replaces_event_id requires a linked occurrence_id and expected_revision"
        )
    if acknowledge_legacy_tombstones is not None and replaces_event_id is None:
        raise ValueError(
            "acknowledge_legacy_tombstones requires replaces_event_id"
        )

    for value, label in (
        (actual_portions, "actual_portions"),
        (actual_yield_portions, "actual_yield_portions"),
    ):
        if value is not UNSET and value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ValueError(f"{label} must be a non-negative integer or null")

    # A correction reuses the original cook's side effects and learner
    # observation, so it must not depend on fresh inventory/tuning reads.
    if replaces_event_id is None:
        fridge_snapshot = fridge_repo.load_set()
        days_snapshot = days_since_last_cook()
    else:
        fridge_snapshot = None
        days_snapshot = None

    result = register_cooked(
        dish_name=name,
        occurrence_id=occurrence_id,
        expected_revision=expected_revision,
        cooked_at=cooked_at,
        actual_portions=actual_portions,
        actual_yield_portions=actual_yield_portions,
        replaces_event_id=replaces_event_id,
        acknowledge_legacy_tombstones=acknowledge_legacy_tombstones,
    )
    dishes = result.pop("dishes_snapshot")

    # A metadata correction is not another cook observation. Derived learner
    # state remains a non-critical correlated child update for new cooks only.
    if not result["corrected"]:
        assert fridge_snapshot is not None and days_snapshot is not None
        try:
            with tuning_repo.lock:
                state = tuning_repo.load()
                rewards = tuning.compute_rewards(
                    name, dishes, fridge_snapshot, days_snapshot, state["candidates"]
                )
                if rewards is not None:
                    state = tuning.apply_update(state, rewards)
                    state = tuning.select_deployed(state)
                    tuning_repo.save(state)
        except Exception:
            logger.exception("weight tuning update failed (non-critical)")

    removed_msg = ""
    if result["removed_inventory"]:
        removed_msg = (
            " Removed from fridge: "
            + ", ".join(result["removed_inventory"])
            + "."
        )
    prep_msg = ""
    if result["prep_consumed"]:
        prep_msg = " Consumed prep items: " + ", ".join(result["prep_consumed"]) + "."
    occurrence_msg = ""
    if occurrence_id:
        occurrence_msg = f" Plan occurrence {occurrence_id} marked cooked."
    action = "Corrected" if result["corrected"] else "Registered"
    message = (
        f"{action} '{result['dish']}' as cooked on {result['cooked_on']}."
        f"{occurrence_msg}{removed_msg}{prep_msg}"
    )
    if result["corrected"]:
        return {
            "status": "ok",
            "action": "corrected",
            "message": message,
            "dish": result["dish"],
            "cook_event_id": result["cook_event_id"],
            "plan_occurrence_id": result["plan_occurrence_id"],
            "occurrence_revision": result["occurrence_revision"],
            "cooked_on": result["cooked_on"],
            "corrected": True,
            "replaces_event_id": result["replaces_event_id"],
            "root_event_id": result["root_event_id"],
            "effects_origin_event_id": result["effects_origin_event_id"],
            "request_fingerprint": result["request_fingerprint"],
            "leftover_after": result["leftover_after"],
            "removed_inventory": result["removed_inventory"],
            "prep_consumed": result["prep_consumed"],
            "transaction_id": result["transaction_id"],
            "replayed": result["replayed"],
        }
    return message
