"""JSON-file-backed implementation of a WeekPlan repository.

Plans are stored one-per-file under ``data/plans/`` as ``<week_id>.json``
(e.g. ``2026-W03.json``). This makes it trivial to list, copy, and archive.
"""

import fcntl
import json
import logging
import threading
from pathlib import Path

from .. import atomic_delete_json, atomic_write_json
from ..plan import WeekPlan

logger = logging.getLogger(__name__)


class _PlanDirectoryLock:
    """Re-entrant thread and process lock for one plans directory."""

    def __init__(self, repository) -> None:
        self.repository = repository
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self):
        self._thread_lock.acquire()
        depth = getattr(self._local, "depth", 0)
        try:
            if depth == 0:
                plans_dir = Path(self.repository.plans_dir)
                plans_dir.mkdir(parents=True, exist_ok=True)
                handle = open(plans_dir / ".plans.lock", "a+", encoding="utf-8")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except Exception:
                    handle.close()
                    raise
                self._local.handle = handle
            self._local.depth = depth + 1
            return self
        except Exception:
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, traceback):
        depth = self._local.depth - 1
        self._local.depth = depth
        try:
            if depth == 0:
                handle = self._local.handle
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    try:
                        handle.close()
                    finally:
                        del self._local.handle
        finally:
            self._thread_lock.release()
        return False


class JsonPlanRepository:
    """Stores weekly plans as individual JSON files under a directory."""

    def __init__(self, plans_dir: Path) -> None:
        self.plans_dir = Path(plans_dir)
        self.lock = _PlanDirectoryLock(self)

    def _path_for(self, week_id: str) -> Path:
        safe_week_id = WeekPlan.normalize_week_id(week_id)
        return self.plans_dir / f"{safe_week_id}.json"

    def load_strict(self, week_id: str) -> WeekPlan | None:
        path = self._path_for(week_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            plan = WeekPlan.from_dict(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError,
                KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid weekly plan '{week_id}': {exc}") from exc
        expected_week = WeekPlan.normalize_week_id(week_id)
        if plan.week_id != expected_week:
            raise ValueError(
                f"weekly plan filename '{expected_week}' conflicts with '{plan.week_id}'"
            )
        return plan

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
        with self.lock:
            self.plans_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self._path_for(plan.week_id), plan.to_dict())

    def delete(self, week_id: str) -> bool:
        """Delete a week plan file. Returns whether it existed."""
        with self.lock:
            path = self._path_for(week_id)
            return atomic_delete_json(path)

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
