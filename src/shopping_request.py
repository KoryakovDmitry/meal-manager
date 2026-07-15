"""Persistent user-requested shopping entry."""

from dataclasses import asdict, dataclass
from datetime import date, datetime
import re

from .dish import Dish
_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")


@dataclass
class ShoppingRequest:
    id: str
    week: str
    requested_name: str
    created_at: str
    updated_at: str
    pending_exact_name: str | None = None
    pending_at: str | None = None
    product_id: str | None = None
    exact_name: str | None = None
    completed_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.startswith(("shopreq_", "shop_")):
            raise ValueError("shopping request id must start with shopreq_ or shop_")
        match = _WEEK_RE.fullmatch(self.week) if isinstance(self.week, str) else None
        if match is None:
            raise ValueError("week must be an ISO week YYYY-Www")
        try:
            date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
        except ValueError as exc:
            raise ValueError("week must be a valid ISO week") from exc
        self.requested_name = Dish.normalize_ingredient(self.requested_name)
        if not self.requested_name or len(self.requested_name) > 200:
            raise ValueError("requested_name must contain 1-200 characters")
        created = datetime.fromisoformat(self.created_at)
        updated = datetime.fromisoformat(self.updated_at)
        if created.tzinfo is None or updated.tzinfo is None or updated < created:
            raise ValueError("shopping request timestamps must be timezone-aware and ordered")
        pending_values = (self.pending_exact_name, self.pending_at)
        if any(value is not None for value in pending_values):
            if not all(isinstance(value, str) and value for value in pending_values):
                raise ValueError("shopping receipt reservation fields must be all present")
            pending_at = self.pending_at
            pending_exact_name = self.pending_exact_name
            assert isinstance(pending_at, str) and isinstance(pending_exact_name, str)
            pending = datetime.fromisoformat(pending_at)
            if pending.tzinfo is None or pending < created or pending > updated:
                raise ValueError("pending_at must be timezone-aware and ordered")
            self.pending_exact_name = Dish.normalize_ingredient(pending_exact_name)
            if not self.pending_exact_name or len(self.pending_exact_name) > 200:
                raise ValueError("pending_exact_name must contain 1-200 characters")

        completion_values = (self.product_id, self.exact_name, self.completed_at)
        if any(value is not None for value in completion_values):
            if not all(isinstance(value, str) and value for value in completion_values):
                raise ValueError("shopping completion fields must be all present")
            completed_at = self.completed_at
            exact_name = self.exact_name
            assert isinstance(completed_at, str) and isinstance(exact_name, str)
            completed = datetime.fromisoformat(completed_at)
            if completed.tzinfo is None or completed < created or completed > updated:
                raise ValueError("completed_at must be timezone-aware and ordered")
            self.exact_name = Dish.normalize_ingredient(exact_name)
            if not self.exact_name or len(self.exact_name) > 200:
                raise ValueError("exact_name must contain 1-200 characters")
            if self.pending_exact_name is not None or self.pending_at is not None:
                raise ValueError("completed receipt cannot retain pending reservation fields")

    @property
    def is_active(self) -> bool:
        return self.completed_at is None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict):
        if not isinstance(value, dict):
            raise ValueError("shopping request must be an object")
        base = {"id", "week", "requested_name", "created_at", "updated_at"}
        completion = {"product_id", "exact_name", "completed_at"}
        reservation = {"pending_exact_name", "pending_at"}
        allowed = base | completion | reservation
        if set(value) not in {
            frozenset(base), frozenset(base | completion), frozenset(allowed)
        }:
            raise ValueError("shopping request fields are invalid")
        payload = dict(value)
        for field_name in (
            "pending_exact_name", "pending_at", "product_id", "exact_name", "completed_at"
        ):
            payload.setdefault(field_name, None)
        return cls(**payload)
