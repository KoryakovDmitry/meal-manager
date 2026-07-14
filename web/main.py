#!/usr/bin/env python3
"""Meal Planning Web Interface — FastAPI backend.

Reads/writes the same JSON files used by the Hermes meal_manager plugin,
ensuring full synchronization between the Telegram bot, agent, and web UI.
"""

import importlib
import json
import logging
import re
import sys
import threading
from functools import wraps
from datetime import date, datetime, timedelta
from math import isfinite
from pathlib import Path
from collections import Counter
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, StrictInt

# ─── Configuration ──────────────────────────────────────────────────────
# Resolve data dir: allow override via MEAL_DATA_DIR env var,
# otherwise default to the plugin's data/ directory.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT.parent))
_inventory_repository_module = importlib.import_module(
    f"{PLUGIN_ROOT.name}.src.repositories.json_fridge"
)
_dish_module = importlib.import_module(f"{PLUGIN_ROOT.name}.src.dish")
_product_catalog_module = importlib.import_module(
    f"{PLUGIN_ROOT.name}.src.product_catalog"
)
JsonFridgeRepository = _inventory_repository_module.JsonFridgeRepository
InventoryDataError = _inventory_repository_module.InventoryDataError
InventoryConflictError = _inventory_repository_module.InventoryConflictError
Dish = _dish_module.Dish
build_product_catalog = _product_catalog_module.build_product_catalog
logger = logging.getLogger(__name__)
DATA_DIR = Path(__import__("os").environ.get("MEAL_DATA_DIR", PLUGIN_ROOT / "data"))
DISHES_PATH = DATA_DIR / "dishes.json"
FRIDGE_PATH = DATA_DIR / "fridge.json"
HISTORY_PATH = DATA_DIR / "history.json"
TUNING_PATH = DATA_DIR / "tuning.json"
PLANS_DIR = DATA_DIR / "plans"
_structured_fridge_repo = JsonFridgeRepository(FRIDGE_PATH)

_WEEK_ID_RE = re.compile(r"^\d{4}-W\d{2}$")
_PLAN_DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}

RECENCY_COOLDOWN_DAYS = 2

app = FastAPI(title="Meal Planning", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = threading.Lock()

# ─── Data access ────────────────────────────────────────────────────────
def _read_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError):
        return default

def _write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(path)

def load_dishes():
    data = _read_json(DISHES_PATH, {"dishes": []})
    return data.get("dishes", [])

def save_dishes(dishes):
    _write_json(DISHES_PATH, {"dishes": dishes})

def _fridge_repository():
    _structured_fridge_repo.path = Path(FRIDGE_PATH)
    return _structured_fridge_repo


def load_fridge():
    return _fridge_repository().load()

def save_fridge(items):
    _fridge_repository().save(items)

def load_history():
    data = _read_json(HISTORY_PATH, {"history": []})
    return data.get("history", [])

def save_history(entries):
    _write_json(HISTORY_PATH, {"history": entries})

def load_tuning():
    return _read_json(TUNING_PATH, {})

def _valid_iso_week(week_id: str) -> bool:
    match = _WEEK_ID_RE.fullmatch(week_id)
    if not match:
        return False
    try:
        date.fromisocalendar(int(week_id[:4]), int(week_id[-2:]), 1)
    except ValueError:
        return False
    return True

_MAX_SAFE_JS_INTEGER = 9_007_199_254_740_991


def _valid_nonnegative_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return 0 <= value <= _MAX_SAFE_JS_INTEGER
    return isfinite(value) and 0 <= value <= _MAX_SAFE_JS_INTEGER


def _valid_positive_number(value) -> bool:
    return _valid_nonnegative_number(value) and value > 0


def _valid_string_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _valid_canonical_name(value) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip().lower()
    )


