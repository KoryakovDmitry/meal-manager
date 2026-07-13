"""JSON-file-backed implementation of a WeekPlan repository.

Plans are stored one-per-file under ``data/plans/`` as ``<week_id>.json``
(e.g. ``2026-W03.json``). This makes it trivial to list, copy, and archive.
"""

import json
import logging
import threading
from pathlib import Path

from .. import atomic_write_json
from ..plan import WeekPlan

logger = logging.getLogger(__name__)


class JsonPlanRepository:
    """Stores weekly plans as individual JSON files under a directory."""

    def __init__(self, plans_dir: Path) -> None:
        self.plans_dir = Path(plans_dir)
        self.lock = threading.Lock()

    def _path_for(self, week_id: str) -> Path:
        safe_week_id = WeekPlan.normalize_week_id(week_id)
        return self.plans_dir / f"{safe_week_id}.json"

    def load(self, week_id: str) -> WeekPlan | None:
        """Load a single week plan. Returns ``None`` if it doesn't exist."""
        path = self._path_for(week_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Failed to load plan %s: %s", path.name, exc)
            return None
        try:
            plan = WeekPlan.from_dict(data)
            expected_week = WeekPlan.normalize_week_id(week_id)
            if plan.week_id != expected_week:
                logger.warning(
                    "Ignoring %s: embedded week %s does not match filename",
                    path.name,
                    plan.week_id,
                )
                return None
            return plan
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Malformed plan in %s: %s", path.name, exc)
            return None

    def save(self, plan: WeekPlan) -> None:
        """Save a week plan to its individual file."""
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path_for(plan.week_id), plan.to_dict())

    def delete(self, week_id: str) -> bool:
        """Delete a week plan file. Returns whether it existed."""
        path = self._path_for(week_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_weeks(self) -> list[dict]:
        """List all week plans with their status, sorted by week_id descending."""
        self.plans_dir.mkdir(parents=True, exist_ok=True)
        result = []
        for path in sorted(self.plans_dir.glob("*.json"), reverse=True):
            week_id = path.stem
            try:
                WeekPlan.normalize_week_id(week_id)
            except ValueError:
                logger.warning("Skipping non-ISO plan filename: %s", path.name)
                continue
            plan = self.load(week_id)
            if plan is not None:
                meal_count = sum(
                    len(plan.days[d].meals) for d in plan.days
                )
                result.append({
                    "week": plan.week_id,
                    "status": plan.status,
                    "meals_count": meal_count,
                    "prep_count": len(plan.prep),
                })
        return result
