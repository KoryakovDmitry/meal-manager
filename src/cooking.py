"""Shared cooking command for native and Web surfaces."""

import json
import uuid
from contextlib import ExitStack
from datetime import date, datetime

from .audit import audit_manager
from .repositories import (
    dish_repo,
    fridge_repo,
    history_repo,
    plan_repo,
    prep_repo,
)
from .repositories.json_fridge import SCHEMA_VERSION as INVENTORY_SCHEMA_VERSION
from .repositories.json_history import CookingEvent, HISTORY_SCHEMA_VERSION, _utc_now


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _locate_occurrence(plan_repository, occurrence_id):
    found = []
    if not plan_repository.plans_dir.exists():
        return None
    for path in sorted(plan_repository.plans_dir.glob("*.json")):
        plan = plan_repository.load_strict(path.stem)
        if plan is None:
            continue
        for day_code, day in plan.days.items():
            for meal in day.meals:
                if meal.occurrence_id == occurrence_id:
                    found.append((plan, day_code, meal))
    if len(found) > 1:
        raise ValueError(f"meal occurrence '{occurrence_id}' is duplicated")
    return found[0] if found else None


def _planned_occurrences_for_dish(plan_repository, dish_name):
    found = []
    if not plan_repository.plans_dir.exists():
        return found
    for path in sorted(plan_repository.plans_dir.glob("*.json")):
        plan = plan_repository.load_strict(path.stem)
        if plan is None or plan.status == "archived":
            continue
        for day_code, day in plan.days.items():
            for meal in day.meals:
                if meal.dish == dish_name and meal.status == "planned":
                    found.append((plan, day_code, meal))
    return found


def _recovered_commit(manager):
    return manager.resolve_last_transaction()


