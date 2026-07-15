"""Tool: get_week_plan — return one weekly plan with a live shopping projection."""

from ..repositories import dish_repo, fridge_repo, prep_repo, shopping_request_repo
from ..shopping import project_plan_shopping
from ._common import tool_handler
from ._plan_common import normalize_week_id, require_plan

NAME = "get_week_plan"

SCHEMA = {
    "description": (
        "Return the meal plan for an ISO week. Defaults to the current week. "
        "current_shopping is recomputed from live recipes, prep and inventory."
    ),
    "type": "object",
    "properties": {
        "week": {
            "type": "string",
            "description": "ISO week YYYY-Www; defaults to the current week",
        },
    },
    "required": [],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    week_id = normalize_week_id(args.get("week"), default_current=True)
    plan = require_plan(week_id)
    catalog = fridge_repo.load_catalog_items()
    projection_error = None
    try:
        current, shopping_stale = project_plan_shopping(
            plan=plan,
            dishes=dish_repo.load_strict(),
            prep_items=prep_repo.load_strict(),
            catalog_items=catalog,
            manual_requests=shopping_request_repo.load(week=week_id),
        )
    except (LookupError, ValueError) as exc:
        current = {"items": []}
        shopping_stale = True
        projection_error = str(exc)

    payload = plan.to_dict()
    payload["current_shopping"] = current
    payload["shopping_stale"] = shopping_stale
    payload["shopping_projection_error"] = projection_error
    return payload
