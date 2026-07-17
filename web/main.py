#!/usr/bin/env python3
"""Meal Planning Web Interface — FastAPI backend.

Reads/writes the same JSON files used by the Hermes meal_manager plugin,
ensuring full synchronization between the Telegram bot, agent, and web UI.
"""

import hashlib
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
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
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
_dish_repository_module = importlib.import_module(
    f"{PLUGIN_ROOT.name}.src.repositories.json_dish"
)
_product_catalog_module = importlib.import_module(
    f"{PLUGIN_ROOT.name}.src.product_catalog"
)
_plan_module = importlib.import_module(f"{PLUGIN_ROOT.name}.src.plan")
_plan_repository_module = importlib.import_module(
    f"{PLUGIN_ROOT.name}.src.repositories.json_plan"
)
_history_repository_module = importlib.import_module(
    f"{PLUGIN_ROOT.name}.src.repositories.json_history"
)
_audit_module = importlib.import_module(f"{PLUGIN_ROOT.name}.src.audit.transaction")
_audit_context_module = importlib.import_module(f"{PLUGIN_ROOT.name}.src.audit.context")
_cooking_module = importlib.import_module(f"{PLUGIN_ROOT.name}.src.cooking")
_prep_repository_module = importlib.import_module(
    f"{PLUGIN_ROOT.name}.src.repositories.json_prep_item"
)
_shopping_module = importlib.import_module(f"{PLUGIN_ROOT.name}.src.shopping")
_shopping_request_repository_module = importlib.import_module(
    f"{PLUGIN_ROOT.name}.src.repositories.json_shopping_request"
)
JsonFridgeRepository = _inventory_repository_module.JsonFridgeRepository
JsonDishRepository = _dish_repository_module.JsonDishRepository
dish_catalog_version = _dish_repository_module.dish_catalog_version
JsonPlanRepository = _plan_repository_module.JsonPlanRepository
JsonHistoryRepository = _history_repository_module.JsonHistoryRepository
HistoryDataError = _history_repository_module.HistoryDataError
AuditTransactionManager = _audit_module.AuditTransactionManager
AuditConflictError = _audit_module.AuditConflictError
audit_scope = _audit_context_module.audit_scope
register_cooked = _cooking_module.register_cooked
retract_cooked = _cooking_module.retract_cooked
JsonPrepItemRepository = _prep_repository_module.JsonPrepItemRepository
JsonShoppingRequestRepository = _shopping_request_repository_module.JsonShoppingRequestRepository
project_plan_shopping = _shopping_module.project_plan_shopping
merge_manual_requests = _shopping_module.merge_manual_requests
InventoryDataError = _inventory_repository_module.InventoryDataError
InventoryConflictError = _inventory_repository_module.InventoryConflictError
Dish = _dish_module.Dish
MealEntry = _plan_module.MealEntry
WeekPlan = _plan_module.WeekPlan
build_product_catalog = _product_catalog_module.build_product_catalog
logger = logging.getLogger(__name__)
DATA_DIR = Path(__import__("os").environ.get("MEAL_DATA_DIR", PLUGIN_ROOT / "data"))
DISHES_PATH = DATA_DIR / "dishes.json"
FRIDGE_PATH = DATA_DIR / "fridge.json"
HISTORY_PATH = DATA_DIR / "history.json"
TUNING_PATH = DATA_DIR / "tuning.json"
PREP_ITEMS_PATH = DATA_DIR / "prep_items.json"
SHOPPING_REQUESTS_PATH = DATA_DIR / "shopping_requests.json"
PLANS_DIR = DATA_DIR / "plans"
_structured_dish_repo = JsonDishRepository(DISHES_PATH)
_structured_fridge_repo = JsonFridgeRepository(FRIDGE_PATH)
_structured_plan_repo = JsonPlanRepository(PLANS_DIR)
_structured_history_repo = JsonHistoryRepository(HISTORY_PATH)
_web_audit_manager = AuditTransactionManager(DATA_DIR)
_structured_prep_repo = JsonPrepItemRepository(DATA_DIR / "prep_items.json")
_structured_shopping_request_repo = JsonShoppingRequestRepository(
    DATA_DIR / "shopping_requests.json"
)

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

def _dish_repository():
    _structured_dish_repo.path = Path(DISHES_PATH)
    return _structured_dish_repo


