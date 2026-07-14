"""JSON-file-backed structured kitchen inventory repository."""

import fcntl
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .. import atomic_write_json
from ..dish import Dish
from ..inventory import InventoryItem

SCHEMA_VERSION = 3
SUPPORTED_SCHEMA_VERSIONS = frozenset({2, 3})
_MIGRATION_NAMESPACE = uuid.UUID("72c45a1a-73c7-4e07-84dd-e20b6e342f95")


class InventoryDataError(ValueError):
    """Persisted inventory is corrupt or uses an unsupported schema."""


class _InventoryFileLock:
    """Re-entrant thread + advisory process lock for one inventory path."""

    def __init__(self, path_getter) -> None:
        self._path_getter = path_getter
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self):
        self._thread_lock.acquire()
        depth = getattr(self._local, "depth", 0)
        try:
            if depth == 0:
                data_path = Path(self._path_getter())
                data_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path = data_path.with_name(data_path.name + ".lock")
                handle = open(lock_path, "a+", encoding="utf-8")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except Exception:
                    handle.close()
                    raise
                self._local.handle = handle
                self._local.path = data_path
            self._local.depth = depth + 1
            return self
        except Exception:
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        depth = self._local.depth - 1
        self._local.depth = depth
        try:
            if depth == 0:
                handle = self._local.handle
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
                del self._local.handle
                del self._local.path
        finally:
            self._thread_lock.release()

    @property
    def active_path(self) -> Path | None:
        return getattr(self._local, "path", None)


