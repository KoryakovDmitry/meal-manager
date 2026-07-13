"""Tool: create_week_plan — create an empty draft plan for an ISO week."""

from ..plan import WeekPlan
from ..repositories import plan_repo, prep_repo
from ._common import maybe_parse_json_arg, tool_handler
from ._plan_common import normalize_week_id

NAME = "create_week_plan"

SCHEMA = {
    "description": (
        "Create an empty weekly meal plan with status draft. Uses the current "
        "ISO week when week is omitted. Optionally attach prep-item references."
    ),
    "type": "object",
    "properties": {
        "week": {
            "type": "string",
            "description": "ISO week YYYY-Www; defaults to the current week",
        },
        "prep": {
            "type": "array",
            "items": {"type": "string"},
            "description": "optional prep-item names planned for the week",
        },
    },
    "required": [],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    week_id = normalize_week_id(args.get("week"), default_current=True)
    raw_prep = maybe_parse_json_arg(args.get("prep", []))
    if not isinstance(raw_prep, list) or not all(isinstance(x, str) for x in raw_prep):
        raise ValueError("prep must be an array of prep-item names")

    available = {item.name for item in prep_repo.load()}
    prep = []
    for raw_name in raw_prep:
        name = WeekPlan.normalize_prep_name(raw_name)
        if name not in available:
            raise LookupError(f"prep item '{raw_name}' is not defined")
        if name not in prep:
            prep.append(name)

    with plan_repo.lock:
        if plan_repo.load(week_id) is not None:
            raise ValueError(f"a weekly plan for '{week_id}' already exists")
        plan = WeekPlan(week_id=week_id, prep=prep)
        plan_repo.save(plan)

    return plan.to_dict()