def load_dishes():
    return [dish.to_dict() for dish in _dish_repository().load()]


def save_dishes(dishes):
    _dish_repository().save([Dish.from_dict(dish) for dish in dishes])

def _fridge_repository():
    _structured_fridge_repo.path = Path(FRIDGE_PATH)
    return _structured_fridge_repo


def _prep_repository():
    _structured_prep_repo.path = Path(PREP_ITEMS_PATH)
    return _structured_prep_repo


def _shopping_request_repository():
    _structured_shopping_request_repo.path = Path(SHOPPING_REQUESTS_PATH)
    return _structured_shopping_request_repo


def load_fridge():
    return _fridge_repository().load()


def load_available_ingredient_keys():
    return _fridge_repository().load_set()

def save_fridge(items):
    _fridge_repository().save(items)

def _history_repository():
    _structured_history_repo.path = Path(HISTORY_PATH)
    return _structured_history_repo


def _audit_transaction_manager(data_dir=None):
    _web_audit_manager.configure(
        Path(data_dir).resolve() if data_dir is not None else Path(HISTORY_PATH).parent
    )
    return _web_audit_manager


def _web_audited(operation, data_root):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            manager = _audit_transaction_manager(data_root())
            try:
                with audit_scope(
                    operation=operation,
                    manager=manager,
                    actor_type="user",
                    surface_kind="web_api",
                ):
                    return fn(*args, **kwargs)
            except (AuditConflictError, OSError, json.JSONDecodeError) as exc:
                logger.error("Audit storage failure", exc_info=exc)
                raise HTTPException(
                    503, "Audit storage is temporarily unavailable"
                ) from exc
        return wrapped
    return decorate


def _history_public(event):
    result = event.to_dict()
    result["dish"] = event.dish_name_snapshot
    result["date"] = event.cooked_at or event.cooked_on
    result["status"] = "active" if event.active else "retracted"
    return result


def load_history():
    return [
        _history_public(event)
        for event in _history_repository().load_events(strict=True)
    ]


def save_history(entries):
    """Compatibility helper; canonical callers persist CookingEvent objects."""
    _history_repository().save_events(entries)

def _plan_repository():
    _structured_plan_repo.plans_dir = Path(PLANS_DIR)
    return _structured_plan_repo


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
            or source.get("kind") not in {"dish", "prep", "manual"}
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
    raw_plan = _read_valid_week_plan(week_id)
    if raw_plan is None:
        plan_path = Path(PLANS_DIR) / f"{week_id}.json"
        if plan_path.exists():
            raise HTTPException(503, "Weekly plan data is malformed")
        raise HTTPException(404, f"Plan '{week_id}' not found")
    try:
        return WeekPlan.from_dict(raw_plan).to_dict()
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(404, f"Plan '{week_id}' not found or malformed") from exc


def _plan_version(plan: WeekPlan | dict) -> str:
    payload = (
        plan.to_dict()
        if isinstance(plan, WeekPlan)
        else WeekPlan.from_dict(plan).to_dict()
    )
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _require_plan_version(plan: WeekPlan, expected_version: str) -> None:
    current_version = _plan_version(plan)
    if current_version != expected_version:
        raise HTTPException(409, {
            "code": "plan_conflict",
            "message": "Plan changed after it was loaded",
            "current_plan": plan.to_dict(),
            "current_version": current_version,
        })