def _valid_shopping_item(item, *, allow_zero=False, require_price=False) -> bool:
    base_keys = {
        "ingredient", "required_uses", "available_uses", "to_buy", "required_by",
    }
    price_keys = {"estimated_unit_price", "estimated_cost"}
    if (
        not isinstance(item, dict)
        or frozenset(item) not in {frozenset(base_keys), frozenset(base_keys | price_keys)}
    ):
        return False
    ingredient = item.get("ingredient")
    required = item.get("required_uses")
    available = item.get("available_uses")
    to_buy = item.get("to_buy")
    if not _valid_canonical_name(ingredient):
        return False
    for value, minimum in ((required, 1), (available, 0), (to_buy, 0)):
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not minimum <= value <= _MAX_SAFE_JS_INTEGER
        ):
            return False
    assert isinstance(required, int) and not isinstance(required, bool)
    assert isinstance(available, int) and not isinstance(available, bool)
    assert isinstance(to_buy, int) and not isinstance(to_buy, bool)
    if to_buy != max(0, required - available) or (allow_zero != (to_buy == 0)):
        return False
    required_by = item.get("required_by")
    if not isinstance(required_by, list) or not required_by:
        return False
    source_uses = 0
    for source in required_by:
        if (
            not isinstance(source, dict)
            or set(source) != {"kind", "name", "uses"}
            or source.get("kind") not in {"dish", "prep"}
            or not _valid_canonical_name(source.get("name"))
        ):
            return False
        uses = source.get("uses")
        if not isinstance(uses, int) or isinstance(uses, bool) or not 1 <= uses <= _MAX_SAFE_JS_INTEGER:
            return False
        source_uses += uses
    if source_uses != required:
        return False
    has_unit = "estimated_unit_price" in item
    has_cost = "estimated_cost" in item
    if has_unit != has_cost:
        return False
    if allow_zero and has_unit:
        return False
    if has_unit:
        unit = item["estimated_unit_price"]
        cost = item["estimated_cost"]
        if not _valid_positive_number(unit) or not _valid_nonnegative_number(cost):
            return False
        raw_cost = unit * to_buy
        if not isfinite(raw_cost) or raw_cost > _MAX_SAFE_JS_INTEGER or round(raw_cost, 2) != cost:
            return False
    return not require_price or has_cost


def _valid_prep_schedule(value, *, capacity=False) -> bool:
    if not isinstance(value, list):
        return False
    expected_keys = {
        "prep_item", "required_uses", "available_uses",
        "projected_uses", "planned_explicitly",
    }
    for item in value:
        if (
            not isinstance(item, dict)
            or set(item) != expected_keys
            or not _valid_canonical_name(item.get("prep_item"))
        ):
            return False
        values = []
        for key, minimum in (
            ("required_uses", 0), ("available_uses", 0), ("projected_uses", 1)
        ):
            number = item.get(key)
            if (
                not isinstance(number, int)
                or isinstance(number, bool)
                or not minimum <= number <= _MAX_SAFE_JS_INTEGER
            ):
                return False
            values.append(number)
        if not isinstance(item.get("planned_explicitly"), bool):
            return False
        if capacity and values[2] >= values[0]:
            return False
    return True


