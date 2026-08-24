"""JSON-file-backed structured kitchen inventory repository."""

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import atomic_write_json
from ..dish import Dish
from ..inventory import InventoryItem
from .file_lock import JsonFileLock

SCHEMA_VERSION = 6
SUPPORTED_SCHEMA_VERSIONS = frozenset({2, 3, 4, 5, 6})
_MIGRATION_NAMESPACE = uuid.UUID("72c45a1a-73c7-4e07-84dd-e20b6e342f95")
_EXPECTED_VERSION_UNSET = object()


class InventoryDataError(ValueError):
    """Persisted inventory is corrupt or uses an unsupported schema."""


class InventoryConflictError(ValueError):
    """A stale caller tried to mutate a newer inventory record."""

    def __init__(self, current_item: InventoryItem) -> None:
        self.current_item = current_item
        super().__init__(
            f"Inventory item '{current_item.name}' changed after it was loaded"
        )


class JsonFridgeRepository:
    """Store known products while exposing current-stock compatibility views."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = JsonFileLock(lambda: self.path)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _next_updated_at(self, current: InventoryItem) -> str:
        """Return a version timestamp strictly newer than ``current``."""
        candidate = datetime.fromisoformat(self._now())
        previous = datetime.fromisoformat(current.updated_at)
        if candidate <= previous:
            candidate = previous + timedelta(microseconds=1)
        return candidate.isoformat()

    @staticmethod
    def _check_expected_updated_at(
        current: InventoryItem,
        expected_updated_at: str | None,
    ) -> None:
        if expected_updated_at is None:
            return
        if (
            not isinstance(expected_updated_at, str)
            or not expected_updated_at.strip()
        ):
            raise ValueError("expected_updated_at must be a non-empty string")
        if current.updated_at != expected_updated_at.strip():
            raise InventoryConflictError(current)

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

    @staticmethod
    def _identity_matches(item: InventoryItem, name: str) -> bool:
        return name == item.name or name in item.aliases

    @classmethod
    def _resolve_from_items(cls, items, name: str, *, available_only=False):
        normalized = Dish.normalize_ingredient(name)
        return next((
            item for item in items
            if (not available_only or item.available)
            and cls._identity_matches(item, normalized)
        ), None)

    @staticmethod
    def _validate_identity_names(items, *, error_type=ValueError) -> None:
        owners: dict[str, str] = {}
        for item in items:
            for name in (item.name, *item.aliases):
                owner = owners.get(name)
                if owner is not None and owner != item.id:
                    raise error_type(f"Duplicate inventory name or alias: {name}")
                owners[name] = item.id

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
        version = raw.get("schema_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise InventoryDataError(
                "Inventory schema_version must be a JSON integer"
            )
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            raise InventoryDataError(
                f"Unsupported inventory schema version: {version}"
            )
        if not isinstance(raw["items"], list):
            raise InventoryDataError("Inventory items must be an array")
        if version < 6 and any(
            isinstance(value, dict) and "stock_cycle" in value
            for value in raw["items"]
        ):
            raise InventoryDataError(
                f"Schema v{version} inventory item contains v6 stock cycle"
            )
        if version == 2 and any(
            isinstance(value, dict)
            and ({"available", "category", "ever_stocked"} & set(value))
            for value in raw["items"]
        ):
            raise InventoryDataError(
                "Schema v2 inventory item contains v3 availability"
            )
        if version == 3 and any(
            not isinstance(value, dict)
            or "available" not in value
            or "category" in value
            or "ever_stocked" in value
            for value in raw["items"]
        ):
            raise InventoryDataError(
                "Schema v3 inventory item is missing availability"
            )
        if version == 4 and any(
            not isinstance(value, dict)
            or not {"available", "category", "ever_stocked"}.issubset(value)
            or "aliases" in value
            for value in raw["items"]
        ):
            raise InventoryDataError(
                "Schema v4 inventory item is missing category lifecycle fields"
            )
        if version == 5 and any(
            not isinstance(value, dict)
            or not {"available", "category", "ever_stocked", "aliases"}.issubset(value)
            for value in raw["items"]
        ):
            raise InventoryDataError(
                "Schema v5 inventory item is missing alias lifecycle fields"
            )
        if version == 6 and any(
            not isinstance(value, dict)
            or not {
                "available", "category", "ever_stocked", "aliases", "stock_cycle"
            }.issubset(value)
            for value in raw["items"]
        ):
            raise InventoryDataError(
                "Schema v6 inventory item is missing stock-cycle lifecycle fields"
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
        self._validate_identity_names(items, error_type=InventoryDataError)
        return items

    def load_items(self) -> list[InventoryItem]:
        """Return only products currently present in kitchen inventory."""
        return [item for item in self.load_catalog_items() if item.available]

    def resolve_ingredient(
        self,
        name: str,
        *,
        available_only: bool = False,
    ) -> InventoryItem | None:
        return self._resolve_from_items(
            self.load_catalog_items(), name, available_only=available_only
        )

    def load(self) -> list[str]:
        """Backward-compatible current-stock name projection."""
        return [item.name for item in self.load_items()]

    def load_set(self) -> set[str]:
        return {
            name
            for item in self.load_items()
            for name in (item.name, *item.aliases)
        }

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
        self._validate_identity_names(items)
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
        if item.available and not available:
            values["stock_cycle"] = item.stock_cycle + 1
        values["updated_at"] = self._next_updated_at(item)
        return InventoryItem.from_dict(values)

    def _replenished(self, item: InventoryItem, fields: dict) -> InventoryItem:
        allowed = {
            "quantity", "unit", "package_count", "storage",
            "expires_on", "comment", "category",
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
            "category": fields.get("category", item.category),
            "ever_stocked": True,
            "updated_at": self._next_updated_at(item),
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
        resolved = {
            name: self._resolve_from_items(catalog, name)
            for name in normalized
        }
        wanted_ids = {
            item.id for item in resolved.values() if item is not None
        }
        result: list[InventoryItem] = []
        for item in catalog:
            if item.id in wanted_ids and not item.available:
                item = self._replenished(item, {})
            elif item.id not in wanted_ids and item.available:
                item = self._with_availability(item, False)
            result.append(item)

        now = self._now()
        for name in normalized:
            if resolved[name] is None:
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
            existing = self._resolve_from_items(items, item.name)
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

    def receive_product(
        self,
        *,
        requested_name: str,
        exact_name: str,
        **fields,
    ) -> InventoryItem:
        requested = Dish.normalize_ingredient(requested_name)
        exact = Dish.normalize_ingredient(exact_name)
        if not requested or not exact:
            raise ValueError("requested_name and exact_name cannot be empty")
        allowed = {
            "quantity", "unit", "package_count", "storage",
            "expires_on", "comment", "category",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unknown receipt fields: {sorted(unknown)}")

        with self.lock:
            items = self.load_catalog_items()
            requested_item = next((
                item for item in items
                if requested == item.name or requested in item.aliases
            ), None)
            exact_item = next((item for item in items if item.name == exact), None)
            if (
                requested_item is not None
                and exact_item is not None
                and requested_item.id != exact_item.id
            ):
                raise ValueError(
                    "Requested and exact names belong to different product identities"
                )
            current = requested_item or exact_item
            if current is None:
                now = self._now()
                created = InventoryItem(
                    id=self._new_id(),
                    name=exact,
                    aliases=[requested] if requested != exact else [],
                    available=True,
                    ever_stocked=True,
                    created_at=now,
                    updated_at=now,
                    **fields,
                )
                items.append(created)
                self._save_items_unlocked(items)
                return created

            values = current.to_dict()
            aliases = list(current.aliases)
            for alias in (current.name, requested):
                if alias != exact and alias not in aliases:
                    aliases.append(alias)
            values.update({
                "name": exact,
                "aliases": aliases,
                "available": True,
                "ever_stocked": True,
                "quantity": fields.get("quantity"),
                "unit": fields.get("unit"),
                "package_count": fields.get("package_count"),
                "storage": fields.get("storage", current.storage),
                "expires_on": fields.get("expires_on"),
                "comment": fields.get("comment"),
                "category": fields.get("category", current.category),
            })
            candidate = InventoryItem.from_dict(values)
            if candidate.to_dict() == current.to_dict():
                return current
            candidate.updated_at = self._next_updated_at(current)
            candidate = InventoryItem.from_dict(candidate.to_dict())
            items[items.index(current)] = candidate
            self._save_items_unlocked(items)
            return candidate

    def replenish_item(
        self,
        *,
        item_id: str | None = None,
        name: str | None = None,
        expected_updated_at=_EXPECTED_VERSION_UNSET,
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
                current = self._resolve_from_items(items, normalized)
                if current is None:
                    if expected_updated_at not in (_EXPECTED_VERSION_UNSET, None):
                        raise LookupError(f"Ingredient '{normalized}' not found")
                    now = self._now()
                    created = InventoryItem(
                        id=self._new_id(),
                        name=normalized,
                        available=True,
                        ever_stocked=True,
                        created_at=now,
                        updated_at=now,
                        **fields,
                    )
                    items.append(created)
                    self._save_items_unlocked(items)
                    return created
            if expected_updated_at is None:
                raise InventoryConflictError(current)
            if expected_updated_at is not _EXPECTED_VERSION_UNSET:
                if not isinstance(expected_updated_at, str):
                    raise ValueError("expected_updated_at must be a non-empty string")
                self._check_expected_updated_at(current, expected_updated_at)
            if current.available:
                raise ValueError(
                    f"Ingredient '{current.name}' already exists in kitchen inventory"
                )
            replenished = self._replenished(current, fields)
            items[items.index(current)] = replenished
            self._save_items_unlocked(items)
            return replenished

    def edit_item(
        self,
        item_id: str,
        patch: dict,
        *,
        expected_updated_at: str | None = None,
    ) -> InventoryItem:
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError("item_id cannot be empty")
        if not isinstance(patch, dict) or not patch:
            raise ValueError("At least one inventory field must be provided")
        allowed = {
            "name", "quantity", "unit", "package_count", "storage",
            "expires_on", "comment", "category",
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
                if item.id == item_id.strip()
            ), None)
            if index is None:
                raise LookupError(
                    f"Inventory item '{item_id.strip()}' not found"
                )
            current = items[index]
            self._check_expected_updated_at(current, expected_updated_at)
            if not current.available:
                raise LookupError(
                    f"Inventory item '{item_id.strip()}' not found"
                )
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
            candidate.updated_at = self._next_updated_at(current)
            candidate = InventoryItem.from_dict(candidate.to_dict())
            items[index] = candidate
            self._save_items_unlocked(items)
            return candidate

    def set_product_category(
        self,
        name: str | None,
        category: str,
        *,
        item_id: str | None = None,
        allow_create: bool = False,
        expected_updated_at=_EXPECTED_VERSION_UNSET,
    ) -> InventoryItem:
        if bool(item_id) == bool(name):
            raise ValueError("Provide exactly one of item_id or name")
        normalized = Dish.normalize_ingredient(name) if name else None
        with self.lock:
            items = self.load_catalog_items()
            if item_id:
                key = item_id.strip()
                current = next((item for item in items if item.id == key), None)
                if current is None:
                    raise LookupError(f"Inventory item '{key}' not found")
            else:
                current = (
                    self._resolve_from_items(items, normalized)
                    if normalized is not None else None
                )
            if current is None:
                if not allow_create or normalized is None:
                    raise LookupError(f"Product '{normalized}' not found")
                if expected_updated_at not in (_EXPECTED_VERSION_UNSET, None):
                    raise LookupError(f"Product '{normalized}' not found")
                now = self._now()
                created = InventoryItem(
                    id=self._new_id(),
                    name=normalized,
                    available=False,
                    category=category,
                    ever_stocked=False,
                    created_at=now,
                    updated_at=now,
                )
                items.append(created)
                self._save_items_unlocked(items)
                return created

            if expected_updated_at is None:
                raise InventoryConflictError(current)
            if expected_updated_at is not _EXPECTED_VERSION_UNSET:
                if not isinstance(expected_updated_at, str):
                    raise ValueError("expected_updated_at must be a non-empty string")
                self._check_expected_updated_at(current, expected_updated_at)
            values = current.to_dict()
            values["category"] = category
            candidate = InventoryItem.from_dict(values)
            if candidate.category == current.category:
                return current
            candidate.updated_at = self._next_updated_at(current)
            candidate = InventoryItem.from_dict(candidate.to_dict())
            items[items.index(current)] = candidate
            self._save_items_unlocked(items)
            return candidate

    def remove_item(
        self,
        item_id: str,
        *,
        expected_updated_at: str | None = None,
    ) -> InventoryItem:
        if not isinstance(item_id, str) or not item_id.strip():
            raise ValueError("item_id cannot be empty")
        with self.lock:
            items = self.load_catalog_items()
            index = next((
                i for i, item in enumerate(items)
                if item.id == item_id.strip()
            ), None)
            if index is None:
                raise LookupError(
                    f"Inventory item '{item_id.strip()}' not found"
                )
            current = items[index]
            self._check_expected_updated_at(current, expected_updated_at)
            if not current.available:
                raise LookupError(
                    f"Inventory item '{item_id.strip()}' not found"
                )
            removed = self._with_availability(current, False)
            items[index] = removed
            self._save_items_unlocked(items)
            return removed

    def merge_product_identity(
        self,
        source_item_id: str,
        target_item_id: str,
        *,
        expected_source_updated_at: str,
        expected_target_updated_at: str,
    ) -> tuple[InventoryItem, list[str]]:
        """Absorb one unavailable duplicate identity into an active target."""
        if not isinstance(source_item_id, str) or not source_item_id.strip():
            raise ValueError("source_item_id cannot be empty")
        if not isinstance(target_item_id, str) or not target_item_id.strip():
            raise ValueError("target_item_id cannot be empty")
        source_id = source_item_id.strip()
        target_id = target_item_id.strip()
        if source_id == target_id:
            raise ValueError("source and target product identities must differ")
        if not isinstance(expected_source_updated_at, str) or not expected_source_updated_at.strip():
            raise ValueError("expected_source_updated_at cannot be empty")
        if not isinstance(expected_target_updated_at, str) or not expected_target_updated_at.strip():
            raise ValueError("expected_target_updated_at cannot be empty")

        with self.lock:
            items = self.load_catalog_items()
            source = next((item for item in items if item.id == source_id), None)
            target = next((item for item in items if item.id == target_id), None)
            if source is None:
                raise LookupError(f"Source product identity '{source_id}' not found")
            if target is None:
                raise LookupError(f"Target product identity '{target_id}' not found")
            self._check_expected_updated_at(source, expected_source_updated_at)
            self._check_expected_updated_at(target, expected_target_updated_at)
            if source.available:
                raise ValueError("source product identity must be out of stock")
            if not target.available:
                raise ValueError("target product identity must be in stock")
            if source.category != target.category:
                raise ValueError("source and target product categories must match")

            seen = {target.name}
            aliases: list[str] = []
            for name in (*target.aliases, source.name, *source.aliases):
                normalized = Dish.normalize_ingredient(name)
                if normalized not in seen:
                    seen.add(normalized)
                    aliases.append(normalized)
            transferred = [
                name for name in (source.name, *source.aliases)
                if name != target.name and name not in target.aliases
            ]
            candidate_data = target.to_dict()
            candidate_data["aliases"] = aliases
            candidate_data["updated_at"] = self._next_updated_at(target)
            candidate = InventoryItem.from_dict(candidate_data)
            merged_items = [
                candidate if item.id == target_id else item
                for item in items
                if item.id != source_id
            ]
            self._save_items_unlocked(merged_items)
            return candidate, transferred

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
                if item.available and any(
                    name in to_remove for name in (item.name, *item.aliases)
                ):
                    item = self._with_availability(item, False)
                    changed = True
                result.append(item)
            if changed:
                self._save_items_unlocked(result)
