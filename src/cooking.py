"""Shared cooking command for native and Web surfaces."""

import hashlib
import json
import re
import uuid
from contextlib import ExitStack
from datetime import date, datetime

from .audit import audit_manager
from .dish import Dish
from .identifiers import validate_cook_event_id, validate_meal_occurrence_id
from .repositories import (
    dish_repo,
    fridge_repo,
    history_repo,
    plan_repo,
    prep_repo,
)
from .repositories.json_fridge import SCHEMA_VERSION as INVENTORY_SCHEMA_VERSION
from .repositories.json_history import (
    CookingEvent,
    HISTORY_SCHEMA_VERSION,
    _utc_now,
    validate_event_lineage,
)


UNSET = object()


class CookingConflictError(ValueError):
    """A revision-fenced cooking intent is stale."""


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


def _assert_cooking_repository_roots(
    manager,
    *,
    dish_repository,
    fridge_repository,
    history_repository,
    plan_repository,
    prep_repository,
):
    manager.assert_repository_path(dish_repository.path, "dishes.json")
    manager.assert_repository_path(fridge_repository.path, "fridge.json")
    manager.assert_repository_path(history_repository.path, "history.json")
    manager.assert_repository_path(prep_repository.path, "prep_items.json")
    manager.assert_repository_path(
        plan_repository.plans_dir, "plans", directory=True
    )


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


def _validate_actual_portions(value, label):
    if value is UNSET:
        return
    if value is not None and (
        not isinstance(value, int) or isinstance(value, bool) or value < 0
    ):
        raise ValueError(f"{label} must be a non-negative integer or null")


def _validate_actual_portion_relationship(actual_portions, actual_yield_portions):
    if (
        isinstance(actual_portions, int)
        and not isinstance(actual_portions, bool)
        and isinstance(actual_yield_portions, int)
        and not isinstance(actual_yield_portions, bool)
        and actual_yield_portions < actual_portions
    ):
        raise ValueError(
            "actual_yield_portions cannot be below actual_portions served"
        )