def _valid_shopping_payload(shopping) -> bool:
    if not isinstance(shopping, dict):
        return False
    if not shopping:
        return True
    if shopping.get("basis") != "cooking_occurrences":
        return False
    allowed_keys = {
        "basis", "items", "covered_by_fridge", "prep_to_make",
        "unresolved_prep_dependencies", "prep_capacity_warnings",
        "estimated_cost", "complete", "priced_items", "total_items",
        "unpriced_items", "weekly_limit", "weekly_budget_status", "warning",
        "trips", "unpriced_trip_items", "trip_limit", "trip_warnings",
    }
    if set(shopping) - allowed_keys:
        return False
    base_lists = (
        "items", "covered_by_fridge", "prep_to_make",
        "unresolved_prep_dependencies", "prep_capacity_warnings",
    )
    if not all(isinstance(shopping.get(key), list) for key in base_lists):
        return False
    items = shopping["items"]
    covered = shopping["covered_by_fridge"]
    if not all(_valid_shopping_item(item) for item in items):
        return False
    if not all(_valid_shopping_item(item, allow_zero=True) for item in covered):
        return False
    names = [item["ingredient"] for item in items + covered]
    if len(names) != len(set(names)):
        return False
    if not _valid_prep_schedule(shopping["prep_to_make"]):
        return False
    prep_names = [item["prep_item"] for item in shopping["prep_to_make"]]
    if len(prep_names) != len(set(prep_names)):
        return False
    if not _valid_prep_schedule(shopping["prep_capacity_warnings"], capacity=True):
        return False
    expected_capacity = [
        item for item in shopping["prep_to_make"]
        if item["projected_uses"] < item["required_uses"]
    ]
    if shopping["prep_capacity_warnings"] != expected_capacity:
        return False
    unresolved_names = []
    for item in shopping["unresolved_prep_dependencies"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"prep_item", "required_uses", "reason"}
            or not _valid_canonical_name(item.get("prep_item"))
            or item.get("reason") != "not_defined"
        ):
            return False
        uses = item.get("required_uses")
        if not isinstance(uses, int) or isinstance(uses, bool) or not 1 <= uses <= _MAX_SAFE_JS_INTEGER:
            return False
        unresolved_names.append(item["prep_item"])
    if len(unresolved_names) != len(set(unresolved_names)):
        return False

    estimate_keys = {
        "estimated_cost", "complete", "priced_items", "total_items",
        "unpriced_items", "weekly_limit", "weekly_budget_status", "warning",
    }
    estimate_present = estimate_keys.intersection(shopping)
    if estimate_present and estimate_present != estimate_keys:
        return False
    has_item_pricing = any("estimated_cost" in item for item in items)
    if has_item_pricing and not estimate_present:
        return False
    if estimate_present:
        if not _valid_nonnegative_number(shopping["estimated_cost"]):
            return False
        if not _valid_positive_number(shopping["weekly_limit"]):
            return False
        priced_names = sorted(item["ingredient"] for item in items if "estimated_cost" in item)
        unpriced_names = sorted(item["ingredient"] for item in items if "estimated_cost" not in item)
        calculated = round(sum(item.get("estimated_cost", 0) for item in items), 2)
        if not _valid_nonnegative_number(calculated) or calculated != shopping["estimated_cost"]:
            return False
        for key in ("priced_items", "total_items"):
            value = shopping[key]
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 0 <= value <= _MAX_SAFE_JS_INTEGER
            ):
                return False
        if shopping["priced_items"] != len(priced_names) or shopping["total_items"] != len(items):
            return False
        if not _valid_string_list(shopping["unpriced_items"]):
            return False
        if shopping["unpriced_items"] != unpriced_names:
            return False
        complete = shopping["complete"]
        if not isinstance(complete, bool) or complete != (not unpriced_names):
            return False
        expected_status = "unknown" if unpriced_names else (
            "over" if shopping["estimated_cost"] > shopping["weekly_limit"] else "within"
        )
        if shopping["weekly_budget_status"] != expected_status:
            return False
        warning = shopping["warning"]
        expected_warning = None
        if expected_status == "unknown":
            expected_warning = "Cost estimate is incomplete; weekly budget status is unknown."
        elif expected_status == "over":
            expected_warning = (
                f"Estimated weekly cost exceeds the soft €{shopping['weekly_limit']:.2f} limit."
            )
        if warning != expected_warning:
            return False

    trip_keys = {"trips", "unpriced_trip_items", "trip_limit", "trip_warnings"}
    trip_present = trip_keys.intersection(shopping)
    if trip_present and trip_present != trip_keys:
        return False
    if trip_present and not estimate_present:
        return False
    if trip_present:
        if not isinstance(shopping["trips"], list) or not _valid_positive_number(shopping["trip_limit"]):
            return False
        assigned = []
        top_items = {item["ingredient"]: item for item in items}
        for index, trip in enumerate(shopping["trips"], 1):
            if (
                not isinstance(trip, dict)
                or set(trip) != {"trip", "items", "estimated_cost", "limit", "over_limit"}
                or trip.get("trip") != index
                or trip.get("limit") != shopping["trip_limit"]
                or not isinstance(trip.get("items"), list)
                or not trip["items"]
                or not isinstance(trip.get("over_limit"), bool)
            ):
                return False
            if not all(_valid_shopping_item(item, require_price=True) for item in trip["items"]):
                return False
            if any(top_items.get(item["ingredient"]) != item for item in trip["items"]):
                return False
            calculated = round(sum(item["estimated_cost"] for item in trip["items"]), 2)
            if not _valid_nonnegative_number(trip.get("estimated_cost")):
                return False
            if calculated != trip["estimated_cost"]:
                return False
            if trip["over_limit"] != (trip["estimated_cost"] > trip["limit"]):
                return False
            assigned.extend(item["ingredient"] for item in trip["items"])
        priced_names = sorted(item["ingredient"] for item in items if "estimated_cost" in item)
        unpriced_names = sorted(item["ingredient"] for item in items if "estimated_cost" not in item)
        if not _valid_string_list(shopping["unpriced_trip_items"]):
            return False
        if sorted(assigned) != priced_names or shopping["unpriced_trip_items"] != unpriced_names:
            return False
        expected_warnings = []
        if unpriced_names:
            expected_warnings.append("Unpriced items are not assigned to cost-limited trips.")
        if any(trip["over_limit"] for trip in shopping["trips"]):
            expected_warnings.append("At least one individual item exceeds the soft trip limit.")
        if shopping["trip_warnings"] != expected_warnings:
            return False
    return True


