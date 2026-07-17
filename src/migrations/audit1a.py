"""AUDIT-1A schema migration and evidence-backed W29 reconstruction."""

import hashlib
import json
from pathlib import Path

from ..plan import MealEntry, WeekPlan
from ..repositories.json_history import JsonHistoryRepository
from ..repositories.json_plan import JsonPlanRepository

_BACKFILLS = (
    {
        "week": "2026-W29",
        "day": "wed",
        "dish": "рамен tanoshi soja caramel с тофу и овощами",
        "portions": 3,
        "cooked_on": "2026-07-15",
        "history_index": 1,
    },
    {
        "week": "2026-W29",
        "day": "thu",
        "dish": "паста с томатным соусом, чечевицей и кабачком",
        "portions": 5,
        "cooked_on": "2026-07-17",
        "history_index": 0,
    },
)


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _stable_occurrence_id(row):
    raw = json.dumps(
        ["AUDIT-1A", row["week"], row["day"], row["dish"], row["portions"]],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "mealocc_" + hashlib.sha256(raw).hexdigest()[:24]


def _stable_cook_event_id(row):
    raw = json.dumps(
        ["native_v1", row["dish"], row["cooked_on"], row["history_index"]],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return "cook_" + hashlib.sha256(raw).hexdigest()[:24]


def _validate_backfill_event(event, row, occurrence_id):
    expected = {
        "id": _stable_cook_event_id(row),
        "dish_name_snapshot": row["dish"],
        "cooked_at": None,
        "cooked_on": row["cooked_on"],
        "time_precision": "date",
        "recorded_at": None,
        "plan_occurrence_id": event.plan_occurrence_id,
        "actual_portions": None,
        "actual_yield_portions": None,
        "retracted_at": None,
        "backfilled": True,
        "provenance": {"source": "legacy_native_history"},
    }
    if event.plan_occurrence_id not in (None, occurrence_id):
        raise ValueError(f"history event {event.id!r} links another occurrence")
    if event.to_dict() != expected:
        raise ValueError(f"history event {event.id!r} conflicts with backfill evidence")


def _state_token(data_dir):
    data_dir = Path(data_dir)
    paths = [data_dir / "history.json"]
    plans_dir = data_dir / "plans"
    if plans_dir.exists():
        paths.extend(sorted(plans_dir.glob("*.json")))
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(data_dir).as_posix().encode("utf-8")
        payload = path.read_bytes() if path.exists() else b"<missing>"
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def build_migration(data_dir, *, plan_repository=None, history_repository=None):
    """Return deterministic after-images and a redacted migration report."""
    data_dir = Path(data_dir).resolve()
    plans = plan_repository or JsonPlanRepository(data_dir / "plans")
    history = history_repository or JsonHistoryRepository(data_dir / "history.json")
    targets = {}
    report = {
        "state_token": _state_token(data_dir),
        "plans_migrated": [],
        "occurrences_backfilled": [],
        "history_migrated": False,
    }

    events = history.load_events(strict=True)
    history_by_key = {}
    for event in events:
        if not event.active:
            continue
        key = (event.dish_name_snapshot, event.cooked_on)
        if key in history_by_key:
            raise ValueError(f"ambiguous cooking-history evidence for {key[0]!r}")
        history_by_key[key] = event

    if plans.plans_dir.exists():
        for path in sorted(plans.plans_dir.glob("*.json")):
            raw_plan = json.loads(path.read_text(encoding="utf-8"))
            plan = WeekPlan.from_dict(raw_plan)
            if plan.week_id != path.stem:
                raise ValueError(f"plan file {path.name!r} contains week {plan.week_id!r}")
            changed = "schema_version" not in raw_plan
            for row in _BACKFILLS:
                if row["week"] != plan.week_id:
                    continue
                occurrence_id = _stable_occurrence_id(row)
                event = history_by_key.get((row["dish"], row["cooked_on"]))
                if event is None:
                    raise ValueError(
                        f"missing cooking-history evidence for {row['dish']!r}"
                    )
                _validate_backfill_event(event, row, occurrence_id)
                evidence_rows = [
                    meal
                    for day in plan.days.values()
                    for meal in day.meals
                    if meal.cook_event_id == event.id
                ]
                if any(meal.occurrence_id != occurrence_id for meal in evidence_rows):
                    raise ValueError(
                        f"cooking evidence {event.id!r} is attached to a conflicting occurrence"
                    )
                matches = [
                    (day_name, meal)
                    for day_name, day in plan.days.items()
                    for meal in day.meals
                    if meal.occurrence_id == occurrence_id
                ]
                if len(matches) > 1:
                    raise ValueError(f"duplicate backfill occurrence {occurrence_id!r}")
                expected = MealEntry(
                    dish=row["dish"],
                    portions=row["portions"],
                    occurrence_id=occurrence_id,
                    root_occurrence_id=occurrence_id,
                    status="cooked",
                    planned_for=None,
                    created_at=None,
                    updated_at=None,
                    status_changed_at=None,
                    cooked_on=row["cooked_on"],
                    cooked_time_precision="date",
                    cook_event_id=event.id,
                    provenance={
                        "source": "AUDIT-1A_evidence_backfill",
                        "backfilled": True,
                        "evidence": [
                            "legacy_native_history",
                            "verified_plan_removal_operation",
                        ],
                    },
                ).bind_to_plan(plan.week_id, row["day"])
                if matches:
                    day_name, existing = matches[0]
                    existing_data = existing.to_dict()
                    expected_data = expected.to_dict()
                    if day_name != row["day"] or existing_data != expected_data:
                        raise ValueError(
                            f"backfill occurrence {occurrence_id!r} conflicts with evidence"
                        )
                    if event.plan_occurrence_id is None:
                        event.plan_occurrence_id = occurrence_id
                        changed = True
                else:
                    plan.days[row["day"]].meals.append(expected)
                    event.plan_occurrence_id = occurrence_id
                    changed = True
                    report["occurrences_backfilled"].append({
                        "occurrence_id": occurrence_id,
                        "week": row["week"],
                        "day": row["day"],
                        "cook_event_id": event.id,
                    })
            after = _json_bytes(plan.to_dict())
            if changed or path.read_bytes() != after:
                targets[path.relative_to(data_dir).as_posix()] = after
                report["plans_migrated"].append(plan.week_id)

    history_after = _json_bytes({
        "schema_version": 2,
        "entries": [event.to_dict() for event in events],
    })
    history_path = data_dir / "history.json"
    history_before = history_path.read_bytes() if history_path.exists() else None
    if history_before != history_after:
        targets["history.json"] = history_after
        report["history_migrated"] = True

    report["targets"] = sorted(targets)
    return targets, report
