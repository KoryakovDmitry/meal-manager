"""Crash-safe receipt-ledger commands shared by native and future Web surfaces."""

from __future__ import annotations

from typing import Any

from .audit import audit_manager
from .audit.context import current_audit_context
from .receipt import (
    PurchaseReceipt,
    build_revision,
    new_receipt,
    revision_input,
)
from .repositories import fridge_repo, receipt_repo, shopping_request_repo


_RECEIPT_INPUT_FIELDS = {
    "merchant_name_raw", "merchant_name_normalized", "branch", "address",
    "purchased_at", "time_precision", "currency", "lines", "subtotal_cents",
    "total_cents", "evidence",
}
_LINK_ARGUMENTS = {
    "inventory_item_id": "inventory_item_ids",
    "product_id": "product_ids",
    "shopping_occurrence_id": "shopping_occurrence_ids",
}


def _validate_link_target(
    key: str,
    identifier: str,
    *,
    fridge_repository,
    shopping_repository,
) -> None:
    if key in {"inventory_item_id", "product_id"}:
        item = next(
            (
                candidate
                for candidate in fridge_repository.load_catalog_items()
                if candidate.id == identifier
            ),
            None,
        )
        if item is None:
            raise ValueError(f"{key} does not identify a known catalog item")
        if key == "product_id" and item.category != "product":
            raise ValueError("product_id must identify a product catalog item")
        return
    request = shopping_repository.get(identifier)
    if request is None:
        request = shopping_repository.get_completion(identifier)
    if request is None:
        raise ValueError(
            "shopping_occurrence_id does not identify a known shopping occurrence"
        )


class ReceiptDuplicateConflict(ValueError):
    """One evidence identity maps to conflicting receipt semantics."""


class ReceiptDuplicateCandidate(ValueError):
    """A similar receipt needs explicit duplicate confirmation."""


def _provenance(actor_type: str, surface_kind: str) -> dict[str, str]:
    active = current_audit_context()
    if active is not None:
        actor_type = active.get("actor", {}).get("type", actor_type)
        surface_kind = active.get("surface", {}).get("kind", surface_kind)
    return {"actor_type": actor_type, "surface_kind": surface_kind}


def _audit_context(actor_type: str, surface_kind: str, operation: str) -> dict[str, Any]:
    active = current_audit_context()
    if active is not None:
        surface = dict(active["surface"])
        surface.setdefault("operation", operation)
        return {
            "actor": dict(active["actor"]),
            "surface": surface,
            "correlation_id": active["correlation_id"],
        }
    return {
        "actor": {"type": actor_type},
        "surface": {"kind": surface_kind, "operation": operation},
    }


def _target_name(repository, manager) -> str:
    try:
        relative = repository.path.absolute().relative_to(manager.data_dir).as_posix()
    except ValueError as exc:
        raise ValueError("receipt repository must live inside the audit data root") from exc
    if relative != "receipts.json":
        raise ValueError("receipt commands require the canonical receipts.json target")
    return relative


def _load_receipts_locked(repository, manager) -> list[PurchaseReceipt]:
    target = _target_name(repository, manager)
    return repository.load_bytes_strict(manager.read_target(target))


def _commit_receipts(
    *,
    receipts: list[PurchaseReceipt],
    receipt: PurchaseReceipt,
    event_type: str,
    operation: str,
    event_payload: dict[str, Any],
    repository,
    manager,
    actor_type: str,
    surface_kind: str,
):
    target = _target_name(repository, manager)
    serialized = repository.serialize(receipts)
    events = [{
        "event_type": event_type,
        "entity": {"type": "purchase_receipt", "id": receipt.receipt_id},
        "payload": event_payload,
    }]
    context = _audit_context(actor_type, surface_kind, operation)
    try:
        return manager.commit(
            operation=operation,
            targets={target: serialized},
            events=events,
            context=context,
        )
    except Exception:
        resolved = manager.resolve_last_transaction()
        if resolved is None:
            raise
        return resolved


def _find_receipt(receipts: list[PurchaseReceipt], receipt_id: str) -> PurchaseReceipt:
    matches = [receipt for receipt in receipts if receipt.receipt_id == receipt_id]
    if len(matches) != 1:
        raise LookupError(f"purchase receipt '{receipt_id}' not found uniquely")
    return matches[0]


