#!/usr/bin/env python3
"""Meal Planning Web Interface — FastAPI backend.

Reads/writes the same JSON files used by the Hermes meal_manager plugin,
ensuring full synchronization between the Telegram bot, agent, and web UI.
"""

import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ─── Configuration ──────────────────────────────────────────────────────
# Resolve data dir: allow override via MEAL_DATA_DIR env var,
# otherwise default to the plugin's data/ directory.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(__import__("os").environ.get("MEAL_DATA_DIR", PLUGIN_ROOT / "data"))
DISHES_PATH = DATA_DIR / "dishes.json"
FRIDGE_PATH = DATA_DIR / "fridge.json"
HISTORY_PATH = DATA_DIR / "history.json"
TUNING_PATH = DATA_DIR / "tuning.json"

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
    except (FileNotFoundError, json.JSONDecodeError):
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

def load_fridge():
    return _read_json(FRIDGE_PATH, [])

def save_fridge(items):
    _write_json(FRIDGE_PATH, items)

def load_history():
    data = _read_json(HISTORY_PATH, {"history": []})
    return data.get("history", [])

def save_history(entries):
    _write_json(HISTORY_PATH, {"history": entries})

def load_tuning():
    return _read_json(TUNING_PATH, {})

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
@app.get("/api/fridge")
def get_fridge():
    return {"ingredients": load_fridge()}

@app.put("/api/fridge")
def set_fridge(payload: FridgeUpdate):
    with _lock:
        items = sorted(set(_normalize(i) for i in payload.ingredients))
        save_fridge(items)
    return {"ingredients": items}

@app.post("/api/fridge/add")
def add_to_fridge(payload: FridgeAddRemove):
    with _lock:
        fridge = load_fridge()
        ing = _normalize(payload.ingredient)
        if ing not in fridge:
            fridge.append(ing)
            fridge.sort()
            save_fridge(fridge)
    return {"ingredients": fridge}

@app.post("/api/fridge/remove")
def remove_from_fridge(payload: FridgeAddRemove):
    with _lock:
        fridge = load_fridge()
        ing = _normalize(payload.ingredient)
        fridge = [i for i in fridge if i != ing]
        save_fridge(fridge)
    return {"ingredients": fridge}

@app.delete("/api/fridge")
def clear_fridge():
    with _lock:
        save_fridge([])
    return {"status": "ok", "ingredients": []}

# ─── API: Suggestions & Shopping ────────────────────────────────────────
@app.get("/api/suggestions")
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
def get_shopping():
    dishes = load_dishes()
    fridge = load_fridge()
    return {"items": _shopping_list(dishes, fridge)}

# ─── API: History ───────────────────────────────────────────────────────
@app.get("/api/history")
def get_history():
    return {"history": load_history()}

@app.post("/api/history")
def add_history(payload: CookedMeal):
    with _lock:
        history = load_history()
        entry = {
            "dish": _normalize(payload.dish),
            "date": payload.date or datetime.now().isoformat(),
        }
        history.append(entry)
        save_history(history)
        # Remove essential ingredients from fridge (same as register_cooked_meal)
        dishes = load_dishes()
        dish = next((d for d in dishes if _normalize(d["name"]) == entry["dish"]), None)
        if dish:
            fridge = load_fridge()
            essentials_to_remove = {
                ing for ing, ess in dish.get("ingredients", {}).items() if ess
            }
            fridge = [i for i in fridge if i not in essentials_to_remove]
            save_fridge(fridge)
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

# ─── API: Stats ─────────────────────────────────────────────────────────
@app.get("/api/stats")
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
        "top_ingredients": total_ingredients_used.most_common(10),
        "most_cooked": cook_counts.most_common(5),
        "cooks_last_7_days": cooks_by_day,
        "tuning": load_tuning(),
    }

# ─── Serve frontend ─────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8088)
