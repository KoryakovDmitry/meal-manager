"""JSON-file-backed implementation of TuningRepository."""

import json
import logging
import threading
from pathlib import Path

from .. import atomic_write_json, read_json_file, tuning

logger = logging.getLogger(__name__)


class JsonTuningRepository:
    """Stores the online-learner state as a JSON object in a single file.

    On a missing, unreadable, or schema-invalid file, ``load`` returns a fresh
    initialized state rather than raising — so suggestions keep working (and
    reproduce today's 0.6/0.4 blend) until real data accumulates. The
    repository owns its own lock; the cook handler holds it around the
    load-modify-save sequence.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = threading.Lock()

    def load(self) -> dict:
        missing = object()
        try:
            raw = read_json_file(self.path, missing=missing)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Failed to load %s: %s", self.path.name, exc)
            return tuning.initialize_state()
        if raw is missing:
            return tuning.initialize_state()
        return tuning.validate_state(raw)

    def save(self, state: dict) -> None:
        atomic_write_json(self.path, state)
