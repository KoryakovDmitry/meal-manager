"""JSON-file-backed implementation of DishRepository."""

import hashlib
import json
import logging
from pathlib import Path

from .. import atomic_write_json
from ..dish import Dish
from .file_lock import JsonFileLock

logger = logging.getLogger(__name__)


def dish_catalog_version(dishes: list[Dish]) -> str:
    payload = json.dumps(
        [dish.to_dict() for dish in dishes],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class JsonDishRepository:
    """Stores the dish catalog as ``{"dishes": [...]}`` in a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = JsonFileLock(lambda: self.path)

    def _io_path(self) -> Path:
        return self.lock.active_path or self.path

    def load_strict(self) -> list[Dish]:
        path = self._io_path()
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid dish catalog: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("dishes", []), list):
            raise ValueError("invalid dish catalog envelope")
        try:
            return [Dish.from_dict(entry) for entry in data.get("dishes", [])]
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid dish catalog entry: {exc}") from exc

    def load(self) -> list[Dish]:
        path = self._io_path()
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Failed to load %s: %s", self.path.name, exc)
            return []
        if not isinstance(data, dict):
            logger.warning(
                "Ignoring %s with unexpected top-level type: %s",
                self.path.name,
                type(data).__name__,
            )
            return []
        raw_dishes = data.get("dishes", [])
        if not isinstance(raw_dishes, list):
            logger.warning(
                "Ignoring %s with non-list dishes field: %r",
                self.path.name,
                raw_dishes,
            )
            return []
        result: list[Dish] = []
        for index, entry in enumerate(raw_dishes):
            try:
                result.append(Dish.from_dict(entry))
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping malformed dish entry at index %s: %r (%s)",
                    index,
                    entry,
                    exc,
                )
                continue
        return result

    def _read_malformed(self) -> list:
        """Return the raw dish entries currently on disk that ``load`` cannot parse.

        ``load`` skips entries ``Dish.from_dict`` rejects, so a naive
        ``save(load())`` would permanently erase them. Callers always hold
        ``self.lock`` across load-modify-save, so re-reading the file here (the
        file is unchanged under the lock) lets ``save`` round-trip those rows
        verbatim instead of dropping them.
        """
        path = self._io_path()
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError, OSError):
            return []
        if not isinstance(data, dict):
            return []
        raw_dishes = data.get("dishes", [])
        if not isinstance(raw_dishes, list):
            return []
        malformed = []
        for entry in raw_dishes:
            try:
                Dish.from_dict(entry)
            except (AttributeError, KeyError, TypeError, ValueError):
                malformed.append(entry)
        return malformed

    @staticmethod
    def _entry_name(entry) -> str | None:
        """Normalized name of a raw dish entry, or None if it has no usable name."""
        try:
            return Dish.normalize_name(entry["name"])
        except (TypeError, KeyError, ValueError):
            return None

    def save(self, dishes: list[Dish]) -> None:
        # Preserve any unparseable entries already on disk so an unrelated write
        # never silently deletes a legacy/hand-edited row it couldn't load. Drop
        # any preserved row whose name collides with a dish being saved, so a
        # live dish can't spawn a permanent, un-removable duplicate-named ghost.
        saved_names = {dish.name for dish in dishes}
        preserved = [
            entry for entry in self._read_malformed()
            if self._entry_name(entry) not in saved_names
        ]
        data = {"dishes": [dish.to_dict() for dish in dishes] + preserved}
        atomic_write_json(self._io_path(), data)

    def restore(self, dish: Dish) -> bool:
        """Re-add *dish* if a same-named entry is no longer in the catalog.

        Used as a delta-rollback for delete: only restores the deleted dish if
        a concurrent writer hasn't already replaced it.
        """
        with self.lock:
            dishes = self.load()
            if any(d.name == dish.name for d in dishes):
                return False
            dishes.append(dish)
            self.save(dishes)
            return True