def _valid_plan_payload(plan, expected_week: str) -> bool:
    if not isinstance(plan, dict) or plan.get("week") != expected_week:
        return False
    status = plan.get("status", "draft")
    if not isinstance(status, str) or status not in {"draft", "approved", "active", "archived"}:
        return False
    prep = plan.get("prep", [])
    days = plan.get("days", {})
    leftovers = plan.get("leftovers", {})
    shopping = plan.get("shopping", {})
    if not isinstance(prep, list) or not all(isinstance(item, str) for item in prep):
        return False
    if not _valid_shopping_payload(shopping):
        return False
    if (
        not isinstance(days, dict)
        or set(days) != _PLAN_DAYS
        or not isinstance(leftovers, dict)
    ):
        return False
    for day in days.values():
        if not isinstance(day, dict):
            return False
        meals = day.get("meals", [])
        if not isinstance(meals, list) or not isinstance(day.get("note", ""), str):
            return False
        for meal in meals:
            if (
                not isinstance(meal, dict)
                or not isinstance(meal.get("dish"), str)
                or not meal["dish"].strip()
            ):
                return False
            portions = meal.get("portions", 2)
            if not isinstance(portions, int) or isinstance(portions, bool) or portions < 1:
                return False
    return True

def _read_valid_week_plan(week_id: str):
    if not _valid_iso_week(week_id):
        return None
    plan = _read_json(PLANS_DIR / f"{week_id}.json", None)
    return plan if _valid_plan_payload(plan, week_id) else None

def load_week_plan(week_id: str):
    if not _valid_iso_week(week_id):
        raise HTTPException(400, "Invalid ISO week; expected a real YYYY-Www week")
    plan = _read_valid_week_plan(week_id)
    if plan is None:
        raise HTTPException(404, f"Plan '{week_id}' not found or malformed")
    return plan

def list_week_plans():
    if not PLANS_DIR.exists():
        return []
    result = []
    for path in sorted(PLANS_DIR.glob("*.json"), reverse=True):
        if not _valid_iso_week(path.stem):
            continue
        plan = _read_valid_week_plan(path.stem)
        if plan is None:
            continue
        days = plan["days"]
        meal_count = sum(
            len(days[day_code].get("meals", []))
            for day_code in _PLAN_DAYS
        )
        result.append({
            "week": path.stem,
            "status": plan.get("status", "draft"),
            "meals_count": meal_count,
            "prep_count": len(plan.get("prep", [])),
        })
    return result

# ─── Helpers ────────────────────────────────────────────────────────────
def _normalize(name: str) -> str:
    return name.strip().lower()

