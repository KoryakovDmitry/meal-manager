"""Current shopping projections shared by native and Web readers."""

import hashlib

from .plan_shopping import build_plan_shopping_list
from .suggestion import (
    DEFAULT_MATCH_WEIGHT,
    DEFAULT_TIME_WEIGHT,
    RECENCY_CAP_DAYS,
    calculate_score,
)

_BASE_KEYS = (
    "basis",
    "items",
    "covered_by_fridge",
    "prep_to_make",
    "unresolved_prep_dependencies",
    "prep_capacity_warnings",
)
_ITEM_BASE_KEYS = {
    "ingredient", "required_uses", "available_uses", "to_buy", "required_by"
}


def _base_item(item: dict) -> dict:
    return {key: item[key] for key in _ITEM_BASE_KEYS if key in item}


def shopping_item_id(week_id: str, ingredient: str) -> str:
    digest = hashlib.sha256(
        f"{week_id}\0{ingredient}".encode("utf-8")
    ).hexdigest()[:24]
    return "shop_" + digest


def inventory_identity_for(catalog_items, ingredient: str):
    return next((
        item for item in catalog_items
        if ingredient == item.name or ingredient in item.aliases
    ), None)


def classify_shopping_items(*, week_id: str, items: list, catalog_items) -> list:
    classified = []
    for raw in items:
        ingredient = raw["ingredient"]
        identity = inventory_identity_for(catalog_items, ingredient)
        known = identity is not None and identity.ever_stocked
        classified.append(dict(raw) | {
            "id": shopping_item_id(week_id, ingredient),
            "kind": "known_missing" if known else "abstract_request",
            "product_id": identity.id if identity is not None else None,
        })
    return classified


def build_current_shopping(
    *, plan, dishes, prep_items, catalog_items, manual_requests=()
) -> dict:
    available_names = {
        name
        for item in catalog_items
        if item.available
        for name in (item.name, *item.aliases)
    }
    fresh = build_plan_shopping_list(
        plan=plan,
        dishes=dishes,
        prep_items=prep_items,
        fridge=available_names,
    )
    items = classify_shopping_items(
        week_id=plan.week_id,
        items=fresh["items"],
        catalog_items=catalog_items,
    )
    items = merge_manual_requests(items, list(manual_requests))
    return dict(fresh) | {"items": items}


def manual_request_item(request) -> dict:
    return {
        "id": request.id,
        "ingredient": request.requested_name,
        "kind": "abstract_request",
        "product_id": None,
        "required_uses": 1,
        "available_uses": 0,
        "to_buy": 1,
        "required_by": [{
            "kind": "manual",
            "name": "ручная покупка",
            "uses": 1,
        }],
    }


def merge_manual_requests(items: list, requests: list) -> list:
    merged = list(items)
    seen_ids = {item.get("id") for item in merged}
    for request in requests:
        if request.id not in seen_ids:
            merged.append(manual_request_item(request))
            seen_ids.add(request.id)
    return merged


def persistable_shopping(shopping: dict) -> dict:
    persisted = dict(shopping)
    persisted["items"] = [
        {key: value for key, value in item.items() if key not in {"id", "kind", "product_id"}}
        for item in shopping.get("items", [])
    ]
    return persisted


def persisted_shopping_is_current(persisted: dict, current: dict) -> bool:
    if not isinstance(persisted, dict) or not persisted:
        return False
    for key in _BASE_KEYS:
        current_value = current.get(key)
        persisted_value = persisted.get(key)
        if key == "items":
            current_value = [_base_item(item) for item in current_value or []]
            persisted_value = [_base_item(item) for item in persisted_value or []]
        if persisted_value != current_value:
            return False
    return True


def project_plan_shopping(
    *, plan, dishes, prep_items, catalog_items, manual_requests=()
) -> tuple[dict, bool]:
    current = build_current_shopping(
        plan=plan,
        dishes=dishes,
        prep_items=prep_items,
        catalog_items=catalog_items,
        manual_requests=manual_requests,
    )
    is_current = persisted_shopping_is_current(plan.shopping, current)
    if is_current:
        projected = dict(plan.shopping)
        projected["items"] = current["items"]
        return projected, False
    return current, bool(plan.shopping)


def suggest_quick_shopping(
    dishes,
    available_ingredients,
    days_since_last,
    match_weight=DEFAULT_MATCH_WEIGHT,
    time_weight=DEFAULT_TIME_WEIGHT,
):
    best_by_ingredient = {}
    for dish in dishes:
        missing_essentials = [
            ingredient
            for ingredient, is_essential in dish.ingredients.items()
            if is_essential and ingredient not in available_ingredients
        ]
        if len(missing_essentials) != 1:
            continue
        missing_ingredient = missing_essentials[0]
        simulated = available_ingredients | {missing_ingredient}
        days = days_since_last.get(dish.name, RECENCY_CAP_DAYS)
        score = calculate_score(
            dish,
            simulated,
            days,
            match_weight=match_weight,
            time_weight=time_weight,
        )
        if score <= 0:
            continue
        data = best_by_ingredient.setdefault(
            missing_ingredient, {"dishes": set(), "max_score": 0}
        )
        data["dishes"].add(dish.name)
        data["max_score"] = max(data["max_score"], score)
    result = [
        (ingredient, ", ".join(sorted(data["dishes"])), data["max_score"])
        for ingredient, data in best_by_ingredient.items()
    ]
    result.sort(key=lambda value: value[2], reverse=True)
    return result
