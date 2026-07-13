"""Tool: list_week_plans — list weekly-plan history."""

from ..repositories import plan_repo
from ._common import tool_handler

NAME = "list_week_plans"

SCHEMA = {
    "description": (
        "List all stored weekly meal plans, newest first, with status and counts. "
        "Use to browse history before repeating a previous week."
    ),
    "type": "object",
    "properties": {},
    "required": [],
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    return plan_repo.list_weeks()
