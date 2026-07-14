# INV-4 — Product catalog and replenishment

**Status:** 📐 Ready
**Requested by:** Dmitrii
**Source:** Meal Planning Development, after a cooked-meal action removed essential ingredients from current inventory

## User problem

`register_cooked_meal` currently consumes essential ingredients by removing their inventory records. The Web UI then shows only what is currently present. A product that disappeared has no visible card or action, so the household must remember and type its exact name again to return it to stock.

The product needs a separate Web surface, **«Каталог продуктов»**, that represents known product identity independently from current availability.

## Product semantics

The catalog is the union of three user-visible states:

| State | Meaning | Primary action |
|---|---|---|
| `in_stock` | Product is currently present in the kitchen inventory | Open/edit current stock record |
| `out_of_stock` | Product was present at least once, but is absent now | `Восполнить` |
| `recipe_only` | Product is referenced by one or more recipes but has never been recorded in stock | `Добавить в запас` |

States are mutually exclusive for filtering. Recipe membership is an additional attribute: an in-stock or out-of-stock product may also be used in recipes.

Required filters:

- `Все`;
- `Сейчас в запасе`;
- `Нет в запасе`;
- `Только в рецептах`;
- text search by normalized product name.

## Important domain distinction

A **product identity** is not the same thing as a **current package/batch**.

The catalog may retain safe defaults or last-known values such as unit and usual storage zone. Replenishment must not silently reuse batch-specific data:

- old expiry date must start empty;
- old quantity/package count may be offered as an editable suggestion, not asserted as current fact;
- old free-text comment must not be copied automatically unless explicitly confirmed;
- replenishment creates/reactivates current availability only after the user confirms the form.

## Proposed persistence model

Prefer one atomic inventory envelope rather than a second independently written catalog file.

Evolve inventory storage to schema v3 so known stocked products survive consumption:

```json
{
  "schema_version": 3,
  "items": [
    {
      "id": "inv_...",
      "name": "репчатый лук",
      "available": false,
      "quantity": null,
      "unit": null,
      "package_count": null,
      "storage": "pantry",
      "expires_on": null,
      "comment": null,
      "created_at": "...",
      "updated_at": "..."
    }
  ]
}
```

Rules:

1. Existing v2 items migrate with `available: true`.
2. Cooking and ordinary stock removal mark a known item `available: false` rather than physically deleting it.
3. Legacy fridge projections and all existing availability-sensitive tools expose only `available: true` items.
4. Structured inventory CRUD keeps its current meaning: `/api/inventory/items` and native inventory tools operate on current stock only.
5. A catalog read combines all v3 inventory identities with recipe ingredient names.
6. A recipe ingredient with no inventory identity is returned as `recipe_only` without pretending it was ever purchased.
7. Replenishing an out-of-stock product reuses its stable product/inventory identity and makes it available after metadata confirmation.
8. Adding a recipe-only product to stock creates its first persistent inventory identity and changes it from `recipe_only` to `in_stock`.
9. Adding an already in-stock product must not create a duplicate; UI routes the user to edit the existing record.
10. Permanent catalog deletion is not part of the normal consumption flow. If later needed for typo cleanup, it must be a separate guarded action.

## Existing-data migration limit

The current system did not persist a complete audit log of removed inventory items. Therefore an exact list of every product ever stocked before INV-4 cannot always be reconstructed.

Migration can prove:

- every current v2 item was stocked and is currently available;
- every recipe ingredient is known to the recipe catalog.

Migration cannot prove that an ingredient absent now was previously purchased unless there is another trustworthy historical record. The UI must not mislabel all recipe ingredients as previously stocked. Previously consumed recipe ingredients will initially appear as `recipe_only` when no surviving stock identity exists; the first replenishment establishes `ever stocked` identity.

A one-time best-effort helper may surface essential ingredients from cooked dishes as replenishment suggestions, but it must label the inference and must not silently claim exact historical inventory.

## Web UX

Add sidebar entry and page:

```text
▦ Каталог продуктов
```

Each catalog row/card shows:

