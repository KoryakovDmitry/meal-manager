"""Unit tests for domain logic modules.

These tests are stateless — they test pure functions and dataclass behavior
without touching data files on disk.

Usage:
    python3 test_unit.py
"""

import copy
import importlib
import pathlib
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: make relative imports work when running standalone.
# ---------------------------------------------------------------------------

_PLUGIN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_PLUGIN_DIR.parent))
_pkg = importlib.import_module(_PLUGIN_DIR.name)

_dish_mod = importlib.import_module(".src.dish", _PLUGIN_DIR.name)
_suggestion_mod = importlib.import_module(".src.suggestion", _PLUGIN_DIR.name)
_shopping_mod = importlib.import_module(".src.shopping", _PLUGIN_DIR.name)
_tuning_mod = importlib.import_module(".src.tuning", _PLUGIN_DIR.name)
_handlers_common = importlib.import_module(".src.handlers._common", _PLUGIN_DIR.name)

Dish = _dish_mod.Dish
calculate_score = _suggestion_mod.calculate_score
suggest_dishes = _suggestion_mod.suggest_dishes
suggest_quick_shopping = _shopping_mod.suggest_quick_shopping
tuning = _tuning_mod
_normalize_ingredients = _handlers_common.normalize_ingredients

# ---------------------------------------------------------------------------
# Assertion helper (same style as test_integration.py)
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0


