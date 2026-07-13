"""JSON-file-backed implementation of a PrepItem repository.

Storage format: ``{"prep_items": [...]}`` envelope, same pattern as
dishes.json. Each entry is a PrepItem.to_dict() serialization.
"""

import json
import logging
import threading
from pathlib import Path

from .. import atomic_write_json
from ..prep_item import PrepItem

logger = logging.getLogger(__name__)


class JsonPrepItemRepository:
    """Stores prep items as ``{"prep_items": [...]}`` in a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = threading.Lock()

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