- product name;
- availability badge: `В запасе`, `Закончился`, or `Только в рецепте`;
- recipe usage count when non-zero;
- last-known safe defaults when available;
- action appropriate to state.

### Replenishment flow

For `out_of_stock`:

1. click `Восполнить`;
2. open an accessible form/modal prefilled with product name and safe defaults;
3. leave expiry empty;
4. confirm quantity, unit, package count, storage, expiry, and comment;
5. save;
6. product becomes visible in both current inventory and catalog as `В запасе`.

For `recipe_only`, the same form uses action label `Добавить в запас` and creates the first stock identity.

On success, update the two relevant caches without a full-page reload. On failure, preserve user-entered values and restore predictable focus.

## Native tool surface

Add read/replenishment tools rather than forcing Hermes to infer catalog state from recipes plus fridge:

- `list_product_catalog(status?, query?)`;
- `replenish_product(product_id or exact name, metadata...)`.

Tool descriptions must state that “replenish” means confirming presence in the kitchen inventory; it does not buy the product or add it to a shopping trip.

Existing tools remain compatible:

- `list_fridge` and `list_inventory_items` return current stock only;
- `add_inventory_item` reactivates a same-named out-of-stock identity instead of creating a duplicate;
- `remove_inventory_item`, legacy remove, `clear_fridge`, and cooking consumption transition identities to out-of-stock;
- recipe suggestion/shopping algorithms see only current stock.

## Acceptance criteria

### Domain and migration

- [ ] v2 → v3 migration preserves every current item, stable ID, metadata, and compatibility name projection.
- [ ] Every migrated v2 item is `available: true`.
- [ ] Consumption marks essential current products unavailable without deleting their identity.
- [ ] Manual remove and clear preserve catalog identity.
- [ ] Replenishment reuses the same stable ID for an out-of-stock product.
- [ ] Recipe-only products are distinguishable from products known to have been stocked.
- [ ] Expiry and comment are not silently copied into a new replenishment batch.
- [ ] Duplicate normalized product names remain impossible.
- [ ] Unsupported/corrupt schema continues to fail closed with sanitized Web errors.

### Compatibility

- [ ] Existing native and legacy inventory reads expose only current stock.
- [ ] Suggestions, quick shopping, weekly shopping, cooking, stats, and recipe availability ignore out-of-stock catalog records.
- [ ] Existing structured inventory API behavior remains backward compatible for current-stock consumers.
- [ ] Cooking/history transaction rollback restores the exact prior availability state on failure.

### Web

- [ ] Sidebar contains `Каталог продуктов` with desktop, collapsed, and mobile navigation support.
- [ ] Catalog can filter all, in-stock, out-of-stock, and recipe-only states.
- [ ] Text search is normalized and case-insensitive.
- [ ] `Восполнить` restores an out-of-stock product to current inventory.
- [ ] `Добавить в запас` promotes a recipe-only ingredient to current stock.
- [ ] In-stock action opens the existing stock editor and never duplicates the item.
- [ ] Success refreshes catalog/current-stock caches; failures preserve entered values.
- [ ] Names/comments are rendered XSS-safe.
- [ ] Keyboard, focus, touch target, mobile overflow, and console gates pass.

### Native tools and release

- [ ] `list_product_catalog` and `replenish_product` schemas are valid and auto-discovered.
- [ ] Unit and integration tests cover all three states and transitions.
- [ ] Cross-process tests cover Web replenish racing agent add/remove.
- [ ] Full unit/integration/Web/Chromium gate passes.
- [ ] Independent fail-closed review passes.
- [ ] Live migration and cook → out-of-stock → replenish QA complete without losing current household data.
- [ ] Completion report is sent to Meal Planning.

## Explicit non-goals for v1

- Exact quantity consumption by recipe; current cooking still consumes presence, not measured grams/pieces.
- Multiple simultaneous batches of the same product.
- Shopping-trip mutation from the catalog.
- Automatic purchasing.
- Product taxonomy, barcode database, brands as separate entities, or nutrition master data.
- Exact reconstruction of inventory items removed before this feature when no trustworthy history exists.