def _normalize_cook_time(cooked_at):
    if cooked_at is None:
        return {
            "cooked_at": None,
            "cooked_on": date.today().isoformat(),
            "time_precision": "date",
        }
    if not isinstance(cooked_at, str):
        raise ValueError("cooked_at must be an RFC3339 string")
    if "T" not in cooked_at:
        cooked_on = date.fromisoformat(cooked_at).isoformat()
        return {
            "cooked_at": None,
            "cooked_on": cooked_on,
            "time_precision": "date",
        }
    parsed = datetime.fromisoformat(cooked_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("cooked_at must be timezone-aware")
    return {
        "cooked_at": parsed.isoformat().replace("+00:00", "Z"),
        "cooked_on": parsed.date().isoformat(),
        "time_precision": "datetime",
    }


def register_cooked(
    *,
    dish_name,
    occurrence_id=None,
    expected_revision=None,
    cooked_at=None,
    actual_portions=None,
    actual_yield_portions=None,
    actor_type="agent",
    surface_kind="native_tool",
    dish_repository=dish_repo,
    fridge_repository=fridge_repo,
    history_repository=history_repo,
    plan_repository=plan_repo,
    prep_repository=prep_repo,
    audit_transaction_manager=audit_manager,
):
    """Commit cooking, plan lifecycle, history, inventory, prep, and audit."""
    timing = _normalize_cook_time(cooked_at)
    with audit_transaction_manager.lock:
        audit_transaction_manager.recover()
        with ExitStack() as stack:
            stack.enter_context(plan_repository.lock)
            stack.enter_context(prep_repository.lock)
            stack.enter_context(fridge_repository.lock)
            stack.enter_context(dish_repository.lock)
            stack.enter_context(history_repository.lock)

            dishes = dish_repository.load_strict()
            dish = next((item for item in dishes if item.name == dish_name), None)
            if dish is None:
                raise LookupError(f"'{dish_name}' is not in the recipe catalog.")

            located = None
            if occurrence_id is not None:
                if not isinstance(occurrence_id, str) or not occurrence_id.startswith("mealocc_"):
                    raise ValueError("occurrence_id must start with mealocc_")
                located = _locate_occurrence(plan_repository, occurrence_id)
                if located is None:
                    raise LookupError(f"meal occurrence '{occurrence_id}' not found")
                plan, day_code, occurrence = located
                if plan.status == "archived":
                    raise ValueError("archived plan occurrences cannot be cooked")
                if occurrence.dish != dish.name:
                    raise ValueError("meal occurrence dish does not match dish_name")
                if occurrence.status != "planned":
                    raise ValueError(
                        f"meal occurrence '{occurrence_id}' is already {occurrence.status}"
                    )
                if (
                    not isinstance(expected_revision, int)
                    or isinstance(expected_revision, bool)
                    or expected_revision < 1
                ):
                    raise ValueError(
                        "expected_revision is required for explicit occurrence_id"
                    )
            else:
                matches = _planned_occurrences_for_dish(plan_repository, dish.name)
                if len(matches) > 1:
                    candidates = [meal.occurrence_id for _, _, meal in matches]
                    raise ValueError(
                        "multiple planned occurrences match dish_name; provide occurrence_id: "
                        + ", ".join(candidates)
                    )
                if matches:
                    plan, day_code, occurrence = matches[0]
                    occurrence_id = occurrence.occurrence_id
                else:
                    plan = None
                    day_code = None
                    occurrence = None

            history_events = history_repository.load_events(strict=True)
            cook_event = CookingEvent(
                id="cook_" + uuid.uuid4().hex,
                dish_name_snapshot=dish.name,
                cooked_at=timing["cooked_at"],
                cooked_on=timing["cooked_on"],
                time_precision=timing["time_precision"],
                recorded_at=_utc_now(),
                plan_occurrence_id=occurrence_id,
                actual_portions=actual_portions,
                actual_yield_portions=actual_yield_portions,
            )
            history_events.append(cook_event)

            targets = {
                "history.json": _json_bytes({
                    "schema_version": HISTORY_SCHEMA_VERSION,
                    "entries": [event.to_dict() for event in history_events],
                }),
            }

            if occurrence is not None:
                occurrence.transition_to(
                    "cooked",
                    expected_revision=expected_revision,
                )
                occurrence.cooked_at = cook_event.cooked_at
                occurrence.cooked_on = cook_event.cooked_on
                occurrence.cooked_time_precision = cook_event.time_precision
                occurrence.actual_portions = actual_portions
                occurrence.actual_yield_portions = actual_yield_portions
                occurrence.cook_event_id = cook_event.id
                plan.shopping = {}
                targets[f"plans/{plan.week_id}.json"] = _json_bytes(plan.to_dict())

            essentials = [
                ingredient
                for ingredient, is_essential in dish.ingredients.items()
                if is_essential
            ]
            inventory = fridge_repository.load_catalog_items()
            removed = []
            removed_ids = set()
            projected_inventory = []
            for item in inventory:
                matching = next((
                    ingredient
                    for ingredient in essentials
                    if ingredient in (item.name, *item.aliases)
                ), None)
                if item.available and matching is not None and item.id not in removed_ids:
                    item = fridge_repository._with_availability(item, False)
                    removed.append(matching)
                    removed_ids.add(item.id)
                projected_inventory.append(item)
            if removed:
                targets["fridge.json"] = _json_bytes({
                    "schema_version": INVENTORY_SCHEMA_VERSION,
                    "items": [item.to_dict() for item in projected_inventory],
                })

            prep_items = prep_repository.load_strict()
            prep_consumed = []
            for dependency in dish.prep_depends:
                prep_item = next(
                    (item for item in prep_items if item.name == dependency), None
                )
                if prep_item is not None and prep_item.remaining > 0:
                    prep_item.remaining -= 1
                    prep_consumed.append(dependency)
            if prep_consumed:
                targets["prep_items.json"] = _json_bytes({
                    "prep_items": [item.to_dict() for item in prep_items],
                })

            try:
                transaction = audit_transaction_manager.commit(
                    operation="register_cooked_meal",
                    targets=targets,
                    events=[{
                        "event_type": "meal.cooked.v1",
                        "entity": {"type": "cook_occurrence", "id": cook_event.id},
                        "payload": {
                            "dish": dish.name,
                            "plan_occurrence_id": occurrence_id,
                            "week": plan.week_id if plan is not None else None,
                            "day": day_code,
                            "cooked_at": cook_event.cooked_at,
                            "cooked_on": cook_event.cooked_on,
                            "time_precision": cook_event.time_precision,
                            "actual_portions": actual_portions,
                            "actual_yield_portions": actual_yield_portions,
                            "inventory_consumed": removed,
                            "prep_consumed": prep_consumed,
                        },
                    }],
                    context={
                        "actor": {"type": actor_type},
                        "surface": {
                            "kind": surface_kind,
                            "operation": "register_cooked_meal",
                        },
                    },
                )
            except Exception:
                # Return success when recovery proves all after-images committed;
                # otherwise preserve the original failure after rollback/conflict.
                transaction = _recovered_commit(audit_transaction_manager)
                if transaction is None:
                    raise

    return {
        "dish": dish.name,
        "cook_event_id": cook_event.id,
        "plan_occurrence_id": occurrence_id,
        "cooked_on": cook_event.cooked_on,
        "removed_inventory": removed,
        "prep_consumed": prep_consumed,
        "transaction_id": transaction["transaction_id"],
        "dishes_snapshot": dishes,
    }


def retract_cooked(
    *,
    event_id,
    actor_type="agent",
    surface_kind="native_tool",
    history_repository=history_repo,
    plan_repository=plan_repo,
    audit_transaction_manager=audit_manager,
):
    """Retract one cook event and reopen its linked plan occurrence."""
    if not isinstance(event_id, str) or not event_id.startswith("cook_"):
        raise ValueError("event_id must start with cook_")
    with audit_transaction_manager.lock:
        audit_transaction_manager.recover()
        with plan_repository.lock:
            with history_repository.lock:
                events = history_repository.load_events(strict=True)
                event = next((item for item in events if item.id == event_id), None)
                if event is None:
                    raise LookupError(f"cooking event '{event_id}' not found")
                if not event.active:
                    raise ValueError(f"cooking event '{event_id}' is already retracted")

                targets = {}
                event.retracted_at = _utc_now()
                targets["history.json"] = _json_bytes({
                    "schema_version": HISTORY_SCHEMA_VERSION,
                    "entries": [item.to_dict() for item in events],
                })

                reopened = None
                week_id = None
                day_code = None
                if event.plan_occurrence_id:
                    located = _locate_occurrence(
                        plan_repository, event.plan_occurrence_id
                    )
                    if located is None:
                        raise ValueError("linked plan occurrence is missing")
                    plan, day_code, reopened = located
                    week_id = plan.week_id
                    if reopened.status != "cooked" or reopened.cook_event_id != event.id:
                        raise ValueError("linked plan occurrence does not match cooking event")
                    now = _utc_now()
                    reopened.status = "planned"
                    reopened.revision += 1
                    reopened.updated_at = now
                    reopened.status_changed_at = now
                    reopened.cooked_at = None
                    reopened.cooked_on = None
                    reopened.cooked_time_precision = None
                    reopened.actual_portions = None
                    reopened.actual_yield_portions = None
                    reopened.cook_event_id = None
                    plan.shopping = {}
                    targets[f"plans/{plan.week_id}.json"] = _json_bytes(plan.to_dict())

                try:
                    transaction = audit_transaction_manager.commit(
                        operation="retract_cooked_meal",
                        targets=targets,
                        events=[{
                            "event_type": "meal.cook_retracted.v1",
                            "entity": {
                                "type": "cook_occurrence",
                                "id": event.id,
                            },
                            "payload": {
                                "dish": event.dish_name_snapshot,
                                "plan_occurrence_id": event.plan_occurrence_id,
                                "week": week_id,
                                "day": day_code,
                                "plan_reopened": event.plan_occurrence_id is not None,
                                "inventory_restored": False,
                            },
                        }],
                        context={
                            "actor": {"type": actor_type},
                            "surface": {
                                "kind": surface_kind,
                                "operation": "retract_cooked_meal",
                            },
                        },
                    )
                except Exception:
                    transaction = _recovered_commit(audit_transaction_manager)
                    if transaction is None:
                        raise
    return {
        "entry": event,
        "transaction_id": transaction["transaction_id"],
        "plan_reopened": event.plan_occurrence_id is not None,
    }