class JsonFridgeRepository:
    """Store known products while exposing current-stock compatibility views."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = _InventoryFileLock(lambda: self.path)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _legacy_timestamp(self) -> str:
        return self._now()

    def _io_path(self) -> Path:
        return self.lock.active_path or self.path

    def _read_raw(self):
        path = self._io_path()
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            raise InventoryDataError(f"Invalid inventory file: {exc}") from exc

    @staticmethod
    def _migration_id(name: str) -> str:
        return "inv_" + uuid.uuid5(_MIGRATION_NAMESPACE, name).hex

    @staticmethod
    def _new_id() -> str:
        return "inv_" + uuid.uuid4().hex

    def load_catalog_items(self) -> list[InventoryItem]:
        """Return every known stocked product, including unavailable records."""
        raw = self._read_raw()
        if isinstance(raw, list):
            timestamp = self._legacy_timestamp()
            items: list[InventoryItem] = []
            seen: set[str] = set()
            for index, value in enumerate(raw):
                if not isinstance(value, str):
                    raise InventoryDataError(
                        f"Invalid legacy inventory entry at index {index}"
                    )
                name = Dish.normalize_ingredient(value)
                if not name:
                    raise InventoryDataError(
                        f"Blank legacy inventory entry at index {index}"
                    )
                if name in seen:
                    continue
                seen.add(name)
                try:
                    items.append(InventoryItem(
                        id=self._migration_id(name),
                        name=name,
                        available=True,
                        created_at=timestamp,
                        updated_at=timestamp,
                    ))
                except ValueError as exc:
                    raise InventoryDataError(
                        f"Invalid legacy inventory entry at index {index}: {exc}"
                    ) from exc
            return items

        if not isinstance(raw, dict):
            raise InventoryDataError(
                "Inventory must be a legacy array or versioned object"
            )
        if set(raw) != {"schema_version", "items"}:
            raise InventoryDataError(
                "Inventory envelope contains unknown or missing fields"
            )
        version = raw["schema_version"]
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise InventoryDataError(
                f"Unsupported inventory schema_version: {version}"
            )
        if not isinstance(raw["items"], list):
            raise InventoryDataError("Inventory items must be an array")
        if version == 2 and any(
            isinstance(value, dict) and "available" in value
            for value in raw["items"]
        ):
            raise InventoryDataError(
                "Schema v2 inventory item contains v3 availability"
            )
        if version == 3 and any(
            not isinstance(value, dict) or "available" not in value
            for value in raw["items"]
        ):
            raise InventoryDataError(
                "Schema v3 inventory item is missing availability"
            )

        try:
            items = [InventoryItem.from_dict(value) for value in raw["items"]]
        except (TypeError, ValueError) as exc:
            raise InventoryDataError(
                f"Invalid persisted inventory item: {exc}"
            ) from exc
        names = [item.name for item in items]
        ids = [item.id for item in items]
        if len(names) != len(set(names)):
            raise InventoryDataError("Duplicate inventory item name")
        if len(ids) != len(set(ids)):
            raise InventoryDataError("Duplicate inventory item id")
        return items

    def load_items(self) -> list[InventoryItem]:
        """Return only products currently present in kitchen inventory."""
        return [item for item in self.load_catalog_items() if item.available]

    def load(self) -> list[str]:
        """Backward-compatible current-stock name projection."""
        return [item.name for item in self.load_items()]

    def load_set(self) -> set[str]:
        return set(self.load())

    def save_items(self, items: list[InventoryItem]) -> None:
        """Persist the complete catalog record set exactly as supplied."""
        with self.lock:
            self._save_items_unlocked(items)

    def _save_items_unlocked(self, items: list[InventoryItem]) -> None:
        if not isinstance(items, list) or not all(
            isinstance(item, InventoryItem) for item in items
        ):
            raise ValueError("items must be InventoryItem records")
        names = [item.name for item in items]
        ids = [item.id for item in items]
        if len(names) != len(set(names)):
            raise ValueError("Duplicate inventory item name")
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate inventory item id")
        atomic_write_json(self._io_path(), {
            "schema_version": SCHEMA_VERSION,
            "items": [item.to_dict() for item in items],
        })

    def _with_availability(
        self,
        item: InventoryItem,
        available: bool,
    ) -> InventoryItem:
        values = item.to_dict()
        values["available"] = available
        values["updated_at"] = self._now()
        return InventoryItem.from_dict(values)

    def _replenished(self, item: InventoryItem, fields: dict) -> InventoryItem:
        allowed = {
            "quantity", "unit", "package_count", "storage",
            "expires_on", "comment",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(
                f"Unknown replenishment fields: {sorted(unknown)}"
            )
        values = item.to_dict()
        values.update({
            "available": True,
            "quantity": fields.get("quantity"),
            "unit": fields.get("unit"),
            "package_count": fields.get("package_count"),
            "storage": fields.get("storage", item.storage),
            "expires_on": fields.get("expires_on"),
            "comment": fields.get("comment"),
            "updated_at": self._now(),
        })
        return InventoryItem.from_dict(values)

    def save(self, ingredients: list[str]) -> None:
        """Replace current stock while preserving unavailable product identity."""
        with self.lock:
            self._save_names_unlocked(ingredients)

    def _save_names_unlocked(self, ingredients: list[str]) -> None:
        if not isinstance(ingredients, list):
            raise ValueError("ingredients must be a list")
        normalized: list[str] = []
        seen: set[str] = set()
        for value in ingredients:
            if not isinstance(value, str):
                raise ValueError("ingredient names must be strings")
            name = Dish.normalize_ingredient(value)
            if not name:
                raise ValueError("ingredient name cannot be empty")
            if name not in seen:
                normalized.append(name)
                seen.add(name)

        catalog = self.load_catalog_items()
        existing = {item.name: item for item in catalog}
        wanted = set(normalized)
        result: list[InventoryItem] = []
        for item in catalog:
            if item.name in wanted and not item.available:
                item = self._replenished(item, {})
            elif item.name not in wanted and item.available:
                item = self._with_availability(item, False)
            result.append(item)

        now = self._now()
        for name in normalized:
            if name not in existing:
                result.append(InventoryItem(
                    id=self._new_id(),
                    name=name,
                    created_at=now,
                    updated_at=now,
                ))
        self._save_items_unlocked(result)

    def add_item(self, **fields) -> InventoryItem:
        if "available" in fields:
            raise ValueError("available cannot be supplied when adding inventory")
        with self.lock:
            items = self.load_catalog_items()
            now = self._now()
            item = InventoryItem(
                id=self._new_id(),
                available=True,
                created_at=now,
                updated_at=now,
                **fields,
            )
            existing = next(
                (current for current in items if current.name == item.name),
                None,
            )
            if existing is not None:
                if existing.available:
                    raise ValueError(
                        f"Ingredient '{item.name}' already exists in kitchen inventory"
                    )
                replenished = self._replenished(existing, {
                    key: value for key, value in fields.items() if key != "name"
                })
                items[items.index(existing)] = replenished
                self._save_items_unlocked(items)
                return replenished
            items.append(item)
            self._save_items_unlocked(items)
            return item

    def replenish_item(
        self,
        *,
        item_id: str | None = None,
        name: str | None = None,
        **fields,
    ) -> InventoryItem:
        if bool(item_id) == bool(name):
            raise ValueError("Provide exactly one of item_id or name")
        with self.lock:
            items = self.load_catalog_items()
            if item_id:
                key = item_id.strip()
                current = next((item for item in items if item.id == key), None)
                if current is None:
                    raise LookupError(f"Inventory item '{key}' not found")
            else:
                normalized = Dish.normalize_ingredient(name)
                current = next(
                    (item for item in items if item.name == normalized),
                    None,
                )
                if current is None:
                    return self.add_item(name=normalized, **fields)
            if current.available:
                raise ValueError(
                    f"Ingredient '{current.name}' already exists in kitchen inventory"
                )
            replenished = self._replenished(current, fields)
            items[items.index(current)] = replenished
            self._save_items_unlocked(items)
            return replenished

    def edit_item(self, item_id: str, patch: dict) -> InventoryItem:
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError("item_id cannot be empty")
        if not isinstance(patch, dict) or not patch:
            raise ValueError("At least one inventory field must be provided")
        allowed = {
            "name", "quantity", "unit", "package_count", "storage",
            "expires_on", "comment",
        }
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(
                f"Unknown editable inventory fields: {sorted(unknown)}"
            )

        with self.lock:
            items = self.load_catalog_items()
            index = next((
                i for i, item in enumerate(items)
                if item.id == item_id.strip() and item.available
            ), None)
            if index is None:
                raise LookupError(
                    f"Inventory item '{item_id.strip()}' not found"
                )
            current = items[index]
            values = current.to_dict()
            values.update(patch)
            if patch.get("quantity", object()) is None and "unit" not in patch:
                values["unit"] = None
            if patch.get("unit", object()) is None and "quantity" not in patch:
                values["quantity"] = None
            values["id"] = current.id
            values["created_at"] = current.created_at
            values["available"] = True
            candidate = InventoryItem.from_dict(values)
            if any(
                other.id != current.id and other.name == candidate.name
                for other in items
            ):
                raise ValueError(
                    f"Ingredient '{candidate.name}' already exists in kitchen inventory"
                )
            if candidate.to_dict() == current.to_dict():
                return current
            candidate.updated_at = self._now()
            candidate = InventoryItem.from_dict(candidate.to_dict())
            items[index] = candidate
            self._save_items_unlocked(items)
            return candidate

    def remove_item(self, item_id: str) -> InventoryItem:
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError("item_id cannot be empty")
        with self.lock:
            items = self.load_catalog_items()
            index = next((
                i for i, item in enumerate(items)
                if item.id == item_id.strip() and item.available
            ), None)
            if index is None:
                raise LookupError(
                    f"Inventory item '{item_id.strip()}' not found"
                )
            removed = self._with_availability(items[index], False)
            items[index] = removed
            self._save_items_unlocked(items)
            return removed

    def rename_by_name(self, old_name: str, new_name: str) -> InventoryItem:
        old_normalized = Dish.normalize_ingredient(old_name)
        new_normalized = Dish.normalize_ingredient(new_name)
        with self.lock:
            current = next(
                (item for item in self.load_items() if item.name == old_normalized),
                None,
            )
            if current is None:
                raise LookupError(
                    f"Ingredient '{old_normalized}' not found in kitchen inventory"
                )
            return self.edit_item(current.id, {"name": new_normalized})

    def remove_items(self, items: list[str]) -> None:
        if not items:
            return
        to_remove = {Dish.normalize_ingredient(item) for item in items}
        with self.lock:
            catalog = self.load_catalog_items()
            changed = False
            result: list[InventoryItem] = []
            for item in catalog:
                if item.available and item.name in to_remove:
                    item = self._with_availability(item, False)
                    changed = True
                result.append(item)
            if changed:
                self._save_items_unlocked(result)
