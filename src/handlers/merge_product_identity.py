"""Tool: merge_product_identity — absorb an unavailable duplicate identity."""

from ..product_identity import merge_product_identity
from ..repositories import fridge_repo, shopping_request_repo
from ._common import reject_unknown_args, require_arg, tool_handler

NAME = "merge_product_identity"
SCHEMA = {
    "description": (
        "Atomically merge one out-of-stock duplicate product identity into an "
        "in-stock target identity, preserving the target and transferring source aliases."
    ),
    "type": "object",
    "properties": {
        "source_item_id": {"type": "string", "minLength": 1, "maxLength": 100},
        "target_item_id": {"type": "string", "minLength": 1, "maxLength": 100},
        "expected_source_updated_at": {
            "type": "string", "minLength": 1, "maxLength": 100,
        },
        "expected_target_updated_at": {
            "type": "string", "minLength": 1, "maxLength": 100,
        },
    },
    "required": [
        "source_item_id",
        "target_item_id",
        "expected_source_updated_at",
        "expected_target_updated_at",
    ],
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    return merge_product_identity(
        fridge_repo=fridge_repo,
        shopping_request_repo=shopping_request_repo,
        source_item_id=require_arg(args, "source_item_id"),
        target_item_id=require_arg(args, "target_item_id"),
        expected_source_updated_at=require_arg(args, "expected_source_updated_at"),
        expected_target_updated_at=require_arg(args, "expected_target_updated_at"),
    )
