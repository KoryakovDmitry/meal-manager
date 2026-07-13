"""Tool: set_plan_status — advance a weekly plan through its lifecycle."""

from ..plan import VALID_STATUSES
from ..repositories import plan_repo
from ._common import require_arg, tool_handler
from ._plan_common import normalize_week_id, require_plan

NAME = "set_plan_status"

_NEXT_STATUS = {
    "draft": "approved",
    "approved": "active",
    "active": "archived",
}

SCHEMA = {
    "description": (
        "Advance a weekly plan through draft → approved → active → archived. "
        "Transitions cannot skip stages or move backwards."
    ),
    "type": "object",
    "properties": {
        "week": {"type": "string", "description": "ISO week YYYY-Www"},
        "status": {"type": "string", "enum": list(VALID_STATUSES)},
    },
    "required": ["week", "status"],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    week_id = normalize_week_id(require_arg(args, "week"))
    status = require_arg(args, "status")
    if status not in VALID_STATUSES:
        raise ValueError("status must be one of: " + ", ".join(VALID_STATUSES))

    with plan_repo.lock:
        plan = require_plan(week_id)
        previous = plan.status
        if status == previous:
            return {
                "week": week_id,
                "status": status,
                "no_change": True,
            }
        expected = _NEXT_STATUS.get(previous)
        if status != expected:
            raise ValueError(
                f"invalid status transition: {previous} → {status}; "
                f"next allowed status is {expected or 'none'}"
            )
        plan.status = status
        plan_repo.save(plan)

    return {"week": week_id, "previous_status": previous, "status": status}
