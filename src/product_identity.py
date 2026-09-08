"""Shared command for safely absorbing one duplicate product identity."""

from pathlib import Path


def _receipt_link_conflicts(*, source_item_id, receipt_repo, audit_manager) -> list[str]:
    if Path(receipt_repo.path).name != "receipts.json":
        raise ValueError("receipt merge guard requires canonical receipts.json")
    payload = audit_manager.read_target("receipts.json")
    receipts = receipt_repo.load_bytes_strict(payload)
    conflicts = set()
    for receipt in receipts:
        for revision in receipt.revisions:
            for line in revision.lines:
                if source_item_id in {
                    *line.links.get("inventory_item_ids", []),
                    *line.links.get("product_ids", []),
                }:
                    conflicts.add(
                        f"{receipt.receipt_id}:{line.receipt_line_id}:r{revision.revision}"
                    )
    return sorted(conflicts)


def merge_product_identity(
    *,
    fridge_repo,
    shopping_request_repo,
    receipt_repo=None,
    audit_manager=None,
    source_item_id: str,
    target_item_id: str,
    expected_source_updated_at: str,
    expected_target_updated_at: str,
) -> dict:
    """Merge an unavailable source into an active target under safe lock order."""
    if receipt_repo is None:
        from .repositories.json_receipt import JsonReceiptRepository

        receipt_repo = JsonReceiptRepository(Path(fridge_repo.path).with_name("receipts.json"))
    if audit_manager is None:
        from .audit.transaction import AuditTransactionManager

        audit_manager = AuditTransactionManager(Path(fridge_repo.path).parent)
    with audit_manager.lock:
        audit_manager.recover()
        with shopping_request_repo.lock:
            with fridge_repo.lock:
                return _merge_product_identity_locked(
                    fridge_repo=fridge_repo,
                    shopping_request_repo=shopping_request_repo,
                    receipt_repo=receipt_repo,
                    audit_manager=audit_manager,
                    source_item_id=source_item_id,
                    target_item_id=target_item_id,
                    expected_source_updated_at=expected_source_updated_at,
                    expected_target_updated_at=expected_target_updated_at,
                )


def _merge_product_identity_locked(
    *,
    fridge_repo,
    shopping_request_repo,
    receipt_repo,
    audit_manager,
    source_item_id: str,
    target_item_id: str,
    expected_source_updated_at: str,
    expected_target_updated_at: str,
) -> dict:
    items = fridge_repo.load_catalog_items()
    source_id = source_item_id.strip() if isinstance(source_item_id, str) else source_item_id
    target_id = target_item_id.strip() if isinstance(target_item_id, str) else target_item_id
    source = next((item for item in items if item.id == source_id), None)
    target = next((item for item in items if item.id == target_id), None)
    if source is None:
        raise LookupError(f"Source product identity '{source_id}' not found")
    if target is None:
        raise LookupError(f"Target product identity '{target_id}' not found")

    source_names = {source.name, *source.aliases}
    conflicts = shopping_request_repo.identity_merge_conflicts(source.id, source_names)
    if conflicts:
        raise ValueError(
            "source product identity has shopping request references: "
            + ", ".join(conflicts)
        )
    receipt_conflicts = _receipt_link_conflicts(
        source_item_id=source.id,
        receipt_repo=receipt_repo,
        audit_manager=audit_manager,
    )
    if receipt_conflicts:
        raise ValueError(
            "source product identity has purchase receipt references: "
            + ", ".join(receipt_conflicts)
        )
    merged, transferred = fridge_repo.merge_product_identity(
        source.id,
        target.id,
        expected_source_updated_at=expected_source_updated_at,
        expected_target_updated_at=expected_target_updated_at,
    )
    return {
        "item": merged.to_public_dict(),
        "merged_from": source.id,
        "transferred_aliases": transferred,
    }
