"""Tool: list_audit_events — query committed domain audit events."""

from ..audit import audit_manager
from ._common import tool_handler

NAME = "list_audit_events"

SCHEMA = {
    "description": (
        "Query committed append-only domain audit events by entity, event type, "
        "or time. Returns newest events first."
    ),
    "type": "object",
    "properties": {
        "entity_type": {"type": "string", "description": "optional entity type"},
        "entity_id": {"type": "string", "description": "optional stable entity ID"},
        "event_type": {"type": "string", "description": "optional exact event type"},
        "since": {
            "type": "string",
            "description": "optional timezone-aware RFC3339 lower bound",
        },
        "until": {"type": "string", "description": "optional RFC3339 upper bound"},
        "actor_type": {"type": "string", "description": "optional actor type"},
        "surface_kind": {"type": "string", "description": "optional surface kind"},
        "operation": {"type": "string", "description": "optional operation name"},
        "operation_id": {"type": "string", "description": "optional transaction/operation ID"},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "description": "maximum events; defaults 100",
        },
    },
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    return audit_manager.list_events(
        entity_type=args.get("entity_type"),
        entity_id=args.get("entity_id"),
        event_type=args.get("event_type"),
        since=args.get("since"),
        until=args.get("until"),
        actor_type=args.get("actor_type"),
        surface_kind=args.get("surface_kind"),
        operation=args.get("operation"),
        operation_id=args.get("operation_id"),
        limit=args.get("limit", 100),
    )
