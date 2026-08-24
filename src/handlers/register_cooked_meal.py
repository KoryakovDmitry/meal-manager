"""Tool: register_cooked_meal — commit one canonical cooking occurrence."""

import logging

from .. import tuning
from ..cooking import register_cooked
from ..repositories import fridge_repo, tuning_repo
from ._common import (
    days_since_last_cook,
    normalize_dish_name,
    require_arg,
    tool_handler,
)

logger = logging.getLogger(__name__)

NAME = "register_cooked_meal"

SCHEMA = {
    "description": (
        "Register a catalog dish as cooked. When occurrence_id is supplied, "
        "the planned row remains in place and becomes cooked. The operation "
        "also appends canonical cooking history and consumes essentials."
    ),
    "type": "object",
    "properties": {
        "dish_name": {
            "type": "string",
            "description": "exact dish name from the catalog",
        },
        "occurrence_id": {
            "type": "string",
            "description": "optional stable mealocc_* ID from a weekly plan",
        },
        "expected_revision": {
            "type": ["integer", "null"],
            "minimum": 1,
            "description": "required OCC revision when occurrence_id is supplied",
        },
        "cooked_at": {
            "type": "string",
            "description": "optional timezone-aware RFC3339 actual cook time",
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
    },
    "required": ["dish_name"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    raw_name = require_arg(args, "dish_name")
    name = normalize_dish_name(raw_name)
    occurrence_id = args.get("occurrence_id")
    expected_revision = args.get("expected_revision")
    cooked_at = args.get("cooked_at")
    actual_portions = args.get("actual_portions")
    actual_yield_portions = args.get("actual_yield_portions")

    for value, label in (
        (actual_portions, "actual_portions"),
        (actual_yield_portions, "actual_yield_portions"),
    ):
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ValueError(f"{label} must be a non-negative integer or null")

    # Capture the learner decision state before the cook transaction mutates it.
    fridge_snapshot = fridge_repo.load_set()
    days_snapshot = days_since_last_cook()

    result = register_cooked(
        dish_name=name,
        occurrence_id=occurrence_id,
        expected_revision=expected_revision,
        cooked_at=cooked_at,
        actual_portions=actual_portions,
        actual_yield_portions=actual_yield_portions,
    )
    dishes = result.pop("dishes_snapshot")

    # Derived learner state is a non-critical correlated child update. The
    # active native audit scope journals the tuning document replacement.
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
    return (
        f"Registered '{result['dish']}' as cooked on {result['cooked_on']}."
        f"{occurrence_msg}{removed_msg}{prep_msg}"
    )