def load_receipts(
    *,
    repository=receipt_repo,
    manager=audit_manager,
) -> list[PurchaseReceipt]:
    """Recover pending audit work before exposing a read projection."""

    with manager.lock:
        manager.recover()
        with repository.mutation_lock(manager):
            return _load_receipts_locked(repository, manager)


def _validate_expected_revision_value(expected_revision: Any) -> None:
    if (
        not isinstance(expected_revision, int)
        or isinstance(expected_revision, bool)
        or expected_revision < 1
    ):
        raise ValueError("expected_revision must be a positive integer")


def _validate_expected_revision(receipt: PurchaseReceipt, expected_revision: Any) -> None:
    _validate_expected_revision_value(expected_revision)
    if receipt.current.revision != expected_revision:
        raise ValueError(
            f"stale receipt revision: expected {expected_revision}, "
            f"current is {receipt.current.revision}"
        )


def record_receipt(
    payload: dict[str, Any],
    *,
    status: str = "confirmed",
    allow_similar: bool = False,
    actor_type: str = "agent",
    surface_kind: str = "native_tool",
    repository=receipt_repo,
    manager=audit_manager,
) -> dict[str, Any]:
    if status not in {"confirmed", "needs_review"}:
        raise ValueError("new receipt status must be confirmed or needs_review")
    if not isinstance(allow_similar, bool):
        raise ValueError("allow_similar must be boolean")
    operation = "record_purchase_receipt"
    with manager.lock:
        manager.recover()
        with repository.mutation_lock(manager):
            receipts = _load_receipts_locked(repository, manager)
            revision = build_revision(
                payload,
                revision=1,
                requested_status=status,
                reason=None,
                provenance=_provenance(actor_type, surface_kind),
            )
            evidence_hash = revision.evidence.get("content_sha256")
            semantic_owner = None
            evidence_owner = None
            for existing in receipts:
                if any(
                    candidate.semantic_fingerprint == revision.semantic_fingerprint
                    for candidate in existing.revisions
                ):
                    semantic_owner = existing
                if evidence_hash is not None and any(
                    candidate.evidence.get("content_sha256") == evidence_hash
                    for candidate in existing.revisions
                ):
                    evidence_owner = existing

            if (
                semantic_owner is not None
                and evidence_owner is not None
                and semantic_owner.receipt_id != evidence_owner.receipt_id
            ):
                raise ReceiptDuplicateConflict(
                    "receipt semantic and evidence identities belong to different receipts"
                )
            if semantic_owner is not None:
                if evidence_hash is not None and evidence_owner is None:
                    raise ReceiptDuplicateConflict(
                        "receipt semantics already exist but the supplied evidence hash is new; "
                        "attach it through an explicit receipt correction"
                    )
                result = semantic_owner.to_public(include_revisions=False)
                result["idempotent"] = True
                return result
            if evidence_owner is not None:
                raise ReceiptDuplicateConflict(
                    "receipt evidence hash already exists with conflicting semantics: "
                    + evidence_owner.receipt_id
                )

            similar = [
                candidate.receipt_id
                for candidate in receipts
                if candidate.current.status != "retracted"
                and candidate.current.merchant_name_normalized
                == revision.merchant_name_normalized
                and candidate.current.purchased_on == revision.purchased_on
                and candidate.current.total_cents == revision.total_cents
            ]
            if similar and not allow_similar:
                raise ReceiptDuplicateCandidate(
                    "similar purchase receipt candidates require allow_similar=true: "
                    + ", ".join(similar)
                )

            receipt = new_receipt(revision)
            receipts.append(receipt)
            _commit_receipts(
                receipts=receipts,
                receipt=receipt,
                event_type="purchase_receipt.recorded.v1",
                operation=operation,
                event_payload={
                    "revision": 1,
                    "status": revision.status,
                    "merchant_name_raw": revision.merchant_name_raw,
                    "purchased_on": revision.purchased_on,
                    "currency": revision.currency,
                    "line_count": len(revision.lines),
                    "total_cents": revision.total_cents,
                    "semantic_fingerprint": revision.semantic_fingerprint,
                    "evidence_sha256": evidence_hash,
                },
                repository=repository,
                manager=manager,
                actor_type=actor_type,
                surface_kind=surface_kind,
            )
            result = receipt.to_public(include_revisions=False)
            result["idempotent"] = False
            result["duplicate_candidates"] = similar
            return result


