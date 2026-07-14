"""Pure product-catalog projection over inventory identities and recipes."""

from collections import Counter

from .dish import Dish
from .inventory import InventoryItem

CATALOG_STATUSES = frozenset({"all", "in_stock", "out_of_stock", "recipe_only"})
CATALOG_CATEGORIES = frozenset({"all", "product", "prep", "ready_meal"})


def build_product_catalog(
    items: list[InventoryItem],
    dishes: list[Dish],
    *,
    status: str = "all",
    category: str = "all",
    query: str | None = None,
) -> list[dict]:
    if status not in CATALOG_STATUSES:
        raise ValueError(f"Unsupported product catalog status: {status}")
    if category not in CATALOG_CATEGORIES:
        raise ValueError(f"Unsupported product catalog category: {category}")
    if query is not None and not isinstance(query, str):
        raise ValueError("query must be a string")
    if query is not None and len(query) > 200:
        raise ValueError("query too long (max 200 chars)")
    normalized_query = (query or "").strip().lower()

    recipe_counts = Counter(
        ingredient
        for dish in dishes
        for ingredient in dish.ingredients
    )
    rows: list[dict] = []
    stocked_names: set[str] = set()

    for item in items:
        stocked_names.add(item.name)
        if not item.available and not item.ever_stocked and item.name not in recipe_counts:
            continue
        item_status = (
            "in_stock"
            if item.available
            else "out_of_stock"
            if item.ever_stocked
            else "recipe_only"
        )
        rows.append(item.to_public_dict() | {
            "available": item.available,
            "status": item_status,
            "recipe_count": recipe_counts.get(item.name, 0),
            "in_recipes": recipe_counts.get(item.name, 0) > 0,
        })

    for name, recipe_count in recipe_counts.items():
        if name in stocked_names:
            continue
        rows.append({
            "id": None,
            "name": name,
            "available": False,
            "quantity": None,
            "unit": None,
            "package_count": None,
            "storage": None,
            "expires_on": None,
            "comment": None,
            "created_at": None,
            "updated_at": None,
            "category": "product",
            "expiry_status": "unknown",
            "status": "recipe_only",
            "recipe_count": recipe_count,
            "in_recipes": True,
        })

    return sorted(
        (
            row for row in rows
            if (status == "all" or row["status"] == status)
            and (category == "all" or row["category"] == category)
            and (not normalized_query or normalized_query in row["name"])
        ),
        key=lambda row: row["name"],
    )
