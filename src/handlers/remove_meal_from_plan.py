"""Tool: remove_meal_from_plan — compatibility command that cancels one meal occurrence."""

import json

from ..audit import audit_manager
from ..repositories import plan_repo
from ._common import require_arg, tool_handler
from ._plan_common import normalize_day, normalize_week_id, require_plan

NAME = "remove_meal_from_plan"

SCHEMA = {
    "description": (
        "Cancel one planned meal occurrence by stable occurrence_id and expected_revision. "
        "The row remains in the plan. day+meal_index remains a legacy compatibility selector."
    ),
    "type": "object",
    "properties": {
        "week": {"type": "string", "description": "ISO week YYYY-Www"},
        "day": {
            "type": "string",
            "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        },
        "meal_index": {
            "type": "integer",
            "minimum": 0,
            "description": "legacy zero-based meal index within the day",
        },
        "occurrence_id": {
            "type": "string",
            "description": "stable meal occurrence ID from get_week_plan",
        },
        "expected_revision": {
            "type": "integer",
            "minimum": 1,
            "description": "required OCC revision when occurrence_id is used",
        },
    },
    "required": ["week"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    week_id = normalize_week_id(require_arg(args, "week"))
    occurrence_id = args.get("occurrence_id")
    stable_mode = occurrence_id is not None
    if stable_mode:
        if not isinstance(occurrence_id, str) or not occurrence_id.startswith("mealocc_"):
            raise ValueError("occurrence_id must start with mealocc_")
        expected_revision = require_arg(args, "expected_revision")
        if (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 1
        ):
            raise ValueError("expected_revision must be a positive integer")
        if "day" in args or "meal_index" in args:
            raise ValueError("use occurrence_id or day+meal_index, not both")
        day = None
        index = None
    else:
        day = normalize_day(require_arg(args, "day"))
        index = require_arg(args, "meal_index")
        expected_revision = None
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise ValueError("meal_index must be a non-negative integer")

    with audit_manager.lock:
        audit_manager.recover()
        with plan_repo.lock:
            plan = require_plan(week_id)
            if plan.status == "archived":
                raise ValueError("archived plans cannot be edited")
            if stable_mode:
                matches = [
                    (candidate_day, meal)
                    for candidate_day, day_plan in plan.days.items()
                    for meal in day_plan.meals
                    if meal.occurrence_id == occurrence_id
                ]
                if len(matches) != 1:
                    raise LookupError(f"meal occurrence '{occurrence_id}' not found uniquely")
                day, cancelled = matches[0]
            else:
                assert day is not None and index is not None
                meals = plan.days[day].meals
                if index >= len(meals):
                    raise LookupError(
                        f"meal_index {index} is out of range for {day} ({len(meals)} meals)"
                    )
                cancelled = meals[index]
            cancelled.transition_to("cancelled", expected_revision=expected_revision)
            plan.shopping = {}
            after = json.dumps(
                plan.to_dict(), ensure_ascii=False, indent=2
            ).encode("utf-8")
            try:
                transaction = audit_manager.commit(
                    operation=NAME,
                    targets={f"plans/{week_id}.json": after},
                    events=[{
                    "event_type": "plan.meal.cancelled.v1",
                    "entity": {
                        "type": "meal_occurrence",
                        "id": cancelled.occurrence_id,
                    },
                    "payload": {
                        "week": week_id,
                        "day": day,
                        "dish": cancelled.dish,
                        "portions_planned": cancelled.portions_planned,
                        "revision": cancelled.revision,
                    },
                }],
                context={
                    "actor": {"type": "agent"},
                    "surface": {"kind": "native_tool", "operation": NAME},
                },
            )
            except Exception:
                transaction = audit_manager.resolve_last_transaction()
                if transaction is None:
                    raise

    return {
        "week": week_id,
        "day": day,
        "cancelled": cancelled.to_dict(),
        "remaining_planned_meals": sum(
            meal.status == "planned" for meal in plan.days[day].meals
        ),
    }