def correct_receipt(
    receipt_id: str,
    *,
    expected_revision: int,
    reason: str,
    changes: dict[str, Any],
    actor_type: str = "agent",
    surface_kind: str = "native_tool",
    repository=receipt_repo,
    manager=audit_manager,
) -> dict[str, Any]:
    if not isinstance(changes, dict):
        raise ValueError("changes must be an object")
    unknown = set(changes) - _RECEIPT_INPUT_FIELDS
    if unknown:
        raise ValueError(f"receipt changes contain unsupported fields: {sorted(unknown)}")
    if not changes:
        raise ValueError("receipt changes cannot be empty")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("correction reason is required")
    operation = "correct_purchase_receipt"
    with manager.lock:
        manager.recover()
        with repository.mutation_lock(manager):
            receipts = _load_receipts_locked(repository, manager)
            receipt = _find_receipt(receipts, receipt_id)
            _validate_expected_revision(receipt, expected_revision)
            if receipt.current.status == "retracted":
                raise ValueError("retracted receipts cannot be corrected")
            if "lines" in changes:
                raw_lines = changes["lines"]
                if isinstance(raw_lines, list) and any(
                    isinstance(line, dict) and "links" in line
                    for line in raw_lines
                ):
                    raise ValueError(
                        "receipt links must be changed through the dedicated link operation"
                    )
            payload = revision_input(receipt.current)
            if "evidence" in changes:
                evidence = dict(payload["evidence"])
                replacement = changes["evidence"]
                if not isinstance(replacement, dict):
                    raise ValueError("changes.evidence must be an object")
                evidence.update(replacement)
                payload["evidence"] = evidence
                changes = {key: value for key, value in changes.items() if key != "evidence"}
            if (
                "merchant_name_raw" in changes
                and "merchant_name_normalized" not in changes
            ):
                changes = dict(changes)
                changes["merchant_name_normalized"] = None
            payload.update(changes)
            revision = build_revision(
                payload,
                revision=receipt.current.revision + 1,
                requested_status="corrected",
                reason=reason.strip(),
                provenance=_provenance(actor_type, surface_kind),
                previous=receipt.current,
            )
            previous_fingerprint = receipt.current.semantic_fingerprint
            receipt.revisions.append(revision)
            _commit_receipts(
                receipts=receipts,
                receipt=receipt,
                event_type="purchase_receipt.corrected.v1",
                operation=operation,
                event_payload={
                    "revision": revision.revision,
                    "previous_revision": expected_revision,
                    "status": revision.status,
                    "reason": reason.strip(),
                    "previous_semantic_fingerprint": previous_fingerprint,
                    "semantic_fingerprint": revision.semantic_fingerprint,
                    "total_cents": revision.total_cents,
                },
                repository=repository,
                manager=manager,
                actor_type=actor_type,
                surface_kind=surface_kind,
            )
            return receipt.to_public(include_revisions=False)