def _can_cook(dish_ingredients: dict, fridge: list) -> bool:
    fridge_set = set(fridge)
    for ing, essential in dish_ingredients.items():
        if essential and ing not in fridge_set:
            return False
    return True

def _missing_essentials(dish_ingredients: dict, fridge: list) -> list:
    fridge_set = set(fridge)
    return [ing for ing, ess in dish_ingredients.items() if ess and ing not in fridge_set]

def _optional_missing(dish_ingredients: dict, fridge: list) -> list:
    fridge_set = set(fridge)
    return [ing for ing, ess in dish_ingredients.items() if not ess and ing not in fridge_set]

def _recent_dishes(history: list, days: int = RECENCY_COOLDOWN_DAYS) -> set:
    cutoff = datetime.now() - timedelta(days=days)
    recent = set()
    for entry in history:
        try:
            d = datetime.fromisoformat(entry["date"])
            if d >= cutoff:
                recent.add(_normalize(entry["dish"]))
        except (KeyError, ValueError, TypeError):
            continue
    return recent

def _dish_score(dish: dict, fridge: list, recent: set) -> float:
    """Replicate the scoring heuristic: availability ratio + recency penalty."""
    name = _normalize(dish["name"])
    ingredients = dish.get("ingredients", {})
    if not ingredients:
        return 0.0
    fridge_set = set(fridge)
    available = sum(1 for ing in ingredients if ing in fridge_set)
    total = len(ingredients)
    availability = available / total
    recency_penalty = 0.3 if name in recent else 0.0
    return round(availability - recency_penalty, 3)

def _shopping_list(dishes, fridge):
    """For each dish missing exactly one essential ingredient, suggest it."""
    fridge_set = set(fridge)
    suggestions = {}
    for dish in dishes:
        ings = dish.get("ingredients", {})
        missing = [i for i, e in ings.items() if e and i not in fridge_set]
        if len(missing) == 1:
            ing = missing[0]
            if ing not in suggestions:
                suggestions[ing] = []
            suggestions[ing].append(dish["name"])
    result = []
    for ing, dishes_list in suggestions.items():
        result.append({
            "ingredient": ing,
            "unlocks": dishes_list,
            "unlocks_count": len(dishes_list),
        })
    result.sort(key=lambda x: x["unlocks_count"], reverse=True)
    return result

# ─── Pydantic models ────────────────────────────────────────────────────
class DishCreate(BaseModel):
    name: str
    ingredients: dict[str, bool] = {}

class DishUpdate(BaseModel):
    name: str | None = None
    ingredients: dict[str, bool] | None = None

class FridgeUpdate(BaseModel):
    ingredients: list[str]

class FridgeAddRemove(BaseModel):
    ingredient: str

class FridgeRename(BaseModel):
    old_ingredient: str
    new_ingredient: str

class InventoryItemCreate(BaseModel):
    name: str = Field(max_length=200)
    quantity: Any = None
    unit: str | None = None
    package_count: StrictInt | None = None
    storage: str | None = None
    expires_on: str | None = None
    comment: str | None = None

    class Config:
        extra = "forbid"

class InventoryItemPatch(BaseModel):
    expected_updated_at: str = Field(min_length=1, max_length=100)
    name: str | None = Field(default=None, max_length=200)
    quantity: Any = None
    unit: str | None = None
    package_count: StrictInt | None = None
    storage: str | None = None
    expires_on: str | None = None
    comment: str | None = None

    class Config:
        extra = "forbid"

class ProductReplenish(BaseModel):
    product_id: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, max_length=200)
    quantity: Any = None
    unit: str | None = None
    package_count: StrictInt | None = None
    storage: str | None = None
    expires_on: str | None = None
    comment: str | None = None

    class Config:
        extra = "forbid"

class CookedMeal(BaseModel):
    dish: str
    date: str | None = None  # ISO date, defaults to today

# ─── API: Dishes ────────────────────────────────────────────────────────
@app.get("/api/dishes")
def get_dishes():
    return {"dishes": load_dishes()}

