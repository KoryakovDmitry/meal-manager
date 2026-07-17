"""Shared helpers for tool handlers.

Validation, normalization, and the common ``@tool_handler`` decorator live
here so each individual handler module stays focused on its own tool's logic.
"""

import functools
import json
import logging
from datetime import date

from ..audit import AuditConflictError, audit_manager
from ..audit.context import audit_scope
from ..dish import Dish
from ..repositories import history_repo
from ..repositories.json_fridge import InventoryDataError
from ..repositories.json_history import HistoryDataError

logger = logging.getLogger(__name__)

# tool_handler creates loggers under the hardcoded ``meal_manager.handlers``
# namespace (independent of how the package is imported). Attach a NullHandler
# there once so library users without logging configured don't see noise.
logging.getLogger("meal_manager.handlers").addHandler(logging.NullHandler())

# ---------------------------------------------------------------------------
# Input limits (safety net for LLM-generated arguments)
# ---------------------------------------------------------------------------

MAX_NAME_LEN = 200
MAX_INGREDIENTS = 100
MAX_BATCH_SIZE = 50
MAX_FRIDGE_UPDATE = 200

_MUTATING_TOOLS = {
    "update_fridge_inventory", "rename_fridge_item", "add_inventory_item",
    "edit_inventory_item", "remove_inventory_item", "merge_product_identity",
    "set_product_category", "replenish_product", "register_cooked_meal",
    "delete_history_entry", "add_dish", "add_dishes_batch", "delete_dish",
    "edit_dish", "set_dish_instructions", "clear_fridge",
    "init_ingredient_session", "dii_add_suggested", "dii_skip_suggested",
    "dii_remove_ingredient", "dii_add_manual", "dii_clear_all",
    "finalize_ingredient_session", "dii_get_state", "add_prep_item",
    "delete_prep_item", "make_prep", "create_week_plan", "add_meal_to_plan",
    "remove_meal_from_plan", "set_plan_status", "repeat_week_plan",
    "generate_shopping_list", "add_manual_shopping_item",
    "receive_shopping_item", "estimate_plan_cost", "split_shopping_list",
}


# ---------------------------------------------------------------------------
# Handler decorator
# ---------------------------------------------------------------------------
# Centralizes the boilerplate every tool used to repeat: JSON serialization
# of the success result, logging + structured error envelope on failure.
# Handlers return Python objects and raise on validation/business errors.

def tool_handler(name: str):
    """Wrap a tool function with JSON serialization and a unified error envelope.

    The wrapped function returns a Python object (dict, list, str, ...). On
    success it is encoded with ``json.dumps(..., ensure_ascii=False)``. Any
    exception is logged via ``logger.exception`` and surfaced as
    ``{"error": str(exc)}`` so all tool errors share one shape.
    """
    log = logging.getLogger(f"meal_manager.handlers.{name}")

    def decorate(fn):
        @functools.wraps(fn)
        def runner(args, **kwargs):
            try:
                if name in _MUTATING_TOOLS:
                    with audit_scope(
                        operation=name,
                        manager=audit_manager,
                        actor_type="agent",
                        surface_kind="native_tool",
                    ):
                        result = fn(args, **kwargs)
                else:
                    result = fn(args, **kwargs)
                return json.dumps(result, ensure_ascii=False)
            except Exception as exc:
                log.exception("%s failed", name)
                if isinstance(exc, InventoryDataError):
                    message = "Inventory storage is temporarily unavailable"
                elif isinstance(exc, (AuditConflictError, HistoryDataError)):
                    message = "Storage is temporarily unavailable"
                elif isinstance(exc, OSError):
                    message = "Storage is temporarily unavailable"
                else:
                    message = str(exc)
                return json.dumps({"error": message}, ensure_ascii=False)

        return runner

    return decorate


def require_arg(args: dict, key: str):
    """Fetch a required argument, raising a clear message if it is absent.

    Handlers used to index ``args[key]`` directly, so a missing field surfaced
    as a bare ``KeyError`` (``{"error": "'key'"}``). This yields an explicit
    "required argument" message instead.
    """
    if not isinstance(args, dict) or key not in args:
        raise ValueError(f"'{key}' is a required argument")
    return args[key]


def reject_unknown_args(args: dict, allowed: set[str]) -> None:
    if not isinstance(args, dict):
        raise ValueError("tool arguments must be an object")
    unknown = set(args) - allowed
    if unknown:
        raise ValueError(f"unknown arguments: {sorted(unknown)}")


def maybe_parse_json_arg(value):
    """Coerce a possibly-JSON-string argument to its parsed form.

    Some LLMs serialize array/object arguments as JSON strings. Returns the
    parsed value on success, or the original string unchanged if it is not valid
    JSON, leaving type validation to the caller. Shared by every handler that
    accepts array/object arguments so the coercion behaves identically.
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _normalize_label(value: str, *, label: str) -> str:
    normalized = Dish._clean(value, label=label.lower())
    if not normalized:
        raise ValueError(f"{label} cannot be empty")
    if len(normalized) > MAX_NAME_LEN:
        raise ValueError(f"{label} too long (max {MAX_NAME_LEN} chars)")
    return normalized


def normalize_dish_name(name: str) -> str:
    return _normalize_label(name, label="Dish name")


def normalize_ingredient_name(name: str) -> str:
    return _normalize_label(name, label="Ingredient name")


def normalize_ingredients(ingredients) -> dict:
    """Accept ingredients as dict {name: bool} or list [name, ...] (all essential).
    Also handles JSON strings (some LLMs serialize the argument).
    Raises ValueError if the input cannot be parsed."""
    ingredients = maybe_parse_json_arg(ingredients)
    if isinstance(ingredients, str):
        raise ValueError(f"Cannot parse ingredients string: {ingredients!r}")
    if isinstance(ingredients, list):
        result = {}
        for ing in ingredients:
            result[normalize_ingredient_name(ing)] = True
    elif isinstance(ingredients, dict):
        result = {}
        for key, value in ingredients.items():
            if not isinstance(value, bool):
                raise ValueError(f"ingredient '{key}' must be true or false")
            result[normalize_ingredient_name(key)] = value
    else:
        raise ValueError(f"ingredients must be a dict or list, got {type(ingredients).__name__}")
    if not result:
        raise ValueError("ingredients cannot be empty")
    # Enforce the cap on the de-duplicated result, so a list containing repeats
    # that collapses under the limit is still accepted.
    if len(result) > MAX_INGREDIENTS:
        raise ValueError(f"Too many ingredients (max {MAX_INGREDIENTS})")
    return result


def days_since_last_cook() -> dict[str, int]:
    """Build a mapping of dish name -> days since it was last cooked."""
    history = history_repo.load()
    today = date.today()
    result = {}
    for name, date_str in history.items():
        try:
            days = (today - date.fromisoformat(date_str)).days
        except ValueError as exc:
            logger.warning("Skipping malformed history entry %r: %s", name, exc)
            continue
        # history_repo.load() already returns normalized (stripped/lowercased)
        # keys, so no re-normalization is needed here.
        result[name] = max(days, 0)
    return result
