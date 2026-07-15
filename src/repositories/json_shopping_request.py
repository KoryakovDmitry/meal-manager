"""JSON repository for persistent manual shopping requests."""

import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from .. import atomic_write_json
from ..dish import Dish
from ..shopping_request import ShoppingRequest
from .file_lock import JsonFileLock


class ShoppingRequestDataError(ValueError):
    pass


class JsonShoppingRequestRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = JsonFileLock(lambda: self.path)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _load_all(self) -> list[ShoppingRequest]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ShoppingRequestDataError(f"Invalid shopping request file: {exc}") from exc
        if not isinstance(raw, dict) or raw.get("schema_version") not in {1, 2}:
            raise ShoppingRequestDataError("Unsupported shopping request schema")
        values = raw.get("requests")
        if not isinstance(values, list):
            raise ShoppingRequestDataError("Shopping requests must be a list")
        try:
            requests = [ShoppingRequest.from_dict(value) for value in values]
        except (TypeError, ValueError) as exc:
            raise ShoppingRequestDataError(f"Invalid shopping request: {exc}") from exc
        ids = [request.id for request in requests]
        if len(ids) != len(set(ids)):
            raise ShoppingRequestDataError("Duplicate shopping request id")
        return requests

    def load(self, *, week: str | None = None) -> list[ShoppingRequest]:
        return [
            request for request in self._load_all()
            if request.is_active and (week is None or request.week == week)
        ]

    def _save_unlocked(self, requests: list[ShoppingRequest]) -> None:
        ids = [request.id for request in requests]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate shopping request id")
        atomic_write_json(self.path, {
            "schema_version": 2,
            "requests": [request.to_dict() for request in requests],
        })

    def add(self, *, week: str, requested_name: str) -> ShoppingRequest:
        normalized_name = Dish.normalize_ingredient(requested_name)
        with self.lock:
            requests = self._load_all()
            existing = next((
                request for request in requests
                if request.is_active
                and request.id.startswith("shopreq_")
                and request.week == week
                and request.requested_name == normalized_name
            ), None)
            if existing is not None:
                return existing
            now = self._now()
            request = ShoppingRequest(
                id="shopreq_" + uuid.uuid4().hex,
                week=week,
                requested_name=normalized_name,
                created_at=now,
                updated_at=now,
            )
            requests.append(request)
            self._save_unlocked(requests)
            return request

    def get(self, request_id: str) -> ShoppingRequest | None:
        return next((request for request in self.load() if request.id == request_id), None)

    def get_completion(self, request_id: str) -> ShoppingRequest | None:
        return next((
            request for request in self._load_all()
            if request.id == request_id and not request.is_active
        ), None)

    def reserve_receipt(
        self,
        request_id: str,
        *,
        week: str,
        requested_name: str,
        exact_name: str,
    ) -> ShoppingRequest:
        """Durably bind one shopping identity to an exact receipt winner."""
        normalized_requested = Dish.normalize_ingredient(requested_name)
        normalized_exact = Dish.normalize_ingredient(exact_name)
        with self.lock:
            requests = self._load_all()
            target = next((request for request in requests if request.id == request_id), None)
            if target is None:
                now = self._now()
                target = ShoppingRequest(
                    id=request_id,
                    week=week,
                    requested_name=normalized_requested,
                    created_at=now,
                    updated_at=now,
                    pending_exact_name=normalized_exact,
                    pending_at=now,
                )
                requests.append(target)
                self._save_unlocked(requests)
                return target
            if target.week != week or target.requested_name != normalized_requested:
                raise ValueError("shopping receipt reservation conflicts with request identity")
            winner = target.exact_name if not target.is_active else target.pending_exact_name
            if winner is not None and winner != normalized_exact:
                raise ValueError("shopping receipt conflicts with reserved exact product")
            if not target.is_active or target.pending_exact_name is not None:
                return target
            now = self._now()
            reserved = replace(
                target,
                pending_exact_name=normalized_exact,
                pending_at=now,
                updated_at=now,
            )
            requests[requests.index(target)] = reserved
            self._save_unlocked(requests)
            return reserved

    def complete(
        self,
        request_id: str,
        *,
        product_id: str,
        exact_name: str,
    ) -> ShoppingRequest | None:
        with self.lock:
            requests = self._load_all()
            target = next((request for request in requests if request.id == request_id), None)
            if target is None:
                return None
            if not target.is_active:
                return target
            normalized_exact = Dish.normalize_ingredient(exact_name)
            if (
                target.pending_exact_name is not None
                and target.pending_exact_name != normalized_exact
            ):
                raise ValueError("shopping completion conflicts with receipt reservation")
            now = self._now()
            completed = replace(
                target,
                product_id=product_id,
                exact_name=normalized_exact,
                completed_at=now,
                pending_exact_name=None,
                pending_at=None,
                updated_at=now,
            )
            requests[requests.index(target)] = completed
            self._save_unlocked(requests)
            return completed

    def remove(self, request_id: str) -> ShoppingRequest | None:
        with self.lock:
            requests = self._load_all()
            target = next((request for request in requests if request.id == request_id), None)
            if target is None:
                return None
            self._save_unlocked([request for request in requests if request.id != request_id])
            return target
