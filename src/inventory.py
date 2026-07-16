"""Structured kitchen inventory item domain model."""

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from .dish import Dish

ALLOWED_UNITS = frozenset({
    "g", "kg", "ml", "l", "pcs", "pack", "can", "jar", "bottle", "portion",
})
ALLOWED_STORAGE = frozenset({"fridge", "freezer", "pantry", "counter"})
ALLOWED_CATEGORIES = frozenset({"product", "prep", "ready_meal"})
MAX_QUANTITY = Decimal("1000000000")
MAX_QUANTITY_DECIMALS = 6
MAX_PACKAGE_COUNT = 10_000
MAX_COMMENT_LEN = 1_000
MAX_ID_LEN = 100
MAX_NAME_LEN = 200
EXPIRING_SOON_DAYS = 3
_CALENDAR_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _canonical_quantity(value) -> str:
    if isinstance(value, bool):
        raise ValueError("quantity must be a positive finite decimal")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("quantity must be a positive finite decimal") from exc
    if not decimal_value.is_finite() or decimal_value <= 0:
        raise ValueError("quantity must be a positive finite decimal")
    if decimal_value > MAX_QUANTITY:
        raise ValueError(f"quantity must not exceed {MAX_QUANTITY}")
    exponent = decimal_value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise ValueError("quantity must be a finite decimal")
    decimal_places = max(0, -exponent)
    if decimal_places > MAX_QUANTITY_DECIMALS:
        raise ValueError(
            f"quantity supports at most {MAX_QUANTITY_DECIMALS} decimal places"
        )
    return format(decimal_value.normalize(), "f")


def _canonical_timestamp(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be an ISO datetime with timezone")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO datetime with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.isoformat()


@dataclass
class InventoryItem:
    id: str
    name: str
    quantity: str | None = None
    unit: str | None = None
    package_count: int | None = None
    storage: str | None = None
    expires_on: str | None = None
    comment: str | None = None
    created_at: str = ""
    updated_at: str = ""
    available: bool = True
    category: str = "product"
    ever_stocked: bool = True
    aliases: list[str] = field(default_factory=list)
    stock_cycle: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("id cannot be empty")
        self.id = self.id.strip()
        if len(self.id) > MAX_ID_LEN:
            raise ValueError(f"id too long (max {MAX_ID_LEN} chars)")

        if not isinstance(self.name, str):
            raise ValueError("name must be a string")
        self.name = Dish.normalize_ingredient(self.name)
        if not self.name:
            raise ValueError("name cannot be empty")
        if len(self.name) > MAX_NAME_LEN:
            raise ValueError(f"name too long (max {MAX_NAME_LEN} chars)")

        if not isinstance(self.available, bool):
            raise ValueError("available must be a boolean")
        if not isinstance(self.category, str):
            raise ValueError("category must be a string")
        self.category = self.category.strip().lower()
        if self.category not in ALLOWED_CATEGORIES:
            raise ValueError(f"unsupported category: {self.category}")
        if not isinstance(self.ever_stocked, bool):
            raise ValueError("ever_stocked must be a boolean")
        if self.available and not self.ever_stocked:
            raise ValueError("available inventory must have been stocked")
        if (
            isinstance(self.stock_cycle, bool)
            or not isinstance(self.stock_cycle, int)
            or self.stock_cycle < 0
        ):
            raise ValueError("stock_cycle must be a non-negative integer")

        if not isinstance(self.aliases, list):
            raise ValueError("aliases must be a list")
        normalized_aliases: list[str] = []
        seen_aliases: set[str] = set()
        for raw_alias in self.aliases:
            if not isinstance(raw_alias, str):
                raise ValueError("aliases must contain strings")
            alias = Dish.normalize_ingredient(raw_alias)
            if not alias:
                raise ValueError("alias cannot be empty")
            if len(alias) > MAX_NAME_LEN:
                raise ValueError(f"alias too long (max {MAX_NAME_LEN} chars)")
            if alias != self.name and alias not in seen_aliases:
                normalized_aliases.append(alias)
                seen_aliases.add(alias)
        self.aliases = normalized_aliases

        if self.quantity is None:
            if self.unit is not None:
                raise ValueError("unit requires quantity")
        else:
            self.quantity = _canonical_quantity(self.quantity)
            if not isinstance(self.unit, str) or not self.unit.strip():
                raise ValueError("quantity requires unit")
            self.unit = self.unit.strip().lower()
            if self.unit not in ALLOWED_UNITS:
                raise ValueError(f"unsupported unit: {self.unit}")

        if self.package_count is not None:
            if (
                isinstance(self.package_count, bool)
                or not isinstance(self.package_count, int)
                or not 1 <= self.package_count <= MAX_PACKAGE_COUNT
            ):
                raise ValueError(
                    f"package_count must be an integer from 1 to {MAX_PACKAGE_COUNT}"
                )

        if self.storage is not None:
            if not isinstance(self.storage, str):
                raise ValueError("storage must be a string or null")
            self.storage = self.storage.strip().lower()
            if self.storage not in ALLOWED_STORAGE:
                raise ValueError(f"unsupported storage: {self.storage}")

        if self.expires_on is not None:
            if not isinstance(self.expires_on, str):
                raise ValueError("expires_on must be an ISO date or null")
            if not _CALENDAR_DATE_RE.fullmatch(self.expires_on.strip()):
                raise ValueError("expires_on must use YYYY-MM-DD")
            try:
                self.expires_on = date.fromisoformat(self.expires_on.strip()).isoformat()
            except ValueError as exc:
                raise ValueError("expires_on must be a real ISO date") from exc

        if self.comment is not None:
            if not isinstance(self.comment, str):
                raise ValueError("comment must be a string or null")
            self.comment = self.comment.strip() or None
            if self.comment is not None and len(self.comment) > MAX_COMMENT_LEN:
                raise ValueError(f"comment too long (max {MAX_COMMENT_LEN} chars)")

        self.created_at = _canonical_timestamp(self.created_at, "created_at")
        self.updated_at = _canonical_timestamp(self.updated_at, "updated_at")
        if datetime.fromisoformat(self.updated_at) < datetime.fromisoformat(self.created_at):
            raise ValueError("updated_at cannot be earlier than created_at")

    def to_dict(self) -> dict:
        return asdict(self)

    def expiry_status(self, *, today: date | None = None) -> str:
        if self.expires_on is None:
            return "unknown"
        current = today or date.today()
        expiry = date.fromisoformat(self.expires_on)
        days = (expiry - current).days
        if days < 0:
            return "expired"
        if days <= EXPIRING_SOON_DAYS:
            return "expiring_soon"
        return "ok"

    def to_public_dict(self, *, today: date | None = None) -> dict:
        payload = self.to_dict()
        payload.pop("available")
        payload.pop("ever_stocked")
        payload.pop("stock_cycle")
        return payload | {"expiry_status": self.expiry_status(today=today)}

    @classmethod
    def from_dict(cls, data: dict) -> "InventoryItem":
        if not isinstance(data, dict):
            raise ValueError("inventory item must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"unknown inventory fields: {sorted(unknown)}")
        return cls(**data)
