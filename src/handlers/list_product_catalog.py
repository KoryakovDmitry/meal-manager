"""Tool: list_product_catalog — list known and recipe-only products."""

from ..product_catalog import build_product_catalog
from ..repositories import dish_repo, fridge_repo
from ._common import reject_unknown_args, tool_handler

NAME = "list_product_catalog"
SCHEMA = {
    "description": (
        "List the product catalog across current stock, previously stocked products, "
        "and ingredients known only from recipes. Supports status and name filters."
    ),
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": ["all", "in_stock", "out_of_stock", "recipe_only"],
            "default": "all",
        },
        "query": {"type": "string", "maxLength": 200},
    },
    "additionalProperties": False,
}


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    return build_product_catalog(
        fridge_repo.load_catalog_items(),
        dish_repo.load(),
        status=args.get("status", "all"),
        query=args.get("query"),
    )