@app.post("/api/dishes")
def add_dish(payload: DishCreate):
    with _lock:
        dishes = load_dishes()
        name = _normalize(payload.name)
        if any(_normalize(d["name"]) == name for d in dishes):
            raise HTTPException(409, f"Dish '{name}' already exists")
        ingredients = {_normalize(k): v for k, v in payload.ingredients.items()}
        dishes.append({"name": name, "ingredients": ingredients})
        save_dishes(dishes)
    return {"status": "ok", "dish": {"name": name, "ingredients": ingredients}}

@app.put("/api/dishes/{dish_name}")
def update_dish(dish_name: str, payload: DishUpdate):
    with _lock:
        dishes = load_dishes()
        target = _normalize(dish_name)
        for i, d in enumerate(dishes):
            if _normalize(d["name"]) == target:
                if payload.name is not None:
                    d["name"] = _normalize(payload.name)
                if payload.ingredients is not None:
                    d["ingredients"] = {_normalize(k): v for k, v in payload.ingredients.items()}
                dishes[i] = d
                save_dishes(dishes)
                return {"status": "ok", "dish": d}
    raise HTTPException(404, f"Dish '{dish_name}' not found")

@app.delete("/api/dishes/{dish_name}")
def delete_dish(dish_name: str):
    with _lock:
        dishes = load_dishes()
        target = _normalize(dish_name)
        new_dishes = [d for d in dishes if _normalize(d["name"]) != target]
        if len(new_dishes) == len(dishes):
            raise HTTPException(404, f"Dish '{dish_name}' not found")
        save_dishes(new_dishes)
    return {"status": "ok"}

# ─── API: Fridge ────────────────────────────────────────────────────────
def _model_patch(payload: BaseModel) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def _inventory_error(exc: Exception):
    if isinstance(exc, InventoryConflictError):
        current_item = getattr(exc, "current_item")
        current_payload = current_item.to_public_dict()
        current_payload["available"] = current_item.available
        raise HTTPException(409, {
            "code": "inventory_conflict",
            "message": str(exc),
            "current_item": current_payload,
        }) from exc
    if isinstance(exc, (InventoryDataError, OSError)):
        logger.error("Inventory storage failure", exc_info=exc)
        raise HTTPException(503, "Inventory storage is temporarily unavailable") from exc
    message = str(exc)
    if isinstance(exc, LookupError):
        raise HTTPException(404, message) from exc
    if "already exists" in message:
        raise HTTPException(409, message) from exc
    raise HTTPException(400, message) from exc