def _require_draft_plan(repo, week_id: str, expected_version: str) -> WeekPlan:
    try:
        normalized_week = WeekPlan.normalize_week_id(week_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    plan = repo.load(normalized_week)
    if plan is None:
        raise HTTPException(404, f"Plan '{normalized_week}' not found or malformed")
    _require_plan_version(plan, expected_version)
    if plan.status != "draft":
        raise HTTPException(409, "Only draft plans can be edited in Web")
    return plan


def _require_plan_day(day: str) -> str:
    normalized = day.strip().lower() if isinstance(day, str) else ""
    if normalized not in _PLAN_DAYS:
        raise HTTPException(400, "Invalid plan day")
    return normalized


def _plan_meal_entry(
    dish: str,
    portions: int,
    *,
    existing_dish: str | None = None,
) -> MealEntry:
    try:
        entry = MealEntry(dish=dish, portions=portions)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    known_dishes = {
        Dish.normalize_name(item.get("name", ""))
        for item in load_dishes()
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if entry.dish not in known_dishes and entry.dish != existing_dish:
        raise HTTPException(404, f"Dish '{entry.dish}' is not in the recipe catalog")
    return entry


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
    cutoff = date.today() - timedelta(days=days)
    recent = set()
    for entry in history:
        if entry.get("status") == "retracted":
            continue
        try:
            cooked_on = datetime.fromisoformat(entry["date"].replace("Z", "+00:00")).date()
            if cooked_on >= cutoff:
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
    instructions: str | None = None
    expected_version: str

class DishUpdate(BaseModel):
    name: str | None = None
    ingredients: dict[str, bool] | None = None
    instructions: str | None = None
    expected_version: str

class FridgeUpdate(BaseModel):
    ingredients: list[str]

class FridgeAddRemove(BaseModel):
    ingredient: str

class FridgeRename(BaseModel):
    old_ingredient: str
    new_ingredient: str

class InventoryItemCreate(BaseModel):
    name: str = Field(max_length=200)
    category: Literal["product", "prep", "ready_meal"] = "product"
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
    category: Literal["product", "prep", "ready_meal"] | None = None
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
    expected_updated_at: str | None = Field(..., min_length=1, max_length=100)
    category: Literal["product", "prep", "ready_meal"] | None = None
    quantity: Any = None
    unit: str | None = None
    package_count: StrictInt | None = None
    storage: str | None = None
    expires_on: str | None = None
    comment: str | None = None

    class Config:
        extra = "forbid"


class ProductCategoryPatch(BaseModel):
    product_id: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category: Literal["product", "prep", "ready_meal"]
    expected_updated_at: str | None = Field(..., min_length=1, max_length=100)

    class Config:
        extra = "forbid"


class PlanMealCreate(BaseModel):
    dish: str = Field(min_length=1, max_length=200)
    portions: StrictInt = Field(default=2, ge=1)
    expected_version: str = Field(min_length=1, max_length=100)

    class Config:
        extra = "forbid"


class PlanMealEdit(BaseModel):
    dish: str = Field(min_length=1, max_length=200)
    portions: StrictInt = Field(ge=1)
    expected_version: str = Field(min_length=1, max_length=100)

    class Config:
        extra = "forbid"


class PlanMealStableEdit(PlanMealEdit):
    expected_revision: StrictInt = Field(ge=1)


class CookedMeal(BaseModel):
    dish: str = Field(min_length=1, max_length=200)
    date: str | None = None
    occurrence_id: str | None = Field(default=None, min_length=1, max_length=100)
    expected_revision: StrictInt | None = Field(default=None, ge=1)
    actual_portions: StrictInt | None = Field(default=None, ge=0)
    actual_yield_portions: StrictInt | None = Field(default=None, ge=0)

    class Config:
        extra = "forbid"

# ─── API: Dishes ────────────────────────────────────────────────────────
def _assert_dish_catalog_version(expected_version: str, dishes: list[Dish]) -> str:
    current_version = dish_catalog_version(dishes)
    if expected_version != current_version:
        raise HTTPException(409, {
            "code": "dish_catalog_conflict",
            "message": "Recipes changed after this screen was loaded.",
            "current_version": current_version,
        })
    return current_version


@app.get("/api/dishes")
def get_dishes():
    repo = _dish_repository()
    with repo.lock:
        dishes = repo.load()
        return {
            "dishes": [dish.to_dict() for dish in dishes],
            "version": dish_catalog_version(dishes),
        }


@app.post("/api/dishes")
@_web_audited("add_dish", lambda: Path(DISHES_PATH).parent)
def add_dish(payload: DishCreate):
    repo = _dish_repository()
    with repo.lock:
        dishes = repo.load()
        _assert_dish_catalog_version(payload.expected_version, dishes)
        name = _normalize(payload.name)
        if any(dish.name == name for dish in dishes):
            raise HTTPException(409, f"Dish '{name}' already exists")
        try:
            dish = Dish.from_dict({
                "name": name,
                "ingredients": payload.ingredients,
                "instructions": payload.instructions,
            })
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        dishes.append(dish)
        repo.save(dishes)
        version = dish_catalog_version(dishes)
    return {"status": "ok", "dish": dish.to_dict(), "version": version}


@app.put("/api/dishes/{dish_name}")
@_web_audited("update_dish", lambda: Path(DISHES_PATH).parent)
def update_dish(dish_name: str, payload: DishUpdate):
    repo = _dish_repository()
    with repo.lock:
        dishes = repo.load()
        _assert_dish_catalog_version(payload.expected_version, dishes)
        target = _normalize(dish_name)
        patch = _model_patch(payload)
        dish = next((item for item in dishes if item.name == target), None)
        if dish is None:
            raise HTTPException(404, f"Dish '{dish_name}' not found")
        candidate_name = patch.get("name", dish.name)
        if candidate_name != dish.name and any(
            item is not dish and item.name == _normalize(candidate_name)
            for item in dishes
        ):
            raise HTTPException(409, f"Dish '{candidate_name}' already exists")
        try:
            candidate = Dish.from_dict({
                "name": candidate_name,
                "ingredients": patch.get("ingredients", dish.ingredients),
                "prep_depends": dish.prep_depends,
                "instructions": patch.get("instructions", dish.instructions),
            })
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        dishes[dishes.index(dish)] = candidate
        repo.save(dishes)
        version = dish_catalog_version(dishes)
    return {"status": "ok", "dish": candidate.to_dict(), "version": version}


@app.delete("/api/dishes/{dish_name}")
@_web_audited("delete_dish", lambda: Path(DISHES_PATH).parent)
def delete_dish(dish_name: str, expected_version: str):
    repo = _dish_repository()
    with repo.lock:
        dishes = repo.load()
        _assert_dish_catalog_version(expected_version, dishes)
        target = _normalize(dish_name)
        remaining = [dish for dish in dishes if dish.name != target]
        if len(remaining) == len(dishes):
            raise HTTPException(404, f"Dish '{dish_name}' not found")
        repo.save(remaining)
        version = dish_catalog_version(remaining)
    return {"status": "ok", "version": version}

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
@_web_audited("add_inventory_item", lambda: Path(FRIDGE_PATH).parent)
def add_inventory_item(payload: InventoryItemCreate):
    fields = _model_patch(payload)
    name = fields.pop("name")
    try:
        item = _fridge_repository().add_item(name=name, **fields)
        return {"item": item.to_public_dict()}
    except (TypeError, ValueError, OSError) as exc:
        _inventory_error(exc)


@app.patch("/api/inventory/items/{item_id}")
@_web_audited("edit_inventory_item", lambda: Path(FRIDGE_PATH).parent)
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
@_web_audited("remove_inventory_item", lambda: Path(FRIDGE_PATH).parent)
def remove_inventory_item(item_id: str, expected_updated_at: str):
    try:
        item = _fridge_repository().remove_item(
            item_id,
            expected_updated_at=expected_updated_at,
        )
        return {"item": item.to_public_dict()}
    except (TypeError, ValueError, LookupError, OSError) as exc:
        _inventory_error(exc)


def _catalog_dishes() -> list[Dish]:
    dishes = []
    for entry in load_dishes():
        try:
            dishes.append(Dish.from_dict(entry))
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return dishes


@app.get("/api/products")
@_inventory_api_errors
def list_product_catalog(
    status: str = "all",
    category: str = "all",
    query: str | None = None,
):
    return {"items": build_product_catalog(
        _fridge_repository().load_catalog_items(),
        _catalog_dishes(),
        status=status,
        category=category,
        query=query,
    )}


@app.patch("/api/products/category")
@_web_audited("set_product_category", lambda: Path(FRIDGE_PATH).parent)
@_inventory_api_errors
def set_product_category(payload: ProductCategoryPatch):
    if payload.product_id:
        if payload.name is not None or payload.expected_updated_at is None:
            raise HTTPException(400, "Persisted products require product_id and a version")
    elif not payload.name or payload.expected_updated_at is not None:
        raise HTTPException(400, "Name targeting is only valid for an absent recipe-only identity")
    rows = build_product_catalog(
        _fridge_repository().load_catalog_items(),
        _catalog_dishes(),
    )
    name = _normalize(payload.name) if payload.name else None
    current = (
        next((row for row in rows if row["id"] == payload.product_id), None)
        if payload.product_id
        else next((row for row in rows if row["name"] == name), None)
    )
    if current is None:
        raise HTTPException(404, "Product not found in product catalog")
    try:
        item = _fridge_repository().set_product_category(
            name,
            payload.category,
            item_id=payload.product_id,
            allow_create=current["id"] is None,
            expected_updated_at=payload.expected_updated_at,
        )
        refreshed = build_product_catalog(
            _fridge_repository().load_catalog_items(),
            _catalog_dishes(),
        )
        updated = next((row for row in refreshed if row["id"] == item.id), None)
        if updated is None:
            updated = item.to_public_dict() | {
                "status": current["status"],
                "recipe_count": 0,
                "in_recipes": False,
            }
        return {"item": updated}
    except (TypeError, ValueError, LookupError, OSError) as exc:
        _inventory_error(exc)


@app.post("/api/products/replenish")
@_web_audited("replenish_product", lambda: Path(FRIDGE_PATH).parent)
def replenish_product(payload: ProductReplenish):
    fields = _model_patch(payload)
    product_id = fields.pop("product_id", None)
    name = fields.pop("name", None)
    expected_updated_at = fields.pop("expected_updated_at")
    if product_id:
        if name is not None or expected_updated_at is None:
            raise HTTPException(400, "Persisted products require product_id and a version")
    elif not name or expected_updated_at is not None:
        raise HTTPException(400, "Name targeting is only valid for an absent recipe-only identity")
    try:
        item = _fridge_repository().replenish_item(
            item_id=product_id,
            name=name,
            expected_updated_at=expected_updated_at,
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
@_web_audited("set_fridge", lambda: Path(FRIDGE_PATH).parent)
@_inventory_api_errors
def set_fridge(payload: FridgeUpdate):
    with _lock:
        items = sorted(set(_legacy_inventory_name(i) for i in payload.ingredients))
        save_fridge(items)
    return {"ingredients": items}

@app.post("/api/fridge/add")
@_web_audited("add_to_fridge", lambda: Path(FRIDGE_PATH).parent)
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
@_web_audited("remove_from_fridge", lambda: Path(FRIDGE_PATH).parent)
@_inventory_api_errors
def remove_from_fridge(payload: FridgeAddRemove):
    with _lock:
        repo = _fridge_repository()
        ing = _legacy_inventory_name(payload.ingredient)
        repo.remove_items([ing])
        fridge = repo.load()
    return {"ingredients": fridge}

@app.put("/api/fridge/item")
@_web_audited("rename_fridge_item", lambda: Path(FRIDGE_PATH).parent)
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
@_web_audited("clear_fridge", lambda: Path(FRIDGE_PATH).parent)
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
    fridge = load_available_ingredient_keys()
    try:
        history = load_history()
    except (HistoryDataError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.error("Suggestion history read failure", exc_info=exc)
        raise HTTPException(503, "History storage is temporarily unavailable") from exc
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

def _current_week_id() -> str:
    year, week, _ = date.today().isocalendar()
    return f"{year}-W{week:02d}"


def _project_shopping_safe(plan: WeekPlan, catalog) -> tuple[dict, bool, str | None]:
    try:
        projected, stale = project_plan_shopping(
            plan=plan,
            dishes=_dish_repository().load_strict(),
            prep_items=_prep_repository().load_strict(),
            catalog_items=catalog,
            manual_requests=_shopping_request_repository().load(week=plan.week_id),
        )
        return projected, stale, None
    except (LookupError, ValueError) as exc:
        return {"items": []}, True, str(exc)


def _weekly_plan_shopping_view(week_id: str, catalog=None) -> dict:
    plan = WeekPlan.from_dict(load_week_plan(week_id))
    if catalog is None:
        catalog = _fridge_repository().load_catalog_items()
    projected, persisted_stale, projection_error = _project_shopping_safe(plan, catalog)
    return {
        "week": week_id,
        "source": "weekly_plan",
        "items": projected.get("items", []),
        "persisted_stale": persisted_stale,
        "projection_error": projection_error,
    }


@app.get("/api/shopping")
@_inventory_api_errors
def get_shopping():
    week_id = _current_week_id()
    catalog = _fridge_repository().load_catalog_items()
    try:
        return _weekly_plan_shopping_view(week_id, catalog)
    except HTTPException as exc:
        if exc.status_code == 404:
            return {
                "week": week_id,
                "source": "weekly_plan",
                "items": merge_manual_requests(
                    [], _shopping_request_repository().load(week=week_id)
                ),
                "persisted_stale": False,
                "projection_error": None,
            }
        raise

# ─── API: History ───────────────────────────────────────────────────────
@app.get("/api/history")
def get_history():
    try:
        return {"history": load_history()}
    except (HistoryDataError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.error("History read failure", exc_info=exc)
        raise HTTPException(503, "History storage is temporarily unavailable") from exc

@app.post("/api/history")
@_web_audited("register_cooked_meal", lambda: Path(HISTORY_PATH).parent)
def add_history(payload: CookedMeal):
    try:
        result = register_cooked(
            dish_name=_normalize(payload.dish),
            occurrence_id=payload.occurrence_id,
            expected_revision=payload.expected_revision,
            cooked_at=payload.date,
            actual_portions=payload.actual_portions,
            actual_yield_portions=payload.actual_yield_portions,
            actor_type="user",
            surface_kind="web",
            dish_repository=_dish_repository(),
            fridge_repository=_fridge_repository(),
            history_repository=_history_repository(),
            plan_repository=_plan_repository(),
            prep_repository=_prep_repository(),
            audit_transaction_manager=_audit_transaction_manager(),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (
        AuditConflictError, HistoryDataError, InventoryDataError,
        OSError, json.JSONDecodeError,
    ) as exc:
        logger.error("Cooking transaction storage failure", exc_info=exc)
        raise HTTPException(503, "Inventory storage is temporarily unavailable") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    event = next(
        item for item in _history_repository().load_events(strict=True)
        if item.id == result["cook_event_id"]
    )
    return {
        "status": "ok",
        "entry": _history_public(event),
        "transaction_id": result["transaction_id"],
    }


@app.delete("/api/history/{event_id}")
@_web_audited("retract_cooked_meal", lambda: Path(HISTORY_PATH).parent)
def delete_history_entry(event_id: str):
    try:
        if event_id.isdigit():
            events = _history_repository().load_events(strict=True)
            index = int(event_id)
            if index >= len(events):
                raise LookupError("history entry index not found")
            event_id = events[index].id
        result = retract_cooked(
            event_id=event_id,
            actor_type="user",
            surface_kind="web",
            history_repository=_history_repository(),
            plan_repository=_plan_repository(),
            audit_transaction_manager=_audit_transaction_manager(),
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (AuditConflictError, HistoryDataError, OSError, json.JSONDecodeError) as exc:
        logger.error("History correction storage failure", exc_info=exc)
        raise HTTPException(503, "History storage is temporarily unavailable") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "status": "ok",
        "entry": _history_public(result["entry"]),
        "transaction_id": result["transaction_id"],
        "plan_reopened": result["plan_reopened"],
        "inventory_restored": False,
    }

# ─── API: Weekly plans ───────────────────────────────────────────────────
@app.get("/api/plans")
def get_week_plans():
    return {"plans": list_week_plans()}


@app.get("/api/plans/{week_id}")
@_inventory_api_errors
def get_week_plan_view(week_id: str):
    persisted = load_week_plan(week_id)
    plan = WeekPlan.from_dict(persisted)
    projected, shopping_stale, projection_error = _project_shopping_safe(
        plan, _fridge_repository().load_catalog_items()
    )
    response_plan = plan.to_dict()
    response_plan["shopping"] = projected
    return {
        "plan": response_plan,
        "version": _plan_version(persisted),
        "shopping_stale": shopping_stale,
        "shopping_projection_error": projection_error,
    }


@app.post("/api/plans/{week_id}/days/{day}/meals")
@_web_audited("add_plan_meal", lambda: Path(PLANS_DIR).parent)
def add_plan_meal(week_id: str, day: str, payload: PlanMealCreate):
    day_code = _require_plan_day(day)
    entry = _plan_meal_entry(payload.dish, payload.portions)
    repo = _plan_repository()
    with repo.lock:
        plan = _require_draft_plan(repo, week_id, payload.expected_version)
        entry.bind_to_plan(plan.week_id, day_code)
        plan.days[day_code].meals.append(entry)
        plan.shopping = {}
        repo.save(plan)
        return {
            "plan": plan.to_dict(),
            "version": _plan_version(plan),
            "meal_index": len(plan.days[day_code].meals) - 1,
        }


@app.patch("/api/plans/{week_id}/meals/{occurrence_id}")
@_web_audited("edit_plan_meal", lambda: Path(PLANS_DIR).parent)
def edit_plan_meal_occurrence(
    week_id: str,
    occurrence_id: str,
    payload: PlanMealStableEdit,
):
    repo = _plan_repository()
    with repo.lock:
        plan = _require_draft_plan(repo, week_id, payload.expected_version)
        matches = [
            meal
            for day in plan.days.values()
            for meal in day.meals
            if meal.occurrence_id == occurrence_id
        ]
        if len(matches) != 1:
            raise HTTPException(404, "Plan meal occurrence not found")
        meal = matches[0]
        candidate = _plan_meal_entry(
            payload.dish, payload.portions, existing_dish=meal.dish
        )
        try:
            meal.revise(
                dish=candidate.dish,
                portions=candidate.portions,
                expected_revision=payload.expected_revision,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        plan.shopping = {}
        repo.save(plan)
        return {"plan": plan.to_dict(), "version": _plan_version(plan)}


@app.patch("/api/plans/{week_id}/days/{day}/meals/{meal_index}")
@_web_audited("edit_plan_meal", lambda: Path(PLANS_DIR).parent)
def edit_plan_meal(
    week_id: str,
    day: str,
    meal_index: int,
    payload: PlanMealEdit,
):
    day_code = _require_plan_day(day)
    repo = _plan_repository()
    with repo.lock:
        plan = _require_draft_plan(repo, week_id, payload.expected_version)
        meals = plan.days[day_code].meals
        if meal_index < 0 or meal_index >= len(meals):
            raise HTTPException(404, "Plan meal not found")
        candidate = _plan_meal_entry(
            payload.dish,
            payload.portions,
            existing_dish=meals[meal_index].dish,
        )
        try:
            meals[meal_index].revise(
                dish=candidate.dish,
                portions=candidate.portions,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        plan.shopping = {}
        repo.save(plan)
        return {"plan": plan.to_dict(), "version": _plan_version(plan)}


@app.delete("/api/plans/{week_id}/meals/{occurrence_id}")
@_web_audited("cancel_plan_meal", lambda: Path(PLANS_DIR).parent)
def cancel_plan_meal(
    week_id: str,
    occurrence_id: str,
    expected_version: str,
    expected_revision: int = Query(ge=1),
):
    repo = _plan_repository()
    audit = _audit_transaction_manager(Path(PLANS_DIR).parent)
    with audit.lock:
        audit.recover()
        with repo.lock:
            plan = _require_draft_plan(repo, week_id, expected_version)
            found = [
                (day_code, meal)
                for day_code, day in plan.days.items()
                for meal in day.meals
                if meal.occurrence_id == occurrence_id
            ]
            if len(found) != 1:
                raise HTTPException(404, "Plan meal occurrence not found")
            day_code, cancelled = found[0]
            try:
                cancelled.transition_to(
                    "cancelled", expected_revision=expected_revision
                )
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            plan.shopping = {}
            after = json.dumps(
                plan.to_dict(), ensure_ascii=False, indent=2
            ).encode("utf-8")
            try:
                transaction = audit.commit(
                    operation="web_cancel_plan_meal",
                    targets={f"plans/{plan.week_id}.json": after},
                    events=[{
                        "event_type": "plan.meal.cancelled.v1",
                        "entity": {
                            "type": "meal_occurrence",
                            "id": cancelled.occurrence_id,
                        },
                        "payload": {
                            "week": plan.week_id,
                            "day": day_code,
                            "dish": cancelled.dish,
                            "portions_planned": cancelled.portions_planned,
                            "revision": cancelled.revision,
                        },
                    }],
                    context={
                        "actor": {"type": "user"},
                        "surface": {
                            "kind": "web",
                            "operation": "cancel_plan_meal",
                        },
                    },
                )
            except Exception:
                transaction = audit.resolve_last_transaction()
                if transaction is None:
                    raise
            return {
                "plan": plan.to_dict(),
                "version": _plan_version(plan),
                "cancelled": cancelled.to_dict(),
                "transaction_id": transaction["transaction_id"],
            }


@app.delete("/api/plans/{week_id}/days/{day}/meals/{meal_index}")
@_web_audited("cancel_plan_meal_legacy", lambda: Path(PLANS_DIR).parent)
def delete_plan_meal(
    week_id: str,
    day: str,
    meal_index: int,
    expected_version: str,
):
    day_code = _require_plan_day(day)
    repo = _plan_repository()
    with repo.lock:
        plan = _require_draft_plan(repo, week_id, expected_version)
        meals = plan.days[day_code].meals
        if meal_index < 0 or meal_index >= len(meals):
            raise HTTPException(404, "Plan meal not found")
        occurrence_id = meals[meal_index].occurrence_id
    result = cancel_plan_meal(
        week_id, occurrence_id, expected_version, meals[meal_index].revision
    )
    result["removed"] = result["cancelled"]
    return result


@app.delete("/api/plans/{week_id}")
@_web_audited("delete_week_plan", lambda: Path(PLANS_DIR).parent)
def delete_week_plan(week_id: str, expected_version: str):
    try:
        normalized_week = WeekPlan.normalize_week_id(week_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    repo = _plan_repository()
    with repo.lock:
        plan = repo.load(normalized_week)
        if plan is None:
            raise HTTPException(404, f"Plan '{normalized_week}' not found or malformed")
        _require_plan_version(plan, expected_version)
        if not repo.delete(normalized_week):
            raise HTTPException(404, f"Plan '{normalized_week}' not found")
    return {"status": "ok", "week": normalized_week}

# ─── API: Audit ──────────────────────────────────────────────────────────
@app.get("/api/audit/events")
def get_audit_events(
    entity_type: str | None = None,
    entity_id: str | None = None,
    event_type: str | None = None,
    since: str | None = None,
    until: str | None = None,
    actor_type: str | None = None,
    surface_kind: str | None = None,
    operation: str | None = None,
    operation_id: str | None = None,
    limit: int = 100,
):
    try:
        events = _audit_transaction_manager().list_events(
            entity_type=entity_type,
            entity_id=entity_id,
            event_type=event_type,
            since=since,
            until=until,
            actor_type=actor_type,
            surface_kind=surface_kind,
            operation=operation,
            operation_id=operation_id,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except (AuditConflictError, OSError) as exc:
        logger.error("Audit history storage failure", exc_info=exc)
        raise HTTPException(503, "Audit history is temporarily unavailable") from exc
    return {"events": events}


@app.get("/api/audit/entities/{entity_type}/{entity_id}")
def get_entity_audit_history(entity_type: str, entity_id: str, limit: int = 100):
    return get_audit_events(entity_type=entity_type, entity_id=entity_id, limit=limit)


# ─── API: Stats ─────────────────────────────────────────────────────────
@app.get("/api/stats")
@_inventory_api_errors
def get_stats():
    dishes = load_dishes()
    inventory_items = _fridge_repository().load_items()
    fridge = list({
        name for item in inventory_items for name in (item.name, *item.aliases)
    })
    try:
        history = load_history()
    except (HistoryDataError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.error("History stats read failure", exc_info=exc)
        raise HTTPException(503, "History storage is temporarily unavailable") from exc
    recent = _recent_dishes(history)

    cookable = sum(1 for d in dishes if _can_cook(d.get("ingredients", {}), fridge))
    total_ingredients_used = Counter()
    for d in dishes:
        for ing in d.get("ingredients", {}):
            total_ingredients_used[ing] += 1

    fridge_utility = {}
    for item in inventory_items:
        identity_names = (item.name, *item.aliases)
        uses = sum(
            1 for dish in dishes
            if any(name in dish.get("ingredients", {}) for name in identity_names)
        )
        fridge_utility[item.name] = uses

    unused_fridge = [
        item.name for item in inventory_items
        if fridge_utility.get(item.name, 0) == 0
    ]

    # History stats
    active_history = [h for h in history if h.get("status") != "retracted"]
    cook_counts = Counter(h.get("dish", "") for h in active_history)
    last_7_days = [
        (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(6, -1, -1)
    ]
    cooks_by_day = {day: 0 for day in last_7_days}
    for h in active_history:
        try:
            day = datetime.fromisoformat(h["date"].replace("Z", "+00:00")).strftime("%Y-%m-%d")
            if day in cooks_by_day:
                cooks_by_day[day] += 1
        except (KeyError, ValueError):
            continue

    return {
        "total_dishes": len(dishes),
        "total_fridge_items": len(inventory_items),
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
