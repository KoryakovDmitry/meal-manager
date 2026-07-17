"""Shared command for safely absorbing one duplicate product identity."""


def merge_product_identity(
    *,
    fridge_repo,
    shopping_request_repo,
    source_item_id: str,
    target_item_id: str,
    expected_source_updated_at: str,
    expected_target_updated_at: str,
) -> dict:
    """Merge an unavailable source into an active target under safe lock order."""
    with shopping_request_repo.lock:
        with fridge_repo.lock:
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
            conflicts = shopping_request_repo.identity_merge_conflicts(
                source.id, source_names
            )
            if conflicts:
                raise ValueError(
                    "source product identity has receipt references: "
                    + ", ".join(conflicts)
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