def link_receipt_line(
    receipt_id: str,
    receipt_line_id: str,
    *,
    expected_revision: int,
    action: str,
    inventory_item_id: str | None = None,
    product_id: str | None = None,
    shopping_occurrence_id: str | None = None,
    actor_type: str = "agent",
    surface_kind: str = "native_tool",
    repository=receipt_repo,
    manager=audit_manager,
    fridge_repository=fridge_repo,
    shopping_repository=shopping_request_repo,
) -> dict[str, Any]:
    if action not in {"link", "unlink"}:
        raise ValueError("action must be link or unlink")
    identifiers = {
        key: value
        for key, value in {
            "inventory_item_id": inventory_item_id,
            "product_id": product_id,
            "shopping_occurrence_id": shopping_occurrence_id,
        }.items()
        if value is not None
    }
    if len(identifiers) != 1:
        raise ValueError("provide exactly one analytical link identifier")
    key, identifier = next(iter(identifiers.items()))
    if not isinstance(identifier, str) or not identifier.strip() or len(identifier.strip()) > 200:
        raise ValueError(f"{key} must be a non-empty identifier")
    identifier = identifier.strip()
    operation = "link_purchase_receipt_line"
    with manager.lock:
        manager.recover()
        with repository.mutation_lock(manager):
            receipts = _load_receipts_locked(repository, manager)
            receipt = _find_receipt(receipts, receipt_id)
            _validate_expected_revision(receipt, expected_revision)
            if receipt.current.status == "retracted":
                raise ValueError("retracted receipts cannot be linked")
            payload = revision_input(receipt.current)
            target = next(
                (
                    line for line in payload["lines"]
                    if line.get("receipt_line_id") == receipt_line_id
                ),
                None,
            )
            if target is None:
                raise LookupError(f"receipt line '{receipt_line_id}' not found")
            link_field = _LINK_ARGUMENTS[key]
            current_values = target["links"][link_field]
            if action == "link":
                if identifier in current_values:
                    result = receipt.to_public(include_revisions=False)
                    result["idempotent"] = True
                    return result
                _validate_link_target(
                    key,
                    identifier,
                    fridge_repository=fridge_repository,
                    shopping_repository=shopping_repository,
                )
                current_values.append(identifier)
            else:
                if identifier not in current_values:
                    result = receipt.to_public(include_revisions=False)
                    result["idempotent"] = True
                    return result
                current_values.remove(identifier)
            revision = build_revision(
                payload,
                revision=receipt.current.revision + 1,
                requested_status=receipt.current.status,
                reason=f"analytical {action}: {key}",
                provenance=_provenance(actor_type, surface_kind),
                previous=receipt.current,
            )
            receipt.revisions.append(revision)
            event_suffix = "linked" if action == "link" else "unlinked"
            _commit_receipts(
                receipts=receipts,
                receipt=receipt,
                event_type=f"purchase_receipt.line_{event_suffix}.v1",
                operation=operation,
                event_payload={
                    "revision": revision.revision,
                    "previous_revision": expected_revision,
                    "receipt_line_id": receipt_line_id,
                    "link_type": key,
                    "link_id": identifier,
                },
                repository=repository,
                manager=manager,
                actor_type=actor_type,
                surface_kind=surface_kind,
            )
            return receipt.to_public(include_revisions=False)


def retract_receipt(
    receipt_id: str,
    *,
    expected_revision: int,
    reason: str,
    actor_type: str = "agent",
    surface_kind: str = "native_tool",
    repository=receipt_repo,
    manager=audit_manager,
) -> dict[str, Any]:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("retraction reason is required")
    _validate_expected_revision_value(expected_revision)
    operation = "retract_purchase_receipt"
    with manager.lock:
        manager.recover()
        with repository.mutation_lock(manager):
            receipts = _load_receipts_locked(repository, manager)
            receipt = _find_receipt(receipts, receipt_id)
            if receipt.current.status == "retracted":
                if (
                    reason.strip() == receipt.current.reason
                    and expected_revision in {
                        receipt.current.revision - 1,
                        receipt.current.revision,
                    }
                ):
                    result = receipt.to_public(include_revisions=False)
                    result["idempotent"] = True
                    return result
                _validate_expected_revision(receipt, expected_revision)
                raise ValueError("receipt is already retracted with a different reason")
            _validate_expected_revision(receipt, expected_revision)
            revision = build_revision(
                revision_input(receipt.current),
                revision=receipt.current.revision + 1,
                requested_status="retracted",
                reason=reason.strip(),
                provenance=_provenance(actor_type, surface_kind),
                previous=receipt.current,
            )
            receipt.revisions.append(revision)
            _commit_receipts(
                receipts=receipts,
                receipt=receipt,
                event_type="purchase_receipt.retracted.v1",
                operation=operation,
                event_payload={
                    "revision": revision.revision,
                    "previous_revision": expected_revision,
                    "reason": reason.strip(),
                    "semantic_fingerprint": revision.semantic_fingerprint,
                },
                repository=repository,
                manager=manager,
                actor_type=actor_type,
                surface_kind=surface_kind,
            )
            return receipt.to_public(include_revisions=False)
