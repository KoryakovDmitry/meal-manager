"""JSON-file-backed implementation of a PrepItem repository.

Storage format: ``{"prep_items": [...]}`` envelope, same pattern as
dishes.json. Each entry is a PrepItem.to_dict() serialization.
"""

import json
import logging
from pathlib import Path

from .. import atomic_write_json
from ..prep_item import PrepItem
from .file_lock import JsonFileLock

logger = logging.getLogger(__name__)


class JsonPrepItemRepository:
    """Stores prep items as ``{"prep_items": [...]}`` in a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = JsonFileLock(lambda: self.path)

    def load_strict(self) -> list[PrepItem]:
        if not self.path.exists():
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid prep catalog: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("prep_items", []), list):
            raise ValueError("invalid prep catalog envelope")
        try:
            return [PrepItem.from_dict(entry) for entry in data.get("prep_items", [])]
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid prep catalog entry: {exc}") from exc

    def load(self) -> list[PrepItem]:
        if not self.path.exists():
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
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
        raw_items = data.get("prep_items", [])
        if not isinstance(raw_items, list):
            return []

        result: list[PrepItem] = []
        for index, entry in enumerate(raw_items):
            try:
                result.append(PrepItem.from_dict(entry))
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "Skipping malformed prep item at index %s: %r (%s)",
                    index,
                    entry,
                    exc,
                )
                continue
        return result

    def save(self, items: list[PrepItem]) -> None:
        data = {"prep_items": [item.to_dict() for item in items]}
        atomic_write_json(self.path, data)
