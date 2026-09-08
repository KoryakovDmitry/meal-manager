"""Fail-closed JSON repository for append-preserving purchase receipts."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from .. import atomic_write_json
from ..receipt import (
    PurchaseReceipt,
    RECEIPT_SCHEMA_VERSION,
    validate_receipt_collection,
)
from .file_lock import JsonFileLock


class ReceiptDataError(ValueError):
    """Canonical receipt storage is unreadable or violates its schema."""


class JsonReceiptRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = JsonFileLock(lambda: self.path)

    @staticmethod
    def mutation_lock(manager):
        """Use the audit manager's descriptor-pinned lock for public commands."""

        return manager.lock

    def _load_raw(self):
        try:
            info = os.stat(self.path, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError("receipt storage must be a regular file")
        descriptor = os.open(
            self.path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                return json.load(handle)
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _validate_raw(raw) -> list[PurchaseReceipt]:
        if raw is None:
            return []
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "receipts"}:
            raise ValueError("receipt ledger fields do not match schema v1")
        version = raw.get("schema_version")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version != RECEIPT_SCHEMA_VERSION
        ):
            raise ValueError(f"unsupported receipt schema_version {version!r}")
        rows = raw.get("receipts")
        if not isinstance(rows, list):
            raise ValueError("receipt ledger receipts must be a list")
        receipts = [PurchaseReceipt.from_dict(item) for item in rows]
        validate_receipt_collection(receipts)
        return receipts

    def load_strict(self) -> list[PurchaseReceipt]:
        try:
            return self._validate_raw(self._load_raw())
        except ReceiptDataError:
            raise
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise ReceiptDataError("purchase receipt storage is corrupt") from exc

    def load_bytes_strict(self, payload: bytes | None) -> list[PurchaseReceipt]:
        try:
            raw = None if payload is None else json.loads(payload.decode("utf-8"))
            return self._validate_raw(raw)
        except ReceiptDataError:
            raise
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as exc:
            raise ReceiptDataError("purchase receipt storage is corrupt") from exc

    def serialize(self, receipts: list[PurchaseReceipt]) -> bytes:
        validate_receipt_collection(receipts)
        payload = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "receipts": [receipt.to_dict() for receipt in receipts],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    def save(self, receipts: list[PurchaseReceipt]) -> None:
        validate_receipt_collection(receipts)
        atomic_write_json(
            self.path,
            {
                "schema_version": RECEIPT_SCHEMA_VERSION,
                "receipts": [receipt.to_dict() for receipt in receipts],
            },
        )