def check(label: str, condition: bool, detail: str = ""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        msg = f"  FAIL  {label}"
        if detail:
            msg += f"  -- {detail}"
        print(msg)


# ---------------------------------------------------------------------------
# Dish tests
# ---------------------------------------------------------------------------


def test_dish_normalize_ingredient():
    print("\n-- Dish.normalize_ingredient --")
    check("strips and lowercases", Dish.normalize_ingredient("  Rice  ") == "rice")
    check("empty string", Dish.normalize_ingredient("   ") == "")
    check("already normalized", Dish.normalize_ingredient("tomato") == "tomato")


def test_dish_normalize_name():
    print("\n-- Dish.normalize_name --")
    check("strips and lowercases", Dish.normalize_name("  Pasta CARBONARA  ") == "pasta carbonara")
    check("empty string", Dish.normalize_name("   ") == "")
    check("already normalized", Dish.normalize_name("tortilla") == "tortilla")


def test_dish_can_cook_with():
    print("\n-- Dish.can_cook_with --")
    dish = Dish(name="test")
    dish.ingredients = {"rice": True, "chicken": True, "pepper": False}

    check("all essentials available", dish.can_cook_with({"rice", "chicken", "pepper"}))
    check("essentials only", dish.can_cook_with({"rice", "chicken"}))
    check("missing essential", not dish.can_cook_with({"rice", "pepper"}))
    check("empty fridge", not dish.can_cook_with(set()))
    check("extra ingredients ok", dish.can_cook_with({"rice", "chicken", "pepper", "salt"}))


def test_dish_can_cook_with_no_ingredients():
    print("\n-- Dish.can_cook_with (no ingredients) --")
    dish = Dish(name="test")
    check("no ingredients = can cook", dish.can_cook_with(set()))


def test_dish_can_cook_with_only_optional():
    print("\n-- Dish.can_cook_with (only optional) --")
    dish = Dish(name="test")
    dish.ingredients = {"salt": False, "pepper": False}
    check("only optionals = can cook", dish.can_cook_with(set()))


def test_dish_to_dict():
    print("\n-- Dish.to_dict --")
    dish = Dish(name="pasta")
    dish.ingredients = {"pasta": True, "sauce": False}
    d = dish.to_dict()
    check("has name", d["name"] == "pasta")
    check("has ingredients", d["ingredients"] == {"pasta": True, "sauce": False})
    check("no prep_time", "prep_time" not in d)


def test_dish_from_dict():
    print("\n-- Dish.from_dict --")
    # Without prep_time
    dish = Dish.from_dict({"name": "Pasta", "ingredients": {"Rice": True}})
    check("name lowercased", dish.name == "pasta")
    check("ingredient lowercased", "rice" in dish.ingredients)

    # With prep_time (backward compat — silently ignored)
    dish2 = Dish.from_dict({"name": "Soup", "prep_time": 20, "ingredients": {"water": True}})
    check("prep_time ignored", dish2.name == "soup")
    check("ingredients loaded", dish2.ingredients == {"water": True})

    # Missing ingredients
    dish3 = Dish.from_dict({"name": "Empty"})
    check("missing ingredients = empty dict", dish3.ingredients == {})


def test_dish_from_dict_invalid():
    print("\n-- Dish.from_dict (invalid) --")
    try:
        Dish.from_dict({"name": "Soup", "ingredients": []})
        check("rejects non-dict ingredients", False, "should have raised ValueError")
    except ValueError:
        check("rejects non-dict ingredients", True)

    try:
        Dish.from_dict({"name": "   ", "ingredients": {}})
        check("rejects blank name", False, "should have raised ValueError")
    except ValueError:
        check("rejects blank name", True)


def test_dish_add_ingredient_validation():
    print("\n-- Dish.add_ingredient (validation) --")
    dish = Dish(name="test")

    try:
        dish.add_ingredient("   ", True)
        check("rejects blank ingredient", False, "should have raised ValueError")
    except ValueError:
        check("rejects blank ingredient", True)

    try:
        dish.add_ingredient("salt", "yes")
        check("rejects non-bool flags", False, "should have raised ValueError")
    except ValueError:
        check("rejects non-bool flags", True)


def test_dish_add_ingredient():
    print("\n-- Dish.add_ingredient --")
    dish = Dish(name="test")
    dish.add_ingredient("  RICE  ", True)
    dish.add_ingredient("Pepper", False)
    check("rice normalized", "rice" in dish.ingredients)
    check("rice is essential", dish.ingredients["rice"] is True)
    check("pepper normalized", "pepper" in dish.ingredients)
    check("pepper is optional", dish.ingredients["pepper"] is False)


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------


def test_calculate_score_basic():
    print("\n-- calculate_score (basic) --")
    dish = Dish(name="test")
    dish.ingredients = {"rice": True, "chicken": True}

    score = calculate_score(dish, {"rice", "chicken"}, 14)
    check("positive score", score > 0, f"got {score}")
    check("max score = 1.0", abs(score - 1.0) < 0.001, f"got {score}")


def test_calculate_score_cooldown():
    print("\n-- calculate_score (cooldown) --")
    dish = Dish(name="test")
    dish.ingredients = {"rice": True}

    check("0 days = blocked", calculate_score(dish, {"rice"}, 0) == 0)
    check("1 day = blocked", calculate_score(dish, {"rice"}, 1) == 0)
    check("2 days = allowed", calculate_score(dish, {"rice"}, 2) > 0)


def test_calculate_score_no_ingredients():
    print("\n-- calculate_score (no ingredients) --")
    dish = Dish(name="test")
    check("empty ingredients = 0", calculate_score(dish, set(), 14) == 0)


def test_calculate_score_partial_ingredients():
    print("\n-- calculate_score (partial) --")
    dish = Dish(name="test")
    dish.ingredients = {"rice": True, "chicken": True, "pepper": False}

    full = calculate_score(dish, {"rice", "chicken", "pepper"}, 14)
    without_optional = calculate_score(dish, {"rice", "chicken"}, 14)
    check("optional increases score", full > without_optional, f"{full} > {without_optional}")


def test_calculate_score_recency_scaling():
    print("\n-- calculate_score (recency scaling) --")
    dish = Dish(name="test")
    dish.ingredients = {"rice": True}

    score_2 = calculate_score(dish, {"rice"}, 2)
    score_7 = calculate_score(dish, {"rice"}, 7)
    score_14 = calculate_score(dish, {"rice"}, 14)
    score_30 = calculate_score(dish, {"rice"}, 30)

    check("more days = higher score", score_2 < score_7 < score_14, f"{score_2}, {score_7}, {score_14}")
    check("14+ days capped", abs(score_14 - score_30) < 0.001, f"{score_14} vs {score_30}")


# ---------------------------------------------------------------------------
# suggest_dishes tests
# ---------------------------------------------------------------------------


def test_suggest_dishes_basic():
    print("\n-- suggest_dishes (basic) --")
    d1 = Dish(name="rice bowl")
    d1.ingredients = {"rice": True}
    d2 = Dish(name="chicken soup")
    d2.ingredients = {"chicken": True, "water": True}

    fridge = {"rice"}
    days = {"rice bowl": 14, "chicken soup": 14}

    result = suggest_dishes([d1, d2], fridge, days)
    check("only rice bowl suggested", len(result) == 1)
    check("correct dish", result[0][0].name == "rice bowl")


def test_suggest_dishes_excludes_recent():
    print("\n-- suggest_dishes (excludes recent) --")
    d1 = Dish(name="rice bowl")
    d1.ingredients = {"rice": True}

    result = suggest_dishes([d1], {"rice"}, {"rice bowl": 1})
    check("cooked yesterday = excluded", len(result) == 0)


def test_suggest_dishes_default_recency():
    print("\n-- suggest_dishes (default recency) --")
    d1 = Dish(name="new dish")
    d1.ingredients = {"rice": True}

    result = suggest_dishes([d1], {"rice"}, {})
    check("new dish suggested", len(result) == 1)


# ---------------------------------------------------------------------------
# suggest_quick_shopping tests
# ---------------------------------------------------------------------------


def test_suggest_quick_shopping_basic():
    print("\n-- suggest_quick_shopping (basic) --")
    d1 = Dish(name="omelette")
    d1.ingredients = {"eggs": True, "oil": True}

    fridge = {"oil"}
    result = suggest_quick_shopping([d1], fridge, {})
    check("one suggestion", len(result) == 1)
    check("missing eggs", result[0][0] == "eggs")
    check("unlocks omelette", "omelette" in result[0][1].lower())


def test_suggest_quick_shopping_two_missing():
    print("\n-- suggest_quick_shopping (two missing) --")
    d1 = Dish(name="omelette")
    d1.ingredients = {"eggs": True, "oil": True}

    result = suggest_quick_shopping([d1], set(), {})
    check("no suggestion when 2 missing", len(result) == 0)


def test_suggest_quick_shopping_groups_by_ingredient():
    print("\n-- suggest_quick_shopping (groups by ingredient) --")
    d1 = Dish(name="fried eggs")
    d1.ingredients = {"eggs": True, "oil": True}
    d2 = Dish(name="omelette")
    d2.ingredients = {"eggs": True, "butter": True}

    fridge = {"oil", "butter"}
    result = suggest_quick_shopping([d1, d2], fridge, {})
    check("eggs unlocks both", len(result) == 1)
    check("ingredient is eggs", result[0][0] == "eggs")


# ---------------------------------------------------------------------------
# _normalize_ingredients tests
# ---------------------------------------------------------------------------


def test_normalize_ingredients_dict():
    print("\n-- _normalize_ingredients (dict) --")
    result = _normalize_ingredients({"Rice": True, "  Chicken ": False})
    check("keys normalized", result == {"rice": True, "chicken": False})


def test_normalize_ingredients_list():
    print("\n-- _normalize_ingredients (list) --")
    result = _normalize_ingredients(["Rice", "Chicken"])
    check("all essential", result == {"rice": True, "chicken": True})


def test_normalize_ingredients_json_string_dict():
    print("\n-- _normalize_ingredients (JSON string dict) --")
    result = _normalize_ingredients('{"Rice": true, "Chicken": false}')
    check("parsed from string", result == {"rice": True, "chicken": False})


def test_normalize_ingredients_json_string_list():
    print("\n-- _normalize_ingredients (JSON string list) --")
    result = _normalize_ingredients('["Rice", "Chicken"]')
    check("parsed from string", result == {"rice": True, "chicken": True})


def test_normalize_ingredients_invalid():
    print("\n-- _normalize_ingredients (invalid) --")
    try:
        _normalize_ingredients(42)
        check("rejects int", False, "should have raised ValueError")
    except ValueError:
        check("rejects int", True)

    try:
        _normalize_ingredients("not json")
        check("rejects bad string", False, "should have raised ValueError")
    except ValueError:
        check("rejects bad string", True)


# ---------------------------------------------------------------------------
# Online weight tuning (src/tuning.py)
# ---------------------------------------------------------------------------


def test_tuning_initial_state():
    print("\n-- tuning.initialize_state --")
    state = tuning.initialize_state()
    check("deploys prior w", state["deployed_match_weight"] == tuning.PRIOR_W)
    check("time weight complements availability",
          abs(state["deployed_match_weight"] + state["deployed_time_weight"] - 1.0) < 1e-9)
    check("zero observations", state["observations"] == 0)
    check("all candidates in band",
          all(tuning.BAND[0] <= w <= tuning.BAND[1] for w in state["candidates"]))
    check("anchor is the initial argmax",
          max(state["candidates"], key=lambda w: tuning._mean(state, tuning._key(w))) == tuning.PRIOR_W)


def test_tuning_deployed_weights_fallback():
    print("\n-- tuning.deployed_weights (fallback) --")
    mw, tw = tuning.deployed_weights({})
    check("falls back to prior blend", mw == tuning.PRIOR_W and abs(mw + tw - 1.0) < 1e-9)


def test_tuning_validate_state():
    print("\n-- tuning.validate_state --")
    good = tuning.initialize_state()
    check("accepts a well-formed state", tuning.validate_state(good) is good)
    check("rejects non-dict", tuning.validate_state("nope")["observations"] == 0)
    check("rejects missing fields", tuning.validate_state({"version": 1})["observations"] == 0)


def test_tuning_compute_rewards_not_cookable():
    print("\n-- tuning.compute_rewards (cooked dish not cookable) --")
    d1 = Dish(name="needs eggs")
    d1.ingredients = {"eggs": True}
    d2 = Dish(name="rice")
    d2.ingredients = {"rice": True}
    rewards = tuning.compute_rewards("needs eggs", [d1, d2], {"rice"}, {}, tuning.CANDIDATES)
    check("returns None (degenerate)", rewards is None)


def test_tuning_compute_rewards_single_dish():
    print("\n-- tuning.compute_rewards (N < 2) --")
    d1 = Dish(name="rice")
    d1.ingredients = {"rice": True}
    rewards = tuning.compute_rewards("rice", [d1], {"rice"}, {}, tuning.CANDIDATES)
    check("returns None (no ranking signal)", rewards is None)


def test_tuning_compute_rewards_top_rank():
    print("\n-- tuning.compute_rewards (top rank -> 1.0) --")
    top = Dish(name="top dish")
    top.ingredients = {"a": True}
    low = Dish(name="low dish")
    low.ingredients = {"b": True, "x": False}
    dishes = [top, low]
    fridge = {"a", "b"}  # optional x absent -> low dish scores strictly lower
    days = {"top dish": 14, "low dish": 2}
    rewards = tuning.compute_rewards("top dish", dishes, fridge, days, tuning.CANDIDATES)
    check("returns a reward dict", rewards is not None)
    check("winning candidate gets reward 1.0",
          abs(rewards[tuning._key(0.60)] - 1.0) < 1e-9, f"got {rewards}")


def test_tuning_apply_update_pure():
    print("\n-- tuning.apply_update (pure, non-mutating) --")
    state = tuning.initialize_state()
    snapshot = copy.deepcopy(state)
    rewards = {tuning._key(w): 1.0 for w in tuning.CANDIDATES}
    new_state = tuning.apply_update(state, rewards)
    check("input left unchanged", state == snapshot)
    check("observations incremented", new_state["observations"] == 1)
    check("count discounted then +1",
          abs(new_state["C"][tuning._key(0.60)]
              - (tuning.GAMMA * snapshot["C"][tuning._key(0.60)] + 1)) < 1e-9)


def _favor_high_w(state, times):
    """Apply a reward monotone in w so the top candidate (0.80) clearly wins."""
    rewards = {tuning._key(w): (w - 0.40) / 0.40 for w in tuning.CANDIDATES}
    for _ in range(times):
        state = tuning.apply_update(state, rewards)
    return tuning.select_deployed(state)


def test_tuning_cold_start():
    print("\n-- tuning.select_deployed (cold start) --")
    state = _favor_high_w(tuning.initialize_state(), tuning.MIN_OBSERVATIONS - 5)
    check("stays at prior below MIN_OBSERVATIONS",
          state["deployed_match_weight"] == tuning.PRIOR_W,
          f"got {state['deployed_match_weight']}")


def test_tuning_shift_after_warmup():
    print("\n-- tuning.select_deployed (shifts once warm) --")
    state = _favor_high_w(tuning.initialize_state(), tuning.MIN_OBSERVATIONS + 20)
    check("shifts upward after MIN_OBSERVATIONS",
          state["deployed_match_weight"] > tuning.PRIOR_W,
          f"got {state['deployed_match_weight']}")
    check("stays within band",
          tuning.BAND[0] <= state["deployed_match_weight"] <= tuning.BAND[1])
    check("weights still sum to 1.0",
          abs(state["deployed_match_weight"] + state["deployed_time_weight"] - 1.0) < 1e-9)


def test_tuning_hysteresis():
    print("\n-- tuning.select_deployed (hysteresis) --")
    state = tuning.initialize_state()
    state["observations"] = tuning.MIN_OBSERVATIONS + 5
    for w in tuning.CANDIDATES:
        key = tuning._key(w)
        state["C"][key] = 1.0
        state["S"][key] = 0.50
    state["S"][tuning._key(0.60)] = 0.60   # current deployed mean
    state["S"][tuning._key(0.65)] = 0.62   # best, but only +0.02 (< margin 0.03)
    result = tuning.select_deployed(state)
    check("sub-margin advantage does not switch deploy",
          result["deployed_match_weight"] == 0.60,
          f"got {result['deployed_match_weight']}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def test_dish_ingredient_keys_normalized_on_construction():
    print("\n-- Dish: ingredient keys normalized on direct construction --")
    d = Dish(name="Soup", ingredients={"  Tomato ": True, "BASIL": False})
    check("ingredient keys stripped+lowercased",
          set(d.ingredients.keys()) == {"tomato", "basil"}, f"got {list(d.ingredients)}")
    check("can_cook_with matches normalized fridge", d.can_cook_with({"tomato"}) is True)


def test_normalize_ingredients_empty_rejected():
    print("\n-- normalize_ingredients: empty rejected --")
    for value in ([], {}, "[]", "{}"):
        try:
            _normalize_ingredients(value)
            check(f"rejects empty {value!r}", False, "should have raised ValueError")
        except ValueError:
            check(f"rejects empty {value!r}", True)


def test_normalize_ingredients_dedup_under_limit():
    print("\n-- normalize_ingredients: dedupes before applying the cap --")
    # A list with many repeats that collapses to a single unique key must be
    # accepted (the cap applies to the de-duplicated result, not the raw list).
    result = _normalize_ingredients(["tomato"] * 150)
    check("repeats collapse to one ingredient", result == {"tomato": True}, f"got {result}")
    # Genuinely too many distinct ingredients is still rejected.
    try:
        _normalize_ingredients([f"ing{i}" for i in range(101)])
        check("rejects >100 distinct ingredients", False, "should have raised")
    except ValueError:
        check("rejects >100 distinct ingredients", True)


def test_tuning_deployed_weights_clamps_out_of_band():
    print("\n-- tuning.deployed_weights (clamp + re-derive) --")
    mw, tw = tuning.deployed_weights(
        {"deployed_match_weight": 1.5, "deployed_time_weight": 0.4}
    )
    check("clamps match weight to band upper bound", mw == tuning.BAND[1], f"got {mw}")
    check("re-derives complementary time weight", abs(mw + tw - 1.0) < 1e-9, f"got {tw}")
    mw2, _ = tuning.deployed_weights({"deployed_match_weight": 0.0})
    check("clamps match weight to band lower bound", mw2 == tuning.BAND[0], f"got {mw2}")


def test_tuning_validate_state_corruption_branches():
    print("\n-- tuning.validate_state (corruption branches) --")

    mismatched = copy.deepcopy(tuning.initialize_state())
    mismatched["candidates"] = [0.1, 0.2, 0.3]
    check("rejects mismatched candidate set",
          tuning.validate_state(mismatched)["observations"] == 0)

    missing_key = copy.deepcopy(tuning.initialize_state())
    missing_key["S"].pop(next(iter(missing_key["S"])))
    check("rejects S/C key mismatch",
          tuning.validate_state(missing_key)["observations"] == 0)

    non_numeric = copy.deepcopy(tuning.initialize_state())
    non_numeric["C"][next(iter(non_numeric["C"]))] = "lots"
    check("rejects non-numeric mass",
          tuning.validate_state(non_numeric)["observations"] == 0)

    boolean_mass = copy.deepcopy(tuning.initialize_state())
    boolean_mass["S"][next(iter(boolean_mass["S"]))] = True
    check("rejects boolean mass (bool is not a valid float here)",
          tuning.validate_state(boolean_mass)["observations"] == 0)


def test_tuning_compute_rewards_no_signal():
    print("\n-- tuning.compute_rewards (no-signal cook returns None) --")
    a = Dish(name="rice bowl", ingredients={"rice": True})
    b = Dish(name="pasta", ingredients={"noodles": True})
    dishes = [a, b]
    fridge = {"rice", "noodles"}
    # The cooked dish is cookable but was cooked today (days=0 < COOLDOWN_DAYS),
    # so it scores 0 for every candidate and carries no learning signal.
    rewards = tuning.compute_rewards(
        "rice bowl", dishes, fridge, {"rice bowl": 0}, tuning.CANDIDATES
    )
    check("cooldown-zeroed cook yields no signal (None)", rewards is None, f"got {rewards}")
    # Sanity contrast: a normal cook does produce a reward dict.
    rewards2 = tuning.compute_rewards(
        "rice bowl", dishes, fridge, {"rice bowl": 14, "pasta": 3}, tuning.CANDIDATES
    )
    check("normal cook produces rewards", isinstance(rewards2, dict) and len(rewards2) > 0)


# ── PrepItem tests ──

_prep_mod = importlib.import_module(".src.prep_item", _PLUGIN_DIR.name)
PrepItem = _prep_mod.PrepItem


def test_prep_item_basic():
    item = PrepItem(name="Hybrid Meatballs", yield_qty=40, yield_unit="шт", storage="freezer")
    check("basic name normalized", item.name == "hybrid meatballs")
    check("yield stored", item.yield_qty == 40)
    check("storage stored", item.storage == "freezer")
    check("remaining defaults to 0", item.remaining == 0)


def test_prep_item_normalizes_name():
    item = PrepItem(name="  Lentil Sauce  ")
    check("name stripped+lowercased", item.name == "lentil sauce")


def test_prep_item_normalizes_ingredients():
    item = PrepItem(name="x", ingredients={"Говядина": True, " Лук ": True})
    check("ingredient keys normalized", "говядина" in item.ingredients and "лук" in item.ingredients)


def test_prep_item_invalid_storage():
    try:
        PrepItem(name="bad", storage="cellar")
        check("invalid storage raises", False)
    except ValueError:
        check("invalid storage raises", True)


def test_prep_item_from_dict():
    data = {
        "name": "Meatballs",
        "ingredients": {"говядина": True, "чечевица": True},
        "yield": 40,
        "yield_unit": "шт",
        "storage": "freezer",
        "remaining": 20,
    }
    item = PrepItem.from_dict(data)
    check("from_dict name", item.name == "meatballs")
    check("from_dict yield", item.yield_qty == 40)
    check("from_dict remaining", item.remaining == 20)
    check("from_dict ingredients", "говядина" in item.ingredients)


def test_prep_item_to_dict_roundtrip():
    item = PrepItem(name="test prep", yield_qty=10, storage="pantry", remaining=5)
    item.ingredients["salt"] = False
    d = item.to_dict()
    restored = PrepItem.from_dict(d)
    check("roundtrip name", restored.name == item.name)
    check("roundtrip yield", restored.yield_qty == item.yield_qty)
    check("roundtrip remaining", restored.remaining == item.remaining)
    check("roundtrip storage", restored.storage == item.storage)
    check("roundtrip ingredients", "salt" in restored.ingredients)


def test_prep_item_remaining_default():
    """When from_dict has no 'remaining', it defaults to yield."""
    data = {"name": "auto", "ingredients": {}, "yield": 30}
    item = PrepItem.from_dict(data)
    check("remaining defaults to yield when absent", item.remaining == 30)


# ── Dish prep_depends tests ──


def test_dish_prep_depends_default_empty():
    d = Dish(name="test dish")
    check("prep_depends defaults to empty list", d.prep_depends == [])


def test_dish_prep_depends_serialized():
    d = Dish(name="test", prep_depends=["hybrid-meatballs"])
    data = d.to_dict()
    check("prep_depends in to_dict", "prep_depends" in data)
    check("prep_depends value", data["prep_depends"] == ["hybrid-meatballs"])


def test_dish_prep_depends_from_dict():
    data = {"name": "soup", "ingredients": {"water": True}, "prep_depends": ["meatballs"]}
    d = Dish.from_dict(data)
    check("prep_depends loaded from dict", d.prep_depends == ["meatballs"])


def test_dish_prep_depends_backward_compat():
    """Old dishes without prep_depends still load fine."""
    data = {"name": "legacy", "ingredients": {"flour": True}}
    d = Dish.from_dict(data)
    check("legacy dish has empty prep_depends", d.prep_depends == [])
    check("legacy dish to_dict has no prep_depends key", "prep_depends" not in d.to_dict())


# ── Weekly plan model tests ──

_plan_mod = importlib.import_module(".src.plan", _PLUGIN_DIR.name)
MealEntry = _plan_mod.MealEntry
DayPlan = _plan_mod.DayPlan
WeekPlan = _plan_mod.WeekPlan


def test_meal_entry_validation():
    meal = MealEntry(dish="  Soup  ", portions=4)
    check("meal dish normalized", meal.dish == "soup")
    check("meal portions retained", meal.portions == 4)
    for bad in (0, -1, True, 1.5):
        try:
            MealEntry(dish="soup", portions=bad)
            check(f"rejects invalid portions {bad!r}", False)
        except ValueError:
            check(f"rejects invalid portions {bad!r}", True)
    try:
        MealEntry(dish="   ", portions=1)
        check("rejects blank dish reference", False)
    except ValueError:
        check("rejects blank dish reference", True)


def test_week_plan_defaults_all_days():
    plan = WeekPlan(week_id="2026-W30")
    check("week plan starts draft", plan.status == "draft")
    check("week plan has seven days", set(plan.days) == set(_plan_mod.DAYS))
    check("week plan days start empty", all(not day.meals for day in plan.days.values()))


def test_week_plan_roundtrip():
    plan = WeekPlan(
        week_id="2026-W30",
        prep=[" Hybrid Meatballs "],
        days={"mon": DayPlan(meals=[MealEntry("Soup", 4)], note="leftovers")},
        leftovers={"soup": {"remaining": 2}},
        shopping={
            "basis": "cooking_occurrences",
            "items": [{
                "ingredient": "carrot",
                "required_uses": 1,
                "available_uses": 0,
                "to_buy": 1,
                "required_by": [{"kind": "dish", "name": "soup", "uses": 1}],
            }],
            "covered_by_fridge": [],
            "prep_to_make": [],
            "unresolved_prep_dependencies": [],
            "prep_capacity_warnings": [],
        },
    )
    restored = WeekPlan.from_dict(plan.to_dict())
    check("plan roundtrip week", restored.week_id == "2026-W30")
    check("plan roundtrip meal", restored.days["mon"].meals[0].dish == "soup")
    check("plan roundtrip note", restored.days["mon"].note == "leftovers")
    check("plan prep normalized", restored.prep == ["hybrid meatballs"])
    check("plan leftovers retained", restored.leftovers["soup"]["remaining"] == 2)
    check("plan shopping retained", restored.shopping["items"][0]["ingredient"] == "carrot")


def test_week_plan_invalid_status():
    try:
        WeekPlan(week_id="2026-W30", status="finished")
        check("invalid plan status rejected", False)
    except ValueError:
        check("invalid plan status rejected", True)


def test_week_plan_rejects_noncanonical_days():
    raw = WeekPlan(week_id="2026-W30").to_dict()
    raw["days"].pop("sun")
    try:
        WeekPlan.from_dict(raw)
        check("partial persisted day map rejected", False)
    except ValueError:
        check("partial persisted day map rejected", True)

    raw = WeekPlan(week_id="2026-W30").to_dict()
    raw["days"]["holiday"] = {"meals": [], "note": ""}
    try:
        WeekPlan.from_dict(raw)
        check("unknown persisted day rejected", False)
    except ValueError:
        check("unknown persisted day rejected", True)


def test_week_plan_rejects_malformed_shopping():
    for malformed in (
        {"items": []},
        {"basis": "cooking_occurrences", "items": [{"ingredient": "carrot"}]},
    ):
        try:
            WeekPlan(week_id="2026-W30", shopping=malformed)
            check("malformed persisted shopping rejected", False)
        except ValueError:
            check("malformed persisted shopping rejected", True)

    source_plan = WeekPlan(
        week_id="2026-W30",
        days={"mon": DayPlan(meals=[MealEntry("soup")])},
    )
    shopping = build_plan_shopping_list(
        plan=source_plan,
        dishes=[Dish(name="soup", ingredients={"carrot": True})],
        prep_items=[],
        fridge=[],
    )
    shopping.update(estimate_shopping_cost(shopping, {"carrot": 2.0}))
    WeekPlan(week_id="2026-W30", shopping=shopping)

    bad_counts = copy.deepcopy(shopping)
    bad_counts["priced_items"] = 0
    try:
        WeekPlan(week_id="2026-W30", shopping=bad_counts)
        check("persisted shopping rejects inconsistent price counts", False)
    except ValueError:
        check("persisted shopping rejects inconsistent price counts", True)

    split = split_shopping_trips(shopping)
    with_trips = copy.deepcopy(shopping)
    with_trips.update({
        "trips": split["trips"],
        "trip_limit": split["trip_limit"],
        "trip_warnings": split["warnings"],
        "unpriced_trip_items": split["unpriced_items"],
    })
    WeekPlan(week_id="2026-W30", shopping=with_trips)
    bad_trip = copy.deepcopy(with_trips)
    bad_trip["trips"][0]["estimated_cost"] = 1.0
    try:
        WeekPlan(week_id="2026-W30", shopping=bad_trip)
        check("persisted shopping rejects inconsistent trip totals", False)
    except ValueError:
        check("persisted shopping rejects inconsistent trip totals", True)

    extra_empty_trip = copy.deepcopy(with_trips)
    extra_empty_trip["trips"].append({
        "trip": 2, "items": [], "estimated_cost": 0.0,
        "limit": 100.0, "over_limit": False,
    })
    try:
        WeekPlan(week_id="2026-W30", shopping=extra_empty_trip)
        check("persisted shopping rejects appended empty trip", False)
    except ValueError:
        check("persisted shopping rejects appended empty trip", True)

    partial = copy.deepcopy(shopping)
    partial.update(estimate_shopping_cost(shopping, {}))
    partial_split = split_shopping_trips(partial, trip_limit=100)
    partial.update({
        "trips": [{
            "trip": 1, "items": [], "estimated_cost": 0.0,
            "limit": 100.0, "over_limit": False,
        }],
        "trip_limit": partial_split["trip_limit"],
        "trip_warnings": partial_split["warnings"],
        "unpriced_trip_items": partial_split["unpriced_items"],
    })
    try:
        WeekPlan(week_id="2026-W30", shopping=partial)
        check("persisted shopping rejects all-empty trip", False)
    except ValueError:
        check("persisted shopping rejects all-empty trip", True)

    def rejects_snapshot(label, snapshot):
        try:
            WeekPlan(week_id="2026-W30", shopping=snapshot)
            check(label, False)
        except ValueError:
            check(label, True)

    priced_without_estimate = copy.deepcopy(shopping)
    for key in (
        "estimated_cost", "complete", "priced_items", "total_items",
        "unpriced_items", "weekly_limit", "weekly_budget_status", "warning",
    ):
        priced_without_estimate.pop(key)
    rejects_snapshot("persisted item pricing requires estimate metadata", priced_without_estimate)

    capacity_detail = {
        "prep_item": "stock", "required_uses": 2, "available_uses": 0,
        "projected_uses": 1, "planned_explicitly": True,
    }
    missing_capacity = copy.deepcopy(build_plan_shopping_list(
        plan=source_plan,
        dishes=[Dish(name="soup", ingredients={"carrot": True})],
        prep_items=[],
        fridge=[],
    ))
    missing_capacity["prep_to_make"] = [capacity_detail]
    rejects_snapshot("persisted shopping requires exact capacity warning", missing_capacity)
    orphan_capacity = copy.deepcopy(missing_capacity)
    orphan_capacity["prep_to_make"] = []
    orphan_capacity["prep_capacity_warnings"] = [capacity_detail]
    rejects_snapshot("persisted shopping rejects orphan capacity warning", orphan_capacity)

    covered_priced = build_plan_shopping_list(
        plan=source_plan,
        dishes=[Dish(name="soup", ingredients={"carrot": True})],
        prep_items=[],
        fridge={"carrot"},
    )
    covered_priced["covered_by_fridge"][0].update({
        "estimated_unit_price": 1.0, "estimated_cost": 0.0,
    })
    rejects_snapshot("persisted covered item cannot retain pricing", covered_priced)

    noncanonical_prep = copy.deepcopy(missing_capacity)
    noncanonical_prep["prep_to_make"][0]["prep_item"] = " Stock "
    noncanonical_prep["prep_capacity_warnings"] = [
        copy.deepcopy(noncanonical_prep["prep_to_make"][0])
    ]
    rejects_snapshot("persisted prep name must be canonical", noncanonical_prep)

    bad_unresolved = copy.deepcopy(missing_capacity)
    bad_unresolved["prep_to_make"] = []
    bad_unresolved["unresolved_prep_dependencies"] = [{
        "prep_item": "stock", "required_uses": 1,
        "reason": "other", "unexpected": True,
    }]
    rejects_snapshot("persisted unresolved prep schema is strict", bad_unresolved)


# ── Phase 3 shopping/budget tests ──

_plan_shopping_mod = importlib.import_module(".src.plan_shopping", _PLUGIN_DIR.name)
build_plan_shopping_list = _plan_shopping_mod.build_plan_shopping_list
estimate_shopping_cost = _plan_shopping_mod.estimate_shopping_cost
split_shopping_trips = _plan_shopping_mod.split_shopping_trips


def test_build_plan_shopping_list_aggregates_occurrences_and_prep():
    plan = WeekPlan(
        week_id="2026-W30",
        prep=["stock"],
        days={
            "mon": DayPlan(meals=[MealEntry("soup", 4)]),
            "tue": DayPlan(meals=[MealEntry("soup", 2)]),
        },
    )
    dishes = [
        Dish(
            name="soup",
            ingredients={"carrot": True, "water": True, "salt": False},
        )
    ]
    prep_items = [
        PrepItem(
            name="stock",
            ingredients={"bones": True, "water": True, "pepper": False},
            yield_qty=4,
        )
    ]

    result = build_plan_shopping_list(
        plan=plan,
        dishes=dishes,
        prep_items=prep_items,
        fridge={"carrot", "water"},
    )
    items = {item["ingredient"]: item for item in result["items"]}
    check("shopping includes aggregated carrot shortage", items["carrot"]["to_buy"] == 1)
    check("shopping aggregates shared water demand", items["water"]["to_buy"] == 2)
    check("shopping includes planned prep source", items["bones"]["to_buy"] == 1)
    check("shopping excludes optional dish ingredient", "salt" not in items)
    check("shopping excludes optional prep ingredient", "pepper" not in items)
    check("shopping declares occurrence basis", result["basis"] == "cooking_occurrences")


def test_build_plan_shopping_list_adds_sources_for_depleted_dependencies():
    plan = WeekPlan(
        week_id="2026-W30",
        days={
            "mon": DayPlan(meals=[MealEntry("soup")]),
            "tue": DayPlan(meals=[MealEntry("soup")]),
        },
    )
    dish = Dish(name="soup", ingredients={"water": True})
    dish.prep_depends = ["stock"]
    stock = PrepItem(
        name="stock",
        ingredients={"bones": True},
        yield_qty=4,
        remaining=1,
    )
    result = build_plan_shopping_list(
        plan=plan, dishes=[dish], prep_items=[stock], fridge=set()
    )
    items = {item["ingredient"]: item for item in result["items"]}
    check("depleted prep adds source ingredient", items["bones"]["to_buy"] == 1)
    check("depleted prep is scheduled for one batch", result["prep_to_make"][0]["prep_item"] == "stock")
    check("prep demand is reported", result["prep_to_make"][0]["required_uses"] == 2)
    check("prep current availability is reported", result["prep_to_make"][0]["available_uses"] == 1)


def test_explicit_prep_uses_replacement_yield_semantics():
    plan = WeekPlan(week_id="2026-W30", prep=["stock"])
    stock = PrepItem(
        name="stock",
        ingredients={"bones": True},
        yield_qty=3,
        remaining=5,
    )
    result = build_plan_shopping_list(
        plan=plan,
        dishes={},
        prep_items=[stock],
        fridge=[],
    )
    prep_plan = result["prep_to_make"][0]
    check("explicit prep appears in prep schedule", prep_plan["prep_item"] == "stock")
    check("explicit prep reports current remaining", prep_plan["available_uses"] == 5)
    check("make prep replacement projects yield, not max", prep_plan["projected_uses"] == 3)


def test_estimate_shopping_cost_is_soft_and_partial_safe():
    shopping = {
        "items": [
            {"ingredient": "carrot", "to_buy": 2},
            {"ingredient": "bones", "to_buy": 1},
        ]
    }
    partial = estimate_shopping_cost(shopping, {"carrot": 1.5})
    check("partial cost keeps known subtotal", partial["estimated_cost"] == 3.0)
    check("partial cost is incomplete", partial["complete"] is False)
    check("partial cost budget status unknown", partial["weekly_budget_status"] == "unknown")
    check("partial cost lists unpriced ingredient", partial["unpriced_items"] == ["bones"])

    complete = estimate_shopping_cost(
        shopping,
        {"carrot": 60.0, "bones": 40.0},
        weekly_limit=150.0,
    )
    check("complete cost sums shopping units", complete["estimated_cost"] == 160.0)
    check("complete cost reports over budget", complete["weekly_budget_status"] == "over")
    check("budget overage is informational", complete["warning"] is not None)

    repriced = estimate_shopping_cost(complete, {"carrot": 1.0})
    repriced_bones = next(item for item in repriced["items"] if item["ingredient"] == "bones")
    check("partial re-estimate clears stale unit price", "estimated_unit_price" not in repriced_bones)
    check("partial re-estimate clears stale item cost", "estimated_cost" not in repriced_bones)
    repriced_trips = split_shopping_trips(repriced)
    check("trip split treats newly unpriced item as unpriced", repriced_trips["unpriced_items"] == ["bones"])


def test_cost_inputs_reject_non_finite_numbers():
    shopping = {"items": [{"ingredient": "carrot", "to_buy": 1}]}
    for bad in (float("nan"), float("inf"), float("-inf")):
        try:
            estimate_shopping_cost(shopping, {"carrot": bad})
            check(f"rejects non-finite price {bad}", False)
        except ValueError:
            check(f"rejects non-finite price {bad}", True)

    try:
        estimate_shopping_cost({"items": [{"ingredient": "carrot", "to_buy": 2}]}, {"carrot": 1e308})
        check("rejects non-finite derived line cost", False)
    except ValueError:
        check("rejects non-finite derived line cost", True)

    tiny = estimate_shopping_cost(shopping, {"carrot": 0.001})
    tiny_split = split_shopping_trips(tiny)
    check("tiny rounded cost remains splittable", tiny_split["trips"][0]["estimated_cost"] == 0.0)

    huge = 10 ** 1000
    for label, operation in (
        ("huge price", lambda: estimate_shopping_cost(shopping, {"carrot": huge})),
        ("huge weekly limit", lambda: estimate_shopping_cost(shopping, {}, weekly_limit=huge)),
        ("huge trip limit", lambda: split_shopping_trips(tiny, trip_limit=huge)),
        ("huge to_buy", lambda: estimate_shopping_cost({**shopping, "items": [{"ingredient": "carrot", "to_buy": huge}]}, {"carrot": 1})),
    ):
        try:
            operation()
            check(f"rejects {label} without overflow", False)
        except (ValueError, OverflowError) as exc:
            check(f"rejects {label} without overflow", isinstance(exc, ValueError))


def test_split_shopping_trips_respects_soft_limit():
    costed = {
        "items": [
            {"ingredient": "a", "to_buy": 1, "estimated_cost": 60.0},
            {"ingredient": "b", "to_buy": 1, "estimated_cost": 50.0},
            {"ingredient": "c", "to_buy": 1, "estimated_cost": 40.0},
            {"ingredient": "bones", "to_buy": 1},
        ]
    }
    result = split_shopping_trips(costed, trip_limit=100.0)
    check("trip splitter creates two priced trips", len(result["trips"]) == 2)
    check("trip splitter first-fits to exact limit", result["trips"][0]["estimated_cost"] == 100.0)
    check("trip splitter keeps each normal trip within limit", all(t["estimated_cost"] <= 100.0 for t in result["trips"]))
    check("trip splitter preserves unpriced items", result["unpriced_items"] == ["bones"])

    oversized = split_shopping_trips(
        {"items": [{"ingredient": "special", "to_buy": 1, "estimated_cost": 120.0}]},
        trip_limit=100.0,
    )
    check("oversized item gets its own trip", len(oversized["trips"]) == 1)
    check("oversized item is a soft warning", oversized["trips"][0]["over_limit"] is True)

    try:
        split_shopping_trips({"items": [{"ingredient": "broken", "estimated_cost": 1.0}]})
        check("trip split rejects item missing to_buy", False)
    except ValueError:
        check("trip split rejects item missing to_buy", True)


# ── Structured inventory item tests ──


def test_inventory_item_roundtrip():
    print("\n-- InventoryItem roundtrip --")
    try:
        inventory_mod = importlib.import_module(".src.inventory", _PLUGIN_DIR.name)
    except ModuleNotFoundError:
        check("inventory module exists", False, "src/inventory.py is missing")
        return

    item = inventory_mod.InventoryItem(
        id="inv_test",
        name="  Куриные Голени  ",
        available=False,
        quantity="2.000",
        unit="kg",
        package_count=1,
        storage="fridge",
        expires_on="2026-07-17",
        comment="  сырые  ",
        created_at="2026-07-14T01:15:00+00:00",
        updated_at="2026-07-14T01:15:00+00:00",
    )
    payload = item.to_dict()
    restored = inventory_mod.InventoryItem.from_dict(payload)

    check("inventory name normalized", item.name == "куриные голени")
    check("quantity canonicalized", item.quantity == "2")
    check("comment trimmed", item.comment == "сырые")
    check("availability roundtrips", restored.available is False)
    positional = inventory_mod.InventoryItem(
        "inv_positional", "рис", "2", "kg", None, None, None, None,
        "2026-07-14T01:15:00+00:00", "2026-07-14T01:15:00+00:00",
    )
    check("positional inventory constructor remains compatible", positional.quantity == "2" and positional.available is True)
    check("legacy public inventory shape hides availability", "available" not in item.to_public_dict())
    check("inventory roundtrip exact", restored == item)
    check("serialized fields are complete", set(payload) == {
        "id", "name", "available", "quantity", "unit", "package_count", "storage",
        "expires_on", "comment", "created_at", "updated_at",
    })


def test_inventory_item_validation():
    print("\n-- InventoryItem validation --")
    inventory_mod = importlib.import_module(".src.inventory", _PLUGIN_DIR.name)
    base = {
        "id": "inv_test",
        "name": "рис",
        "created_at": "2026-07-14T01:15:00+00:00",
        "updated_at": "2026-07-14T01:15:00+00:00",
    }
    invalid_cases = [
        ({"id": ""}, "blank id"),
        ({"name": "   "}, "blank name"),
        ({"name": None}, "null name"),
        ({"name": "x" * 201}, "overlong name"),
        ({"available": "yes"}, "non-boolean availability"),
        ({"quantity": "2"}, "quantity without unit"),
        ({"unit": "kg"}, "unit without quantity"),
        ({"quantity": True, "unit": "kg"}, "boolean quantity"),
        ({"quantity": "0", "unit": "kg"}, "zero quantity"),
        ({"quantity": "-1", "unit": "kg"}, "negative quantity"),
        ({"quantity": "NaN", "unit": "kg"}, "non-finite quantity"),
        ({"quantity": "1.1234567", "unit": "kg"}, "excessive quantity precision"),
        ({"quantity": "1000000001", "unit": "kg"}, "unsafe quantity magnitude"),
        ({"quantity": "2", "unit": "stone"}, "unknown unit"),
        ({"package_count": True}, "boolean package count"),
        ({"package_count": 0}, "zero package count"),
        ({"package_count": 10001}, "unsafe package count"),
        ({"storage": "cellar"}, "unknown storage"),
        ({"expires_on": "2026-02-30"}, "invalid expiry date"),
        ({"expires_on": "20260717"}, "compact expiry date"),
        ({"expires_on": "2026-W29-5"}, "week expiry date"),
        ({"comment": "x" * 1001}, "overlong comment"),
        ({"created_at": "not-a-date"}, "invalid created timestamp"),
        ({"created_at": "2026-07-15T01:15:00+00:00"}, "updated timestamp before created"),
    ]
    for patch_values, label in invalid_cases:
        try:
            inventory_mod.InventoryItem(**(base | patch_values))
            check(f"rejects {label}", False)
        except (TypeError, ValueError, ArithmeticError):
            check(f"rejects {label}", True)

    try:
        inventory_mod.InventoryItem.from_dict(base | {"unknown": "field"})
        check("rejects unknown persisted fields", False)
    except (TypeError, ValueError):
        check("rejects unknown persisted fields", True)


def test_inventory_item_expiry_status():
    print("\n-- InventoryItem expiry status --")
    from datetime import date

    inventory_mod = importlib.import_module(".src.inventory", _PLUGIN_DIR.name)
    base = {
        "id": "inv_test",
        "name": "рис",
        "created_at": "2026-07-14T01:15:00+00:00",
        "updated_at": "2026-07-14T01:15:00+00:00",
    }
    statuses = {
        None: "unknown",
        "2026-07-13": "expired",
        "2026-07-14": "expiring_soon",
        "2026-07-17": "expiring_soon",
        "2026-07-18": "ok",
    }
    for expires_on, expected in statuses.items():
        item = inventory_mod.InventoryItem(**base, expires_on=expires_on)
        check(
            f"expiry {expires_on} -> {expected}",
            item.expiry_status(today=date(2026, 7, 14)) == expected,
        )


def test_product_catalog_statuses_and_filters():
    print("\n-- product catalog statuses and filters --")
    catalog_mod = importlib.import_module(".src.product_catalog", _PLUGIN_DIR.name)
    inventory_mod = importlib.import_module(".src.inventory", _PLUGIN_DIR.name)
    stamp = "2026-07-14T01:15:00+00:00"
    items = [
        inventory_mod.InventoryItem(
            id="inv_rice", name="рис", available=True,
            created_at=stamp, updated_at=stamp,
        ),
        inventory_mod.InventoryItem(
            id="inv_milk", name="молоко", available=False,
            storage="fridge", created_at=stamp, updated_at=stamp,
        ),
    ]
    dishes = [
        Dish("каша", {"молоко": True, "томаты": False}),
        Dish("салат", {"томаты": True}),
    ]

    rows = catalog_mod.build_product_catalog(items, dishes)
    by_name = {row["name"]: row for row in rows}
    check("current product is in_stock", by_name["рис"]["status"] == "in_stock")
    check("removed product is out_of_stock", by_name["молоко"]["status"] == "out_of_stock")
    check("recipe-only product is distinguished", by_name["томаты"]["status"] == "recipe_only")
    check("recipe usage count is aggregated", by_name["томаты"]["recipe_count"] == 2)
    filtered = catalog_mod.build_product_catalog(
        items, dishes, status="recipe_only", query="ТОМ",
    )
    check("catalog status and query filters compose", [row["name"] for row in filtered] == ["томаты"])
    for kwargs in ({"status": "missing"}, {"query": "x" * 201}):
        try:
            catalog_mod.build_product_catalog(items, dishes, **kwargs)
            check("catalog rejects invalid filter", False)
        except ValueError:
            check("catalog rejects invalid filter", True)


def main():
    test_dish_normalize_ingredient()
    test_dish_normalize_name()
    test_dish_can_cook_with()
    test_dish_can_cook_with_no_ingredients()
    test_dish_can_cook_with_only_optional()
    test_dish_to_dict()
    test_dish_from_dict()
    test_dish_from_dict_invalid()
    test_dish_add_ingredient()
    test_dish_add_ingredient_validation()
    test_dish_ingredient_keys_normalized_on_construction()

    test_calculate_score_basic()
    test_calculate_score_cooldown()
    test_calculate_score_no_ingredients()
    test_calculate_score_partial_ingredients()
    test_calculate_score_recency_scaling()

    test_suggest_dishes_basic()
    test_suggest_dishes_excludes_recent()
    test_suggest_dishes_default_recency()

    test_suggest_quick_shopping_basic()
    test_suggest_quick_shopping_two_missing()
    test_suggest_quick_shopping_groups_by_ingredient()

    test_normalize_ingredients_dict()
    test_normalize_ingredients_list()
    test_normalize_ingredients_json_string_dict()
    test_normalize_ingredients_json_string_list()
    test_normalize_ingredients_invalid()
    test_normalize_ingredients_empty_rejected()
    test_normalize_ingredients_dedup_under_limit()

    test_tuning_initial_state()
    test_tuning_deployed_weights_fallback()
    test_tuning_deployed_weights_clamps_out_of_band()
    test_tuning_validate_state()
    test_tuning_validate_state_corruption_branches()
    test_tuning_compute_rewards_not_cookable()
    test_tuning_compute_rewards_single_dish()
    test_tuning_compute_rewards_top_rank()
    test_tuning_compute_rewards_no_signal()
    test_tuning_apply_update_pure()
    test_tuning_cold_start()
    test_tuning_shift_after_warmup()
    test_tuning_hysteresis()

    # ── PrepItem model ──
    print("\n-- PrepItem model --")
    test_prep_item_basic()
    test_prep_item_normalizes_name()
    test_prep_item_normalizes_ingredients()
    test_prep_item_invalid_storage()
    test_prep_item_from_dict()
    test_prep_item_to_dict_roundtrip()
    test_prep_item_remaining_default()

    # ── Dish prep_depends ──
    print("\n-- Dish prep_depends --")
    test_dish_prep_depends_default_empty()
    test_dish_prep_depends_serialized()
    test_dish_prep_depends_from_dict()
    test_dish_prep_depends_backward_compat()

    # ── Weekly plan model ──
    print("\n-- Weekly plan model --")
    test_meal_entry_validation()
    test_week_plan_defaults_all_days()
    test_week_plan_roundtrip()
    test_week_plan_invalid_status()
    test_week_plan_rejects_noncanonical_days()
    test_week_plan_rejects_malformed_shopping()

    test_inventory_item_roundtrip()
    test_inventory_item_validation()
    test_inventory_item_expiry_status()
    test_product_catalog_statuses_and_filters()

    print("\n-- Phase 3 shopping/budget --")
    test_build_plan_shopping_list_aggregates_occurrences_and_prep()
    test_build_plan_shopping_list_adds_sources_for_depleted_dependencies()
    test_explicit_prep_uses_replacement_yield_semantics()
    test_estimate_shopping_cost_is_soft_and_partial_safe()
    test_cost_inputs_reject_non_finite_numbers()
    test_split_shopping_trips_respects_soft_limit()

    print(f"\n{'='*40}")
    print(f"  {_passed} passed, {_failed} failed")
    print(f"{'='*40}")
    sys.exit(1 if _failed else 0)


if __name__ == "__main__":
    main()
