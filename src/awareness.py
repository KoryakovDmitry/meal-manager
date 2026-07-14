"""Exact-target Hermes turn-boundary inventory awareness."""

import importlib
import json
import logging
from pathlib import Path
from typing import Callable

from .state_sync import build_inventory_snapshot, format_inventory_notice

logger = logging.getLogger(__name__)
CONFIG_FILENAME = "awareness_targets.json"
MAX_CONFIG_BYTES = 32_768
MAX_TARGETS = 20


class AwarenessConfigError(ValueError):
    """The local awareness target allowlist is invalid."""


def load_awareness_targets(path: Path) -> frozenset[tuple[str, str, str]]:
    """Load a strict local allowlist; missing config disables awareness."""
    path = Path(path)
    if not path.exists():
        return frozenset()
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise AwarenessConfigError("awareness config is too large")
        raw = json.loads(path.read_text(encoding="utf-8"))
    except AwarenessConfigError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AwarenessConfigError("awareness config cannot be read") from exc
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "targets"}:
        raise AwarenessConfigError("awareness config envelope is invalid")
    if raw["schema_version"] != 1 or not isinstance(raw["targets"], list):
        raise AwarenessConfigError("awareness config schema is unsupported")
    if len(raw["targets"]) > MAX_TARGETS:
        raise AwarenessConfigError("awareness config has too many targets")

    targets: set[tuple[str, str, str]] = set()
    for entry in raw["targets"]:
        if not isinstance(entry, dict) or set(entry) != {
            "platform", "chat_id", "thread_id"
        }:
            raise AwarenessConfigError("awareness target is invalid")
        values = (
            entry["platform"].strip() if isinstance(entry["platform"], str) else "",
            entry["chat_id"].strip() if isinstance(entry["chat_id"], str) else "",
            entry["thread_id"].strip() if isinstance(entry["thread_id"], str) else "",
        )
        if not all(values) or any(len(value) > 200 for value in values):
            raise AwarenessConfigError("awareness target values are invalid")
        targets.add(values)
    return frozenset(targets)


def _gateway_session_value(name: str, default: str = "") -> str:
    module = importlib.import_module("gateway.session_context")
    return module.get_session_env(name, default)


def build_pre_llm_hook(
    repo,
    config_path: Path,
    *,
    get_session_value: Callable[[str, str], str] | None = None,
):
    """Create a fail-closed exact-topic ``pre_llm_call`` hook."""
    session_value = get_session_value or _gateway_session_value
    config_path = Path(config_path)

    def pre_llm_call(*, platform: str = "", **_kwargs):
        try:
            targets = load_awareness_targets(config_path)
            if not targets:
                return None
            chat_id = str(session_value("HERMES_SESSION_CHAT_ID", "") or "")
            thread_id = str(session_value("HERMES_SESSION_THREAD_ID", "") or "")
            target = (str(platform or "").strip(), chat_id.strip(), thread_id.strip())
            if target not in targets:
                return None
        except Exception:
            logger.exception("Inventory awareness target resolution failed")
            return None

        try:
            with repo.lock:
                snapshot = build_inventory_snapshot(repo.load_catalog_items())
            return {"context": format_inventory_notice(snapshot)}
        except Exception:
            logger.exception("Inventory awareness snapshot failed")
            return {
                "context": (
                    "[MEAL_MANAGER INVENTORY STATE — authoritative state unavailable]\n"
                    "Inventory freshness is unknown. Before any inventory-dependent "
                    "answer or action, call sync_meal_manager_state. Do not rely on older "
                    "conversation summaries.\n"
                    "[/MEAL_MANAGER INVENTORY STATE]"
                )
            }

    return pre_llm_call