def _inventory_api_errors(fn):
    """Map persistence failures uniformly for legacy and derived routes."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (InventoryDataError, OSError) as exc:
            _inventory_error(exc)
        except (TypeError, ValueError) as exc:
            _inventory_error(exc)
    return wrapped


def _legacy_inventory_name(value: str) -> str:
    name = _normalize(value)
    if not name:
        raise ValueError("Ingredient name must not be blank")
    if len(name) > 200:
        raise ValueError("Ingredient name must be at most 200 characters")
    return name


@app.get("/api/inventory/items")
def list_inventory_items():
    try:
        return {"items": [item.to_public_dict() for item in _fridge_repository().load_items()]}
    except (TypeError, ValueError, OSError) as exc:
        _inventory_error(exc)


@app.post("/api/inventory/items")
def add_inventory_item(payload: InventoryItemCreate):
    fields = _model_patch(payload)
    name = fields.pop("name")
    try:
        item = _fridge_repository().add_item(name=name, **fields)
        return {"item": item.to_public_dict()}
    except (TypeError, ValueError, OSError) as exc:
        _inventory_error(exc)


@app.patch("/api/inventory/items/{item_id}")
def edit_inventory_item(item_id: str, payload: InventoryItemPatch):
    try:
        fields = _model_patch(payload)
        expected_updated_at = fields.pop("expected_updated_at")
        item = _fridge_repository().edit_item(
            item_id,
            fields,
            expected_updated_at=expected_updated_at,
        )
        return {"item": item.to_public_dict()}
    except (TypeError, ValueError, LookupError, OSError) as exc:
        _inventory_error(exc)


@app.delete("/api/inventory/items/{item_id}")
def remove_inventory_item(item_id: str, expected_updated_at: str):
    try:
        item = _fridge_repository().remove_item(
            item_id,
            expected_updated_at=expected_updated_at,
        )
        return {"item": item.to_public_dict()}
    except (TypeError, ValueError, LookupError, OSError) as exc:
        _inventory_error(exc)


@app.get("/api/products")
@_inventory_api_errors
def list_product_catalog(status: str = "all", query: str | None = None):
    dishes = []
    for entry in load_dishes():
        try:
            dishes.append(Dish.from_dict(entry))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return {"items": build_product_catalog(
        _fridge_repository().load_catalog_items(),
        dishes,
        status=status,
        query=query,
    )}


@app.post("/api/products/replenish")
def replenish_product(payload: ProductReplenish):
    fields = _model_patch(payload)
    product_id = fields.pop("product_id", None)
    name = fields.pop("name", None)
    try:
        item = _fridge_repository().replenish_item(
            item_id=product_id,
            name=name,
            **fields,
        )
        return {"item": item.to_public_dict()}
    except (TypeError, ValueError, LookupError, OSError) as exc:
        _inventory_error(exc)


@app.get("/api/fridge")
@_inventory_api_errors
def get_fridge():
    return {"ingredients": load_fridge()}

@app.put("/api/fridge")
@_inventory_api_errors
def set_fridge(payload: FridgeUpdate):
    with _lock:
        items = sorted(set(_legacy_inventory_name(i) for i in payload.ingredients))
        save_fridge(items)
    return {"ingredients": items}

@app.post("/api/fridge/add")
@_inventory_api_errors
def add_to_fridge(payload: FridgeAddRemove):
    with _lock:
        repo = _fridge_repository()
        ing = _legacy_inventory_name(payload.ingredient)
        with repo.lock:
            fridge = repo.load()
            if ing not in fridge:
                fridge.append(ing)
                fridge.sort()
                repo.save(fridge)
    return {"ingredients": fridge}

@app.post("/api/fridge/remove")
@_inventory_api_errors
def remove_from_fridge(payload: FridgeAddRemove):
    with _lock:
        repo = _fridge_repository()
        ing = _legacy_inventory_name(payload.ingredient)
        repo.remove_items([ing])
        fridge = repo.load()
    return {"ingredients": fridge}

@app.put("/api/fridge/item")
@_inventory_api_errors
def rename_fridge_item(payload: FridgeRename):
    old_name = _normalize(payload.old_ingredient)
    new_name = _normalize(payload.new_ingredient)
    if not old_name or not new_name:
        raise HTTPException(400, "Ingredient names must not be blank")
    if len(old_name) > 200 or len(new_name) > 200:
        raise HTTPException(400, "Ingredient names must be at most 200 characters")

    with _lock:
        try:
            item = _fridge_repository().rename_by_name(old_name, new_name)
        except (InventoryDataError, OSError):
            raise
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        fridge = load_fridge()
    return {
        "ingredients": fridge,
        "item": item.to_public_dict(),
        "renamed": {"old_ingredient": old_name, "new_ingredient": new_name},
        "changed": old_name != new_name,
    }

@app.delete("/api/fridge")
@_inventory_api_errors
def clear_fridge():
    with _lock:
        save_fridge([])
    return {"status": "ok", "ingredients": []}

# ─── API: Suggestions & Shopping ────────────────────────────────────────
@app.get("/api/suggestions")
@_inventory_api_errors
def get_suggestions():
    dishes = load_dishes()
    fridge = load_fridge()
    history = load_history()
    recent = _recent_dishes(history)
    suggestions = []
    for d in dishes:
        score = _dish_score(d, fridge, recent)
        can_cook = _can_cook(d.get("ingredients", {}), fridge)
        missing = _missing_essentials(d.get("ingredients", {}), fridge)
        optional_missing = _optional_missing(d.get("ingredients", {}), fridge)
        suggestions.append({
            "name": d["name"],
            "ingredients": d.get("ingredients", {}),
            "score": score,
            "can_cook": can_cook,
            "missing_essentials": missing,
            "missing_optional": optional_missing,
            "recently_cooked": _normalize(d["name"]) in recent,
        })
    suggestions.sort(key=lambda x: x["score"], reverse=True)
    return {"suggestions": suggestions}

@app.get("/api/shopping")
@_inventory_api_errors
def get_shopping():
    dishes = load_dishes()
    fridge = load_fridge()
    return {"items": _shopping_list(dishes, fridge)}

# ─── API: History ───────────────────────────────────────────────────────
@app.get("/api/history")
def get_history():
    return {"history": load_history()}

@app.post("/api/history")
@_inventory_api_errors
def add_history(payload: CookedMeal):
    with _lock:
        history = load_history()
        entry = {
            "dish": _normalize(payload.dish),
            "date": payload.date or datetime.now().isoformat(),
        }
        dishes = load_dishes()
        dish = next((d for d in dishes if _normalize(d["name"]) == entry["dish"]), None)
        repo = _fridge_repository()
        inventory_before = None
        with repo.lock:
            if dish:
                inventory_before = repo.load_catalog_items()
                essentials_to_remove = {
                    ing for ing, ess in dish.get("ingredients", {}).items() if ess
                }
                repo.remove_items(list(essentials_to_remove))
            try:
                history.append(entry)
                save_history(history)
            except Exception:
                if inventory_before is not None:
                    repo.save_items(inventory_before)
                raise
    return {"status": "ok", "entry": entry}

@app.delete("/api/history/{entry_index}")
def delete_history_entry(entry_index: int):
    with _lock:
        history = load_history()
        if entry_index < 0 or entry_index >= len(history):
            raise HTTPException(404, "History entry not found")
        history.pop(entry_index)
        save_history(history)
    return {"status": "ok"}

# ─── API: Weekly plans (read-only view) ─────────────────────────────────
@app.get("/api/plans")
def get_week_plans():
    return {"plans": list_week_plans()}

@app.get("/api/plans/{week_id}")
def get_week_plan_view(week_id: str):
    return {"plan": load_week_plan(week_id)}

# ─── API: Stats ─────────────────────────────────────────────────────────
@app.get("/api/stats")
@_inventory_api_errors
def get_stats():
    dishes = load_dishes()
    fridge = load_fridge()
    history = load_history()
    recent = _recent_dishes(history)

    cookable = sum(1 for d in dishes if _can_cook(d.get("ingredients", {}), fridge))
    total_ingredients_used = Counter()
    for d in dishes:
        for ing in d.get("ingredients", {}):
            total_ingredients_used[ing] += 1

    fridge_utility = {}
    for item in fridge:
        uses = sum(
            1 for d in dishes
            if item in d.get("ingredients", {})
        )
        fridge_utility[item] = uses

    unused_fridge = [i for i in fridge if fridge_utility.get(i, 0) == 0]

    # History stats
    cook_counts = Counter(h.get("dish", "") for h in history)
    last_7_days = [
        (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(6, -1, -1)
    ]
    cooks_by_day = {day: 0 for day in last_7_days}
    for h in history:
        try:
            day = datetime.fromisoformat(h["date"]).strftime("%Y-%m-%d")
            if day in cooks_by_day:
                cooks_by_day[day] += 1
        except (KeyError, ValueError):
            continue

    return {
        "total_dishes": len(dishes),
        "total_fridge_items": len(fridge),
        "cookable_now": cookable,
        "recently_cooked": len(recent),
        "unused_fridge_items": unused_fridge,
        "fridge_utility": fridge_utility,
        "top_ingredients": total_ingredients_used.most_common(10),
        "most_cooked": cook_counts.most_common(5),
        "cooks_last_7_days": cooks_by_day,
        "tuning": load_tuning(),
    }

# ─── Serve frontend ─────────────────────────────────────────────────────
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8088)
