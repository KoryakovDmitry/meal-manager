"""Tool: receive one shopping item into exact structured inventory."""

from ..repositories import (
    dish_repo,
    fridge_repo,
    plan_repo,
    prep_repo,
    shopping_request_repo,
)
from ..shopping import build_current_shopping, persistable_shopping
from ._common import normalize_ingredient_name, reject_unknown_args, require_arg, tool_handler
from ._plan_common import normalize_week_id

NAME = "receive_shopping_item"
_NULLABLE_STRING = {"oneOf": [{"type": "string"}, {"type": "null"}]}
SCHEMA = {
    "description": (
        "Confirm a purchased shopping item. Refines its generic requested name "
        "to an exact product, keeps the generic name as an alias, adds the product "
        "to kitchen inventory, and only then removes the shopping request."
    ),
    "type": "object",
    "properties": {
        "week": {"type": "string", "description": "ISO week YYYY-Www; defaults to current week"},
        "shopping_item_id": {"type": "string", "maxLength": 100},
        "exact_name": {"type": "string", "maxLength": 200},
        "category": {"type": "string", "enum": ["product", "prep", "ready_meal"]},
        "quantity": {"oneOf": [{"type": "number"}, {"type": "string"}, {"type": "null"}]},
        "unit": {"oneOf": [{"type": "string", "enum": ["g", "kg", "ml", "l", "pcs", "pack", "can", "jar", "bottle", "portion"]}, {"type": "null"}]},
        "package_count": {"oneOf": [{"type": "integer", "minimum": 1, "maximum": 10000}, {"type": "null"}]},
        "storage": {"oneOf": [{"type": "string", "enum": ["fridge", "freezer", "pantry", "counter"]}, {"type": "null"}]},
        "expires_on": _NULLABLE_STRING,
        "comment": {"oneOf": [{"type": "string", "maxLength": 1000}, {"type": "null"}]},
    },
    "required": ["shopping_item_id", "exact_name"],
    "additionalProperties": False,
}


def _receive_under_request_lock(args: dict):
    reject_unknown_args(args, set(SCHEMA["properties"]))
    week_id = normalize_week_id(args.get("week"), default_current=True)
    item_id = require_arg(args, "shopping_item_id")
    exact_name = normalize_ingredient_name(require_arg(args, "exact_name"))
    metadata = {
        key: args[key]
        for key in (
            "category", "quantity", "unit", "package_count", "storage",
            "expires_on", "comment",
        )
        if key in args
    }

    tracked_id = item_id.startswith(("shopreq_", "shop_"))
    manual = shopping_request_repo.get(item_id) if tracked_id else None
    completion = shopping_request_repo.get_completion(item_id) if tracked_id else None
    if completion is not None:
        if completion.week != week_id or completion.exact_name != exact_name:
            raise ValueError("shopping receipt replay conflicts with completed request")
        product = next((
            item for item in fridge_repo.load_catalog_items()
            if item.id == completion.product_id
        ), None)
        if product is None:
            raise LookupError("completed shopping product is missing from inventory catalog")
        return {
            "status": "already_received",
            "requested_name": completion.requested_name,
            "product": product.to_public_dict(),
            "generic_alias_preserved": True,
            "shopping_item_removed": True,
            "plan_shopping_refreshed": False,
            "projection_warning": None,
        }
    if manual is not None and manual.week != week_id:
        raise LookupError("shopping item does not belong to the requested week")

    with plan_repo.lock:
        plan = plan_repo.load_strict(week_id)
        if manual is not None:
            requested_name = manual.requested_name
        else:
            if plan is None:
                raise LookupError(f"plan '{week_id}' does not exist")
            current_before = build_current_shopping(
                plan=plan,
                dishes=dish_repo.load_strict(),
                prep_items=prep_repo.load_strict(),
                catalog_items=fridge_repo.load_catalog_items(),
                manual_requests=shopping_request_repo.load(week=week_id),
            )
            target = next((
                item for item in current_before["items"] if item["id"] == item_id
            ), None)
            if target is None:
                raise LookupError("shopping item is not currently required")
            requested_name = target["ingredient"]

        receipt_record = shopping_request_repo.reserve_receipt(
            item_id,
            week=week_id,
            requested_name=requested_name,
            exact_name=exact_name,
        )
        if receipt_record.pending_exact_name != exact_name:
            raise ValueError("shopping receipt conflicts with reserved exact product")

        product = fridge_repo.receive_product(
            requested_name=requested_name,
            exact_name=exact_name,
            **metadata,
        )

        completed = shopping_request_repo.complete(
            item_id,
            product_id=product.id,
            exact_name=product.name,
        )
        request_removed = completed is not None and not completed.is_active

        plan_refreshed = False
        projection_warning = None
        if plan is not None:
            try:
                current_after = build_current_shopping(
                    plan=plan,
                    dishes=dish_repo.load_strict(),
                    prep_items=prep_repo.load_strict(),
                    catalog_items=fridge_repo.load_catalog_items(),
                    manual_requests=shopping_request_repo.load(week=week_id),
                )
                plan.shopping = persistable_shopping(current_after)
                plan_repo.save(plan)
                plan_refreshed = True
            except (LookupError, ValueError) as exc:
                projection_warning = str(exc)

    return {
        "status": "received",
        "requested_name": requested_name,
        "product": product.to_public_dict(),
        "generic_alias_preserved": requested_name in product.aliases or requested_name == product.name,
        "shopping_item_removed": request_removed,
        "plan_shopping_refreshed": plan_refreshed,
        "projection_warning": projection_warning,
    }


@tool_handler(NAME)
def HANDLER(args: dict, **kwargs):
    # Serialize completion lookup, exact-product mutation, and tombstone write.
    # JsonFileLock is re-entrant, so repository calls below safely re-acquire it.
    with shopping_request_repo.lock:
        return _receive_under_request_lock(args)
