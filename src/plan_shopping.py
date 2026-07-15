"""Pure weekly-plan shopping and soft-budget calculations."""

from collections import Counter, defaultdict
from math import isfinite


_MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _finite_bounded_number(value, *, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError(f"{label} exceeds the supported numeric range")
        return float(value)
    if not isfinite(value) or abs(value) > _MAX_SAFE_INTEGER:
        raise ValueError(f"{label} must be finite and within the supported range")
    return float(value)


def _positive_number(value, *, label):
    number = _finite_bounded_number(value, label=label)
    if number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


def _nonnegative_number(value, *, label):
    number = _finite_bounded_number(value, label=label)
    if number < 0:
        raise ValueError(f"{label} must be non-negative")
    return number


def _bounded_int(value, *, label, minimum=0):
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= _MAX_SAFE_INTEGER
    ):
        raise ValueError(f"{label} must be an integer from {minimum} to {_MAX_SAFE_INTEGER}")
    return value


def _nonempty_name(value, *, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _canonical_name(value, *, label):
    value = _nonempty_name(value, label=label)
    if value != value.strip().lower():
        raise ValueError(f"{label} must be canonical lowercase without outer whitespace")
    return value


def _validate_persisted_item(item, *, covered=False):
    base_keys = {
        "ingredient", "required_uses", "available_uses", "to_buy", "required_by",
    }
    price_keys = {"estimated_unit_price", "estimated_cost"}
    if not isinstance(item, dict) or frozenset(item) not in {frozenset(base_keys), frozenset(base_keys | price_keys)}:
        raise ValueError("shopping items must match the generated schema")
    ingredient = _canonical_name(item.get("ingredient"), label="shopping ingredient")
    required = _bounded_int(item.get("required_uses"), label=f"required uses for '{ingredient}'", minimum=1)
    available = _bounded_int(item.get("available_uses"), label=f"available uses for '{ingredient}'")
    to_buy = _bounded_int(item.get("to_buy"), label=f"to_buy for '{ingredient}'")
    if to_buy != max(0, required - available) or (covered != (to_buy == 0)):
        raise ValueError(f"shopping quantities for '{ingredient}' are inconsistent")
    required_by = item.get("required_by")
    if not isinstance(required_by, list) or not required_by:
        raise ValueError(f"required_by for '{ingredient}' must be a non-empty list")
    source_uses = 0
    for source in required_by:
        if (
            not isinstance(source, dict)
            or set(source) != {"kind", "name", "uses"}
            or source.get("kind") not in {"dish", "prep", "manual"}
        ):
            raise ValueError(f"required_by source for '{ingredient}' is invalid")
        _canonical_name(source.get("name"), label="required_by name")
        source_uses += _bounded_int(source.get("uses"), label="required_by uses", minimum=1)
    if source_uses != required:
        raise ValueError(f"required_by uses for '{ingredient}' do not match required_uses")
    has_unit = "estimated_unit_price" in item
    has_cost = "estimated_cost" in item
    if has_unit != has_cost:
        raise ValueError(f"pricing fields for '{ingredient}' must appear together")
    if covered and has_unit:
        raise ValueError("covered_by_fridge items cannot contain pricing fields")
    if has_unit:
        unit = _positive_number(item["estimated_unit_price"], label=f"unit price for '{ingredient}'")
        cost = _nonnegative_number(item["estimated_cost"], label=f"cost for '{ingredient}'")
        raw_cost = unit * to_buy
        if not isfinite(raw_cost) or raw_cost > _MAX_SAFE_INTEGER or round(raw_cost, 2) != cost:
            raise ValueError(f"cost for '{ingredient}' is inconsistent")
    return ingredient, has_cost, item.get("estimated_cost", 0.0)


def _validate_prep_detail(item, *, capacity=False):
    expected_keys = {
        "prep_item", "required_uses", "available_uses",
        "projected_uses", "planned_explicitly",
    }
    if not isinstance(item, dict) or set(item) != expected_keys:
        raise ValueError("prep schedule entries must match the generated schema")
    _canonical_name(item.get("prep_item"), label="prep item name")
    required = _bounded_int(item.get("required_uses"), label="prep required_uses")
    _bounded_int(item.get("available_uses"), label="prep available_uses")
    projected = _bounded_int(item.get("projected_uses"), label="prep projected_uses", minimum=1)
    if not isinstance(item.get("planned_explicitly"), bool):
        raise ValueError("prep planned_explicitly must be boolean")
    if capacity and projected >= required:
        raise ValueError("prep capacity warning must describe an actual shortfall")


def validate_shopping_snapshot(shopping):
    """Validate the complete persisted shopping snapshot and its invariants."""
    if not isinstance(shopping, dict):
        raise ValueError("shopping must be a dict")
    if not shopping:
        return shopping
    if shopping.get("basis") != "cooking_occurrences":
        raise ValueError("shopping basis must be cooking_occurrences")
    allowed_keys = {
        "basis", "items", "covered_by_fridge", "prep_to_make",
        "unresolved_prep_dependencies", "prep_capacity_warnings",
        "estimated_cost", "complete", "priced_items", "total_items",
        "unpriced_items", "weekly_limit", "weekly_budget_status", "warning",
        "trips", "unpriced_trip_items", "trip_limit", "trip_warnings",
    }
    if set(shopping) - allowed_keys:
        raise ValueError("shopping contains unsupported fields")
    for key in (
        "items", "covered_by_fridge", "prep_to_make",
        "unresolved_prep_dependencies", "prep_capacity_warnings",
    ):
        if not isinstance(shopping.get(key), list):
            raise ValueError(f"shopping {key} must be a list")

    item_rows = [_validate_persisted_item(item) for item in shopping["items"]]
    covered_rows = [
        _validate_persisted_item(item, covered=True)
        for item in shopping["covered_by_fridge"]
    ]
    ingredients = [row[0] for row in item_rows + covered_rows]
    if len(ingredients) != len(set(ingredients)):
        raise ValueError("shopping ingredients must be unique")
    for item in shopping["prep_to_make"]:
        _validate_prep_detail(item)
    prep_names = [item["prep_item"] for item in shopping["prep_to_make"]]
    if len(prep_names) != len(set(prep_names)):
        raise ValueError("prep_to_make entries must be unique")
    for item in shopping["prep_capacity_warnings"]:
        _validate_prep_detail(item, capacity=True)
    expected_capacity = [
        item for item in shopping["prep_to_make"]
        if item["projected_uses"] < item["required_uses"]
    ]
    if shopping["prep_capacity_warnings"] != expected_capacity:
        raise ValueError("prep_capacity_warnings must exactly match projected shortfalls")
    unresolved_names = []
    for item in shopping["unresolved_prep_dependencies"]:
        if not isinstance(item, dict) or set(item) != {
            "prep_item", "required_uses", "reason",
        }:
            raise ValueError("unresolved prep entries must match the generated schema")
        name = _canonical_name(item.get("prep_item"), label="unresolved prep item")
        unresolved_names.append(name)
        _bounded_int(item.get("required_uses"), label="unresolved prep required_uses", minimum=1)
        if item.get("reason") != "not_defined":
            raise ValueError("unresolved prep reason must be not_defined")
    if len(unresolved_names) != len(set(unresolved_names)):
        raise ValueError("unresolved prep entries must be unique")

    estimate_keys = {
        "estimated_cost", "complete", "priced_items", "total_items",
        "unpriced_items", "weekly_limit", "weekly_budget_status", "warning",
    }
    estimate_present = estimate_keys.intersection(shopping)
    if estimate_present and estimate_present != estimate_keys:
        raise ValueError("shopping cost estimate fields are incomplete")
    has_item_pricing = any(row[1] for row in item_rows)
    if has_item_pricing and not estimate_present:
        raise ValueError("item pricing requires complete top-level estimate metadata")
    if estimate_present:
        subtotal = _nonnegative_number(shopping["estimated_cost"], label="estimated cost")
        limit = _positive_number(shopping["weekly_limit"], label="weekly limit")
        priced_names = sorted(row[0] for row in item_rows if row[1])
        unpriced_names = sorted(row[0] for row in item_rows if not row[1])
        calculated = round(sum(row[2] for row in item_rows if row[1]), 2)
        if not isfinite(calculated) or calculated != subtotal:
            raise ValueError("estimated subtotal does not match item costs")
        priced_count = _bounded_int(shopping["priced_items"], label="priced_items")
        total_count = _bounded_int(shopping["total_items"], label="total_items")
        if priced_count != len(priced_names) or total_count != len(item_rows):
            raise ValueError("shopping price coverage counts are inconsistent")
        if shopping["unpriced_items"] != unpriced_names:
            raise ValueError("unpriced_items does not match item pricing")
        complete = shopping["complete"]
        if not isinstance(complete, bool) or complete != (not unpriced_names):
            raise ValueError("shopping complete flag is inconsistent")
        expected_status = "unknown" if unpriced_names else ("over" if subtotal > limit else "within")
        if shopping["weekly_budget_status"] != expected_status:
            raise ValueError("weekly budget status is inconsistent")
        warning = shopping["warning"]
        expected_warning = None
        if expected_status == "unknown":
            expected_warning = "Cost estimate is incomplete; weekly budget status is unknown."
        elif expected_status == "over":
            expected_warning = f"Estimated weekly cost exceeds the soft €{limit:.2f} limit."
        if warning != expected_warning:
            raise ValueError("shopping warning is inconsistent")

    trip_keys = {"trips", "unpriced_trip_items", "trip_limit", "trip_warnings"}
    trip_present = trip_keys.intersection(shopping)
    if trip_present and trip_present != trip_keys:
        raise ValueError("shopping trip fields are incomplete")
    if trip_present and not estimate_present:
        raise ValueError("shopping trips require a complete cost estimate snapshot")
    if trip_present:
        trip_limit = _positive_number(shopping["trip_limit"], label="trip limit")
        if not isinstance(shopping["trips"], list):
            raise ValueError("trips must be a list")
        assigned = []
        top_items = {item["ingredient"]: item for item in shopping["items"]}
        for index, trip in enumerate(shopping["trips"], 1):
            if (
                not isinstance(trip, dict)
                or set(trip) != {"trip", "items", "estimated_cost", "limit", "over_limit"}
                or trip.get("trip") != index
            ):
                raise ValueError("trips must match the generated schema with sequential numbers")
            if trip.get("limit") != trip_limit or not isinstance(trip.get("over_limit"), bool):
                raise ValueError("trip limit metadata is inconsistent")
            if not isinstance(trip.get("items"), list) or not trip["items"]:
                raise ValueError("trip items must be a non-empty list")
            rows = [_validate_persisted_item(item) for item in trip["items"]]
            if not all(row[1] for row in rows):
                raise ValueError("trip items must be priced")
            if any(top_items.get(item["ingredient"]) != item for item in trip["items"]):
                raise ValueError("trip items must match top-level shopping items")
            trip_cost = _nonnegative_number(trip.get("estimated_cost"), label="trip cost")
            calculated = round(sum(row[2] for row in rows), 2)
            if calculated != trip_cost or trip["over_limit"] != (trip_cost > trip_limit):
                raise ValueError("trip totals or over-limit flag are inconsistent")
            assigned.extend(row[0] for row in rows)
        priced_names = sorted(row[0] for row in item_rows if row[1])
        unpriced_names = sorted(row[0] for row in item_rows if not row[1])
        if sorted(assigned) != priced_names or shopping["unpriced_trip_items"] != unpriced_names:
            raise ValueError("trip item assignment is inconsistent")
        expected_warnings = []
        if unpriced_names:
            expected_warnings.append("Unpriced items are not assigned to cost-limited trips.")
        if any(trip["over_limit"] for trip in shopping["trips"]):
            expected_warnings.append("At least one individual item exceeds the soft trip limit.")
        if shopping["trip_warnings"] != expected_warnings:
            raise ValueError("trip_warnings is inconsistent")
    return shopping


def build_plan_shopping_list(*, plan, dishes, prep_items, fridge):
    """Aggregate essential ingredient uses for dishes and planned prep.

    Recipes do not yet carry gram quantities, so one meal entry means one
    cooking use of each essential ingredient. One fridge entry covers one use.
    """
    dishes_by_name = {dish.name: dish for dish in dishes}
    prep_by_name = {item.name: item for item in prep_items}
    required = Counter()
    sources = defaultdict(Counter)
    prep_demand = Counter()

    for day in plan.days.values():
        for meal in day.meals:
            dish = dishes_by_name.get(meal.dish)
            if dish is None:
                raise LookupError(f"dish '{meal.dish}' is not in the catalog")
            for ingredient, essential in dish.ingredients.items():
                if not essential:
                    continue
                required[ingredient] += 1
                sources[ingredient][("dish", dish.name)] += 1
            for dependency in dish.prep_depends:
                prep_demand[dependency] += 1

    prep_names_to_make = set(plan.prep)
    unresolved_prep = []
    for dependency, required_uses in sorted(prep_demand.items()):
        prep = prep_by_name.get(dependency)
        if prep is None:
            unresolved_prep.append({
                "prep_item": dependency,
                "required_uses": required_uses,
                "reason": "not_defined",
            })
            continue
        if prep.remaining < required_uses:
            prep_names_to_make.add(dependency)

    prep_to_make = []
    prep_capacity_warnings = []
    for prep_name in sorted(prep_names_to_make):
        prep = prep_by_name.get(prep_name)
        if prep is None:
            raise LookupError(f"prep item '{prep_name}' is not defined")
        required_uses = prep_demand.get(prep_name, 0)
        detail = {
            "prep_item": prep_name,
            "required_uses": required_uses,
            "available_uses": prep.remaining,
            # make_prep replaces remaining with one fresh batch; it does not add.
            "projected_uses": prep.yield_qty,
            "planned_explicitly": prep_name in plan.prep,
        }
        prep_to_make.append(detail)
        if prep.yield_qty < required_uses:
            prep_capacity_warnings.append(dict(detail))
        for ingredient, essential in prep.ingredients.items():
            if not essential:
                continue
            required[ingredient] += 1
            sources[ingredient][("prep", prep.name)] += 1

    items = []
    covered = []
    for ingredient in sorted(required):
        available_uses = 1 if ingredient in fridge else 0
        to_buy = max(0, required[ingredient] - available_uses)
        entry = {
            "ingredient": ingredient,
            "required_uses": required[ingredient],
            "available_uses": available_uses,
            "to_buy": to_buy,
            "required_by": [
                {"kind": kind, "name": name, "uses": uses}
                for (kind, name), uses in sorted(sources[ingredient].items())
            ],
        }
        if to_buy:
            items.append(entry)
        else:
            covered.append(entry)

    return {
        "basis": "cooking_occurrences",
        "items": items,
        "covered_by_fridge": covered,
        "prep_to_make": prep_to_make,
        "unresolved_prep_dependencies": unresolved_prep,
        "prep_capacity_warnings": prep_capacity_warnings,
    }


def estimate_shopping_cost(shopping, prices, *, weekly_limit=150.0):
    """Apply optional per-shopping-unit prices and return a soft budget check."""
    if not isinstance(shopping, dict) or not isinstance(shopping.get("items"), list):
        raise ValueError("shopping must contain an items list")
    if not isinstance(prices, dict):
        raise ValueError("prices must be an ingredient-to-price object")
    weekly_limit = _positive_number(weekly_limit, label="weekly_limit")

    normalized_prices = {}
    for raw_name, raw_price in prices.items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise ValueError("price ingredient names must be non-empty strings")
        name = raw_name.strip().lower()
        normalized_prices[name] = _positive_number(raw_price, label=f"price for '{name}'")

    priced_items = []
    unpriced_items = []
    subtotal = 0.0
    for raw_item in shopping["items"]:
        if not isinstance(raw_item, dict):
            raise ValueError("shopping items must be objects")
        ingredient = raw_item.get("ingredient")
        if not isinstance(ingredient, str) or not ingredient:
            raise ValueError("shopping item ingredient must be a non-empty string")
        to_buy = _bounded_int(
            raw_item.get("to_buy"),
            label="shopping item to_buy",
            minimum=1,
        )

        item = dict(raw_item)
        # Every estimate is a fresh snapshot; never retain derived fields from
        # an older, more complete price map.
        item.pop("estimated_unit_price", None)
        item.pop("estimated_cost", None)
        unit_price = normalized_prices.get(ingredient)
        if unit_price is None:
            unpriced_items.append(ingredient)
        else:
            try:
                raw_item_cost = unit_price * to_buy
            except OverflowError as exc:
                raise ValueError(f"derived cost for '{ingredient}' is out of range") from exc
            if not isfinite(raw_item_cost) or raw_item_cost > _MAX_SAFE_INTEGER:
                raise ValueError(f"derived cost for '{ingredient}' is not finite or supported")
            item_cost = round(raw_item_cost, 2)
            item["estimated_unit_price"] = unit_price
            item["estimated_cost"] = item_cost
            subtotal += item_cost
            _nonnegative_number(subtotal, label="estimated shopping subtotal")
        priced_items.append(item)

    subtotal = round(subtotal, 2)
    complete = not unpriced_items
    if not complete:
        status = "unknown"
        warning = "Cost estimate is incomplete; weekly budget status is unknown."
    elif subtotal > weekly_limit:
        status = "over"
        warning = f"Estimated weekly cost exceeds the soft €{weekly_limit:.2f} limit."
    else:
        status = "within"
        warning = None

    return {
        "items": priced_items,
        "estimated_cost": subtotal,
        "complete": complete,
        "priced_items": len(priced_items) - len(unpriced_items),
        "total_items": len(priced_items),
        "unpriced_items": sorted(unpriced_items),
        "weekly_limit": weekly_limit,
        "weekly_budget_status": status,
        "warning": warning,
    }


def split_shopping_trips(shopping, *, trip_limit=100.0):
    """Pack priced items into trips; preserve unpriced items separately."""
    if not isinstance(shopping, dict) or not isinstance(shopping.get("items"), list):
        raise ValueError("shopping must contain an items list")
    trip_limit = _positive_number(trip_limit, label="trip_limit")

    priced = []
    unpriced = []
    for raw_item in shopping["items"]:
        if not isinstance(raw_item, dict):
            raise ValueError("shopping items must be objects")
        ingredient = raw_item.get("ingredient")
        if not isinstance(ingredient, str) or not ingredient:
            raise ValueError("shopping item ingredient must be a non-empty string")
        _bounded_int(raw_item.get("to_buy"), label="shopping item to_buy", minimum=1)
        if "estimated_cost" not in raw_item:
            unpriced.append(ingredient)
            continue
        cost = _nonnegative_number(
            raw_item["estimated_cost"], label=f"estimated cost for '{ingredient}'"
        )
        priced.append((cost, ingredient, dict(raw_item)))

    trips = []
    for cost, _ingredient, item in sorted(priced, key=lambda row: (-row[0], row[1])):
        destination = None
        if cost <= trip_limit:
            for trip in trips:
                if not trip["over_limit"] and trip["estimated_cost"] + cost <= trip_limit:
                    destination = trip
                    break
        if destination is None:
            destination = {
                "trip": len(trips) + 1,
                "items": [],
                "estimated_cost": 0.0,
                "limit": trip_limit,
                "over_limit": cost > trip_limit,
            }
            trips.append(destination)
        destination["items"].append(item)
        destination["estimated_cost"] = round(destination["estimated_cost"] + cost, 2)

    warnings = []
    if unpriced:
        warnings.append("Unpriced items are not assigned to cost-limited trips.")
    if any(trip["over_limit"] for trip in trips):
        warnings.append("At least one individual item exceeds the soft trip limit.")

    return {
        "trips": trips,
        "unpriced_items": sorted(unpriced),
        "trip_limit": trip_limit,
        "warnings": warnings,
    }
