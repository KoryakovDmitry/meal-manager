#!/usr/bin/env python3
"""Dry-run or apply the AUDIT-1A migration after an external locked backup."""

import argparse
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT.parent))

from meal_manager.src.audit.transaction import AuditTransactionManager  # noqa: E402
from meal_manager.src.migrations.audit1a import build_migration
from meal_manager.src.repositories.json_history import JsonHistoryRepository
from meal_manager.src.repositories.json_plan import JsonPlanRepository  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=PLUGIN_ROOT / "data")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-state-token")
    args = parser.parse_args()
    args.data_dir = args.data_dir.resolve()
    plans = JsonPlanRepository(args.data_dir / "plans")
    history = JsonHistoryRepository(args.data_dir / "history.json")

    if not args.apply:
        with plans.lock:
            with history.lock:
                _, report = build_migration(
                    args.data_dir,
                    plan_repository=plans,
                    history_repository=history,
                )
        print(json.dumps({"mode": "dry-run", **report}, ensure_ascii=False, indent=2))
        return 0
    if not args.expected_state_token:
        parser.error("--apply requires --expected-state-token from a fresh dry-run")

    manager = AuditTransactionManager(args.data_dir)
    with manager.lock, plans.lock, history.lock:
        targets, report = build_migration(
            args.data_dir,
            plan_repository=plans,
            history_repository=history,
        )
        if args.expected_state_token != report["state_token"]:
            raise SystemExit("state token changed; repeat backup and dry-run")
        if not targets:
            print(json.dumps(
                {"mode": "apply", "status": "no-op", **report},
                ensure_ascii=False,
                indent=2,
            ))
            return 0

        events = [{
            "event_type": "migration.audit1a.applied.v1",
            "entity": {"type": "meal_manager_dataset", "id": "primary"},
            "payload": {
                "target_count": len(targets),
                "plans_migrated": report["plans_migrated"],
                "occurrence_ids": [
                    item["occurrence_id"] for item in report["occurrences_backfilled"]
                ],
            },
        }]
        for item in report["occurrences_backfilled"]:
            events.append({
                "event_type": "meal.occurrence_backfilled.v1",
                "entity": {"type": "meal_occurrence", "id": item["occurrence_id"]},
                "payload": item,
            })
        try:
            result = manager.commit(
                operation="audit1a_migration",
                targets=targets,
                events=events,
                context={
                    "actor": {"type": "system"},
                    "surface": {"kind": "maintenance_cli"},
                    "correlation_id": "AUDIT-1A-production-migration",
                },
            )
        except Exception:
            result = manager.resolve_last_transaction()
            if result is None:
                raise
    print(json.dumps({
        "mode": "apply",
        "status": result["status"],
        "transaction_id": result["transaction_id"],
        **report,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