def _correction_request_fingerprint(
    *,
    dish_name,
    occurrence_id,
    expected_revision,
    cooked_at,
    actual_portions,
    actual_yield_portions,
    replaces_event_id,
):
    def canonical(value):
        return {"state": "omitted"} if value is UNSET else {"state": "value", "value": value}

    payload = {
        "schema_version": 1,
        "dish_name": dish_name,
        "occurrence_id": occurrence_id,
        "expected_revision": expected_revision,
        "cooked_at": canonical(cooked_at),
        "actual_portions": canonical(actual_portions),
        "actual_yield_portions": canonical(actual_yield_portions),
        "replaces_event_id": replaces_event_id,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _request_field(value):
    return {"state": "omitted"} if value is UNSET else {
        "state": "value", "value": value,
    }


def _event_metadata(event):
    if event is None:
        return None
    return {
        "cooked_at": event.cooked_at,
        "cooked_on": event.cooked_on,
        "time_precision": event.time_precision,
        "actual_portions": event.actual_portions,
        "actual_yield_portions": event.actual_yield_portions,
    }


def _leftover_evidence(plan, occurrence):
    if plan is None or occurrence is None or not occurrence.leftover_lot_ids:
        return None
    if len(occurrence.leftover_lot_ids) != 1:
        raise ValueError("meal occurrence has multiple leftover lots")
    lot_id = occurrence.leftover_lot_ids[0]
    lot = plan.leftovers.get(lot_id)
    if not isinstance(lot, dict):
        raise ValueError("meal occurrence leftover lot is missing")
    return {"lot_id": lot_id, **dict(lot)}


def _committed_cook_result(
    *, manager, event, dishes, payload=None, transaction_id=None, replayed=None
):
    if payload is None or transaction_id is None:
        expected_type = (
            "meal.cook_corrected.v1"
            if event.provenance is not None else "meal.cooked.v1"
        )
        matches = manager.list_events(
            entity_type="cook_occurrence",
            entity_id=event.id,
            event_type=expected_type,
            limit=10,
        )
        if len(matches) != 1:
            raise ValueError("committed cooking audit event is missing or ambiguous")
        audit_event = matches[0]
        payload = audit_event["payload"]
        transaction_id = audit_event["transaction_id"]
        if replayed is None:
            replayed = True
    elif replayed is None:
        replayed = False
    corrected = event.provenance is not None
    return {
        "dish": event.dish_name_snapshot,
        "cook_event_id": event.id,
        "plan_occurrence_id": event.plan_occurrence_id,
        "cooked_on": event.cooked_on,
        "removed_inventory": list(payload.get("inventory_consumed", [])),
        "prep_consumed": list(payload.get("prep_consumed", [])),
        "corrected": corrected,
        "replaces_event_id": payload.get("replaces_event_id"),
        "root_event_id": payload.get("root_event_id"),
        "effects_origin_event_id": payload.get("effects_origin_event_id"),
        "occurrence_revision": (
            payload.get("occurrence_revision", {}).get("after")
        ),
        "request_fingerprint": payload.get("request_fingerprint"),
        "leftover_after": payload.get("leftover_after"),
        "transaction_id": transaction_id,
        "replayed": replayed,
        "event": event,
        "dishes_snapshot": dishes,
    }


def _resolve_committed_correction_retry(
    manager,
    *,
    history_repository,
    plan_repository,
    replaces_event_id,
    occurrence_id,
    request_fingerprint,
):
    if replaces_event_id is None or occurrence_id is None:
        return None
    try:
        with manager.consistent_read():
            manager.assert_repository_path(
                history_repository.path, "history.json"
            )
            manager.assert_repository_path(
                plan_repository.plans_dir, "plans", directory=True
            )
            events = history_repository.load_events(strict=True)
            children = [
                event for event in events
                if isinstance(event.provenance, dict)
                and event.provenance.get("replaces_event_id")
                    == replaces_event_id
                and event.provenance.get("request_fingerprint")
                    == request_fingerprint
            ]
            if len(children) != 1 or not children[0].active:
                return None
            child = children[0]
            located = _locate_occurrence(plan_repository, occurrence_id)
            if located is None:
                return None
            _plan, _day, occurrence = located
            if (
                occurrence.status != "cooked"
                or occurrence.cook_event_id != child.id
            ):
                return None
            return _committed_cook_result(
                manager=manager,
                event=child,
                dishes=[],
            )
    except Exception:
        return None


def _unique_lineage_tip(events, lineage_children):
    """Resolve one linked correction tip without row or timestamp ordering."""
    tips = [event for event in events if event.id not in lineage_children]
    if len(tips) != 1:
        raise ValueError("retracted correction recovery has an ambiguous lineage tip")
    return tips[0]


def _event_is_lineage_ancestor(event, target, history_by_id, lineage_children):
    """True when ``event`` is a chain ancestor of ``target``."""
    current = target
    visited = set()
    while current is not None:
        if current.id == event.id:
            return True
        if current.id in visited:
            return False
        visited.add(current.id)
        provenance = current.provenance
        if (
            isinstance(provenance, dict)
            and provenance.get("source") == "cook_event_correction"
        ):
            current = history_by_id.get(provenance["replaces_event_id"])
        else:
            current = None
    return False


def _reconcile_replacement_leftovers(
    *,
    plan,
    occurrence,
    replaced_event,
    cook_event,
    actual_portions,
    actual_yield_portions,
):
    """Reconcile one occurrence's stable leftover lot during metadata correction."""
    linked_ids = set(occurrence.leftover_lot_ids)
    source_ids = {
        lot_id
        for lot_id, lot in plan.leftovers.items()
        if isinstance(lot, dict)
        and lot.get("source_occurrence_id") == occurrence.occurrence_id
    }
    if linked_ids != source_ids:
        raise ValueError("linked leftover lots do not match the corrected occurrence")
    if len(linked_ids) > 1:
        raise ValueError("corrected occurrence has multiple leftover lots")

    desired_portions = 0
    if (
        isinstance(actual_portions, int)
        and isinstance(actual_yield_portions, int)
        and actual_yield_portions > actual_portions
    ):
        desired_portions = actual_yield_portions - actual_portions

    if linked_ids:
        lot_id = next(iter(linked_ids))
        lot = plan.leftovers[lot_id]
        if lot.get("source_cook_event_id") != replaced_event.id:
            raise ValueError("leftover lot does not belong to the replaced cook event")
        consumed = lot.get("consumed_portions", 0)
        if not isinstance(consumed, int) or isinstance(consumed, bool) or consumed < 0:
            raise ValueError("leftover lot consumed_portions is invalid")
        if desired_portions < consumed:
            raise ValueError(
                "corrected leftover yield is below already consumed portions"
            )
        if desired_portions == 0:
            plan.leftovers.pop(lot_id)
            occurrence.leftover_lot_ids = []
            return
        lot.update({
            "dish": cook_event.dish_name_snapshot,
            "portions": desired_portions,
            "source_cook_event_id": cook_event.id,
            "source_occurrence_id": occurrence.occurrence_id,
        })
        occurrence.leftover_lot_ids = [lot_id]
        return

    if desired_portions:
        lot_id = "leftover_" + uuid.uuid4().hex
        plan.leftovers[lot_id] = {
            "dish": cook_event.dish_name_snapshot,
            "portions": desired_portions,
            "source_cook_event_id": cook_event.id,
            "source_occurrence_id": occurrence.occurrence_id,
            "created_at": _utc_now(),
            "consumed_portions": 0,
        }
        occurrence.leftover_lot_ids = [lot_id]


def _register_cooked_once(
    *,
    dish_name,
    occurrence_id=None,
    expected_revision=None,
    cooked_at=UNSET,
    actual_portions=UNSET,
    actual_yield_portions=UNSET,
    replaces_event_id=None,
    acknowledge_legacy_tombstones=None,
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
    requested_dish_name = Dish.normalize_name(dish_name)
    if not requested_dish_name or len(requested_dish_name) > 200:
        raise ValueError("dish_name must contain at most 200 characters")
    explicit_occurrence_id = occurrence_id is not None
    _validate_actual_portions(actual_portions, "actual_portions")
    _validate_actual_portions(actual_yield_portions, "actual_yield_portions")
    _validate_actual_portion_relationship(actual_portions, actual_yield_portions)
    validate_meal_occurrence_id(
        occurrence_id, "occurrence_id", optional=True
    )
    validate_cook_event_id(
        replaces_event_id, "replaces_event_id", optional=True
    )
    request_fields = {
        "cooked_at": _request_field(cooked_at),
        "actual_portions": _request_field(actual_portions),
        "actual_yield_portions": _request_field(actual_yield_portions),
    }
    if acknowledge_legacy_tombstones is not None and replaces_event_id is None:
        raise ValueError(
            "acknowledge_legacy_tombstones requires replaces_event_id"
        )
    if acknowledge_legacy_tombstones is not None:
        if (
            not isinstance(acknowledge_legacy_tombstones, list)
            or not acknowledge_legacy_tombstones
            or any(
                not isinstance(item, str)
                or re.fullmatch(r"cook_[0-9a-f]{24,32}", item) is None
                for item in acknowledge_legacy_tombstones
            )
            or len(set(acknowledge_legacy_tombstones))
                != len(acknowledge_legacy_tombstones)
        ):
            raise ValueError(
                "acknowledge_legacy_tombstones must be a non-empty list of "
                "unique cook event ids"
            )
    if cooked_at is not UNSET and cooked_at is not None and (
        not isinstance(cooked_at, str) or len(cooked_at) > 100
    ):
        raise ValueError("cooked_at must be an RFC3339 string of at most 100 characters")
    timing = (
        _normalize_cook_time(cooked_at)
        if cooked_at is not UNSET and cooked_at is not None
        else None
    )
    correction_fingerprint = (
        _correction_request_fingerprint(
            dish_name=requested_dish_name,
            occurrence_id=occurrence_id,
            expected_revision=expected_revision,
            cooked_at=cooked_at,
            actual_portions=actual_portions,
            actual_yield_portions=actual_yield_portions,
            replaces_event_id=replaces_event_id,
        )
        if replaces_event_id is not None else None
    )
    with audit_transaction_manager.lock:
        audit_transaction_manager.recover()
        _assert_cooking_repository_roots(
            audit_transaction_manager,
            dish_repository=dish_repository,
            fridge_repository=fridge_repository,
            history_repository=history_repository,
            plan_repository=plan_repository,
            prep_repository=prep_repository,
        )
        with ExitStack() as stack:
            stack.enter_context(plan_repository.lock)
            stack.enter_context(prep_repository.lock)
            stack.enter_context(fridge_repository.lock)
            stack.enter_context(dish_repository.lock)
            stack.enter_context(history_repository.lock)

            history_events = history_repository.load_events(strict=True)
            history_by_id, lineage_children = validate_event_lineage(history_events)
            replaced_event = None
            replacement_target_was_active = False
            unverified_tombstones = []
            dish = None
            if replaces_event_id is not None:
                if not explicit_occurrence_id:
                    raise ValueError(
                        "replaces_event_id requires an explicit linked occurrence_id"
                    )
                replaced_event = history_by_id.get(replaces_event_id)
                if replaced_event is None:
                    raise LookupError(
                        f"cooking event '{replaces_event_id}' not found"
                    )
                canonical_dish_name = replaced_event.dish_name_snapshot
                if requested_dish_name != canonical_dish_name:
                    raise ValueError("replacement target dish does not match dish_name")
                # Historical metadata remains correctable after the current recipe
                # has been deleted or edited. Corrections never consume its inputs.
                dishes = []
            else:
                dishes = dish_repository.load_strict()
                dish = next(
                    (item for item in dishes if item.name == requested_dish_name),
                    None,
                )
                if dish is None:
                    raise LookupError(
                        f"'{requested_dish_name}' is not in the recipe catalog."
                    )
                canonical_dish_name = dish.name

            located = None
            if occurrence_id is not None:
                located = _locate_occurrence(plan_repository, occurrence_id)
                if located is None:
                    raise LookupError(f"meal occurrence '{occurrence_id}' not found")
                plan, day_code, occurrence = located
                if plan.status == "archived":
                    raise ValueError("archived plan occurrences cannot be cooked")
                if occurrence.dish != canonical_dish_name:
                    raise ValueError("meal occurrence dish does not match dish_name")
                if occurrence.status not in (
                    {"planned", "cooked"}
                    if replaces_event_id is not None else {"planned"}
                ):
                    raise ValueError(
                        f"meal occurrence '{occurrence_id}' cannot be "
                        f"{'corrected' if replaces_event_id is not None else 'cooked'} "
                        f"while {occurrence.status}"
                    )
                if (
                    not isinstance(expected_revision, int)
                    or isinstance(expected_revision, bool)
                    or expected_revision < 1
                ):
                    raise ValueError(
                        "expected_revision is required for explicit occurrence_id"
                    )
                if occurrence.revision != expected_revision:
                    raise CookingConflictError(
                        "meal occurrence revision conflict"
                    )
            else:
                matches = _planned_occurrences_for_dish(
                    plan_repository, canonical_dish_name
                )
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

            linked_events = (
                [
                    event
                    for event in history_events
                    if event.plan_occurrence_id == occurrence_id
                ]
                if occurrence is not None else []
            )
            if replaces_event_id is not None:
                if occurrence is None:
                    raise ValueError("replacement target must link a plan occurrence")
                if replaced_event.plan_occurrence_id != occurrence_id:
                    raise ValueError(
                        "replacement target belongs to another plan occurrence"
                    )
                unverified_tombstones = [
                    event.id
                    for event in linked_events
                    if (
                        event.id != replaced_event.id
                        and event.plan_occurrence_id == occurrence_id
                        and not (
                            isinstance(event.provenance, dict)
                            and event.provenance.get("source")
                                == "cook_event_correction"
                        )
                        and not _event_is_lineage_ancestor(
                            event, replaced_event, history_by_id,
                            lineage_children,
                        )
                    )
                ]
                acknowledged = set(acknowledge_legacy_tombstones or [])
                if unverified_tombstones and not (
                    set(unverified_tombstones) <= acknowledged
                ):
                    raise ValueError(
                        "linked occurrence has unverified legacy cooking "
                        "events; acknowledge_legacy_tombstones must list "
                        "them explicitly: "
                        + ", ".join(sorted(
                            set(unverified_tombstones) - acknowledged
                        ))
                    )
                if replaced_event.id in lineage_children:
                    child = history_by_id[lineage_children[replaced_event.id]]
                    child_provenance = child.provenance or {}
                    if (
                        child_provenance.get("request_fingerprint")
                            == correction_fingerprint
                        and child.active
                        and occurrence.status == "cooked"
                        and occurrence.cook_event_id == child.id
                    ):
                        return _committed_cook_result(
                            manager=audit_transaction_manager,
                            event=child,
                            dishes=[],
                        )
                    raise ValueError(
                        "replacement target has already been superseded; correct the "
                        "latest event in the lineage"
                    )
                active_linked = [event for event in linked_events if event.active]
                replacement_target_was_active = replaced_event.active
                if replacement_target_was_active:
                    if (
                        len(active_linked) != 1
                        or active_linked[0].id != replaced_event.id
                        or occurrence.status != "cooked"
                        or occurrence.cook_event_id != replaced_event.id
                    ):
                        raise ValueError(
                            "active replacement target does not match the linked occurrence"
                        )
                elif (
                    active_linked
                    or occurrence.status != "planned"
                    or occurrence.cook_event_id is not None
                ):
                    raise ValueError(
                        "retracted replacement target does not match the reopened occurrence"
                    )
                elif _unique_lineage_tip(
                    linked_events, lineage_children
                ).id != replaced_event.id:
                    raise ValueError(
                        "replacement target is not the latest event in the lineage"
                    )
                assert replaced_event is not None
                if cooked_at is UNSET:
                    timing = {
                        "cooked_at": replaced_event.cooked_at,
                        "cooked_on": replaced_event.cooked_on,
                        "time_precision": replaced_event.time_precision,
                    }
                elif cooked_at is None:
                    timing = {
                        "cooked_at": None,
                        "cooked_on": replaced_event.cooked_on,
                        "time_precision": "date",
                    }
                if actual_portions is UNSET:
                    actual_portions = replaced_event.actual_portions
                if actual_yield_portions is UNSET:
                    actual_yield_portions = replaced_event.actual_yield_portions
            elif linked_events:
                raise ValueError(
                    "meal occurrence has prior cooking history; provide "
                    "replaces_event_id to correct the same physical cook"
                )
            else:
                if actual_portions is UNSET:
                    actual_portions = None
                if actual_yield_portions is UNSET:
                    actual_yield_portions = None
            _validate_actual_portion_relationship(
                actual_portions, actual_yield_portions
            )
            if timing is None:
                timing = _normalize_cook_time(None)
            recorded_at = _utc_now()
            occurrence_revision_before = (
                occurrence.revision if occurrence is not None else None
            )
            metadata_before = _event_metadata(replaced_event)
            leftover_before = (
                _leftover_evidence(plan, occurrence)
                if replaced_event is not None else None
            )
            if replacement_target_was_active:
                replaced_event.retracted_at = recorded_at
            if replaced_event is not None:
                predecessor_provenance = replaced_event.provenance
                if (
                    isinstance(predecessor_provenance, dict)
                    and predecessor_provenance.get("source")
                        == "cook_event_correction"
                ):
                    root_event_id = predecessor_provenance["root_event_id"]
                    effects_origin_event_id = predecessor_provenance[
                        "effects_origin_event_id"
                    ]
                else:
                    root_event_id = replaced_event.id
                    effects_origin_event_id = replaced_event.id
            else:
                root_event_id = None
                effects_origin_event_id = None
            cook_event = CookingEvent(
                id="cook_" + uuid.uuid4().hex,
                dish_name_snapshot=canonical_dish_name,
                cooked_at=timing["cooked_at"],
                cooked_on=timing["cooked_on"],
                time_precision=timing["time_precision"],
                recorded_at=recorded_at,
                plan_occurrence_id=occurrence_id,
                actual_portions=actual_portions,
                actual_yield_portions=actual_yield_portions,
                provenance=(
                    {
                        "source": "cook_event_correction",
                        "replaces_event_id": replaced_event.id,
                        "root_event_id": root_event_id,
                        "effects_origin_event_id": effects_origin_event_id,
                        "request_fingerprint": correction_fingerprint,
                        "acknowledged_legacy_event_ids": sorted(
                            unverified_tombstones
                        ),
                    }
                    if replaced_event is not None else None
                ),
            )
            history_events.append(cook_event)

            targets = {
                "history.json": _json_bytes({
                    "schema_version": HISTORY_SCHEMA_VERSION,
                    "entries": [event.to_dict() for event in history_events],
                }),
            }

            if occurrence is not None:
                if replacement_target_was_active:
                    if occurrence.revision != expected_revision:
                        raise ValueError("meal occurrence revision conflict")
                    occurrence.revision += 1
                    occurrence.updated_at = recorded_at
                else:
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

                if replaced_event is not None:
                    _reconcile_replacement_leftovers(
                        plan=plan,
                        occurrence=occurrence,
                        replaced_event=replaced_event,
                        cook_event=cook_event,
                        actual_portions=actual_portions,
                        actual_yield_portions=actual_yield_portions,
                    )
                elif (
                    isinstance(actual_portions, int)
                    and isinstance(actual_yield_portions, int)
                    and actual_yield_portions > actual_portions
                ):
                    lot_id = "leftover_" + uuid.uuid4().hex
                    plan.leftovers[lot_id] = {
                        "dish": canonical_dish_name,
                        "portions": actual_yield_portions - actual_portions,
                        "source_cook_event_id": cook_event.id,
                        "source_occurrence_id": occurrence_id,
                        "created_at": _utc_now(),
                        "consumed_portions": 0,
                    }
                    occurrence.leftover_lot_ids.append(lot_id)

                plan.shopping = {}
                targets[f"plans/{plan.week_id}.json"] = _json_bytes(plan.to_dict())

            occurrence_revision_after = (
                occurrence.revision if occurrence is not None else None
            )
            metadata_after = _event_metadata(cook_event)
            leftover_after = (
                _leftover_evidence(plan, occurrence)
                if replaced_event is not None else None
            )

            removed = []
            if replaced_event is None:
                assert dish is not None
                essentials = [
                    ingredient
                    for ingredient, is_essential in dish.ingredients.items()
                    if is_essential
                ]
                inventory = fridge_repository.load_catalog_items()
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

            prep_consumed = []
            if replaced_event is None:
                assert dish is not None
                prep_items = prep_repository.load_strict()
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

            audit_payload = {
                "dish": canonical_dish_name,
                "plan_occurrence_id": occurrence_id,
                "week": plan.week_id if plan is not None else None,
                "day": day_code,
                "cooked_at": cook_event.cooked_at,
                "cooked_on": cook_event.cooked_on,
                "time_precision": cook_event.time_precision,
                "actual_portions": actual_portions,
                "actual_yield_portions": actual_yield_portions,
                "replaces_event_id": (
                    replaced_event.id if replaced_event is not None else None
                ),
                "root_event_id": root_event_id,
                "effects_origin_event_id": effects_origin_event_id,
                "request_fingerprint": correction_fingerprint,
                "request_fields": request_fields,
                "occurrence_revision": {
                    "before": occurrence_revision_before,
                    "after": occurrence_revision_after,
                },
                "metadata_before": metadata_before,
                "metadata_after": metadata_after,
                "leftover_before": leftover_before,
                "leftover_after": leftover_after,
                "inventory_effect": (
                    "retained" if replaced_event is not None else "applied"
                ),
                "prep_effect": (
                    "retained" if replaced_event is not None else "applied"
                ),
                "inventory_consumed": removed,
                "prep_consumed": prep_consumed,
            }
            try:
                transaction = audit_transaction_manager.commit(
                    operation=(
                        "correct_cooked_meal"
                        if replaced_event is not None else "register_cooked_meal"
                    ),
                    targets=targets,
                    events=[{
                        "event_type": (
                            "meal.cook_corrected.v1"
                            if replaced_event is not None else "meal.cooked.v1"
                        ),
                        "entity": {"type": "cook_occurrence", "id": cook_event.id},
                        "payload": audit_payload,
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

    return _committed_cook_result(
        manager=audit_transaction_manager,
        event=cook_event,
        dishes=dishes,
        payload=audit_payload,
        transaction_id=transaction["transaction_id"],
    )


def register_cooked(**kwargs):
    """Run one cook command and resolve only exact durable correction retries."""
    replaces_event_id = kwargs.get("replaces_event_id")
    fingerprint = None
    if replaces_event_id is not None:
        fingerprint = _correction_request_fingerprint(
            dish_name=Dish.normalize_name(kwargs.get("dish_name")),
            occurrence_id=kwargs.get("occurrence_id"),
            expected_revision=kwargs.get("expected_revision"),
            cooked_at=kwargs.get("cooked_at", UNSET),
            actual_portions=kwargs.get("actual_portions", UNSET),
            actual_yield_portions=kwargs.get(
                "actual_yield_portions", UNSET
            ),
            replaces_event_id=replaces_event_id,
        )
    try:
        return _register_cooked_once(**kwargs)
    except Exception:
        if fingerprint is not None:
            resolved = _resolve_committed_correction_retry(
                kwargs.get("audit_transaction_manager", audit_manager),
                history_repository=kwargs.get(
                    "history_repository", history_repo
                ),
                plan_repository=kwargs.get("plan_repository", plan_repo),
                replaces_event_id=replaces_event_id,
                occurrence_id=kwargs.get("occurrence_id"),
                request_fingerprint=fingerprint,
            )
            if resolved is not None:
                return resolved
        raise


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
        audit_transaction_manager.assert_repository_path(
            history_repository.path, "history.json"
        )
        audit_transaction_manager.assert_repository_path(
            plan_repository.plans_dir, "plans", directory=True
        )
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
