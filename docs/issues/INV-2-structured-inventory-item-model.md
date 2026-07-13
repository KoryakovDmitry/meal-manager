# INV-2 — Structured kitchen inventory item model

**Status:** 📐 Ready
**Requested by:** Dmitrii Koriakov
**Source:** Meal Planning Development, immediately after INV-1 rename/edit intake

## User problem

The current kitchen inventory stores only a flat list of normalized strings. This is sufficient to answer “is this ingredient present?”, but it loses the information Dima actually supplies while inventorying the kitchen:

- amount or weight (`2 кг` chicken);
- count (`8 pieces`);
- number of packages;
- measurement unit;
- storage location;
- expiry/best-before date;
- free-form comments and product details.

The missing structure forces the planning agent to keep those facts only in conversational context or a temporary preliminary sheet. They are not durable, cannot be edited reliably, and are invisible in the web interface.

## Goal

Replace the flat string list with a backward-compatible, versioned `InventoryItem` model that represents one household stock slot per normalized product name. Expose the structured fields through native `meal_manager` tools and Dima’s web UI while preserving existing recipe/suggestion behavior.

## Phase-1 product decisions

### One slot per normalized product name

INV-2 keeps the current uniqueness invariant: one ordinary inventory slot per normalized `name`. It does **not** model separate purchase lots of the same product. If two packages have different expiry dates, phase 1 stores the nearest relevant date and allows the distinction to be noted in `comment`.

This keeps the small-household workflow understandable and preserves unambiguous name-based compatibility tools. Multi-lot inventory is a future extension, not an implicit part of this migration.

### Structured but nullable metadata

Only `id` and `name` are required. Every migrated legacy item remains valid with unknown metadata. The user can enrich records incrementally rather than being forced to specify every field.

### Quantities do not yet drive recipes or shopping arithmetic

Recipes still express ingredient presence rather than required grams/units. INV-2 persists and displays quantities, but existing cookability and shopping aggregation continue using the derived set of names until the separate recipe-quantity contract is implemented.

The system must not pretend that `2 кг` satisfies a calculated recipe demand when recipes have no quantity requirement.

## Persisted schema

`data/fridge.json` becomes a versioned envelope:

```json
{
  "schema_version": 2,
  "items": [
    {
      "id": "inv_01J...",
      "name": "куриные голени",
      "quantity": "2",
      "unit": "kg",
      "package_count": 1,
      "storage": "fridge",
      "expires_on": "2026-07-17",
      "comment": "сырые; приготовить или заморозить",
      "created_at": "2026-07-14T01:15:00+02:00",
      "updated_at": "2026-07-14T01:15:00+02:00"
    }
  ]
}
```

## Field contract

| Field | Type | Required | Contract |
|---|---|---:|---|
| `id` | string | yes | Stable opaque identifier, generated once and unchanged by rename |
| `name` | string | yes | Existing normalized ingredient/product name; unique across slots |
| `quantity` | canonical decimal string or `null` | no | Positive finite amount, persisted as a decimal string to avoid float drift |
| `unit` | enum or `null` | no | Required when `quantity` is present; absent when amount is unknown |
| `package_count` | positive integer or `null` | no | Physical package/container count; independent from quantity |
| `storage` | enum or `null` | no | `fridge`, `freezer`, `pantry`, or `counter`; null means unspecified |
| `expires_on` | ISO date or `null` | no | User-entered nearest relevant expiry/best-before date |
| `comment` | string or `null` | no | Trimmed free text with an explicit length limit; never interpreted as instructions |
| `created_at` | ISO datetime | yes | Record creation/migration timestamp |
| `updated_at` | ISO datetime | yes | Updated only after a successful persisted mutation |

Initial unit vocabulary:

- mass: `g`, `kg`;
- volume: `ml`, `l`;
- count: `pcs`;
- packaging: `pack`, `can`, `jar`, `bottle`;
- household planning: `portion`.

Internal codes remain language-neutral. The web UI and agent responses render localized labels such as `кг`, `шт.`, `уп.`.

## Validation and normalization

1. `name` uses the existing ingredient normalization and length limits.
2. `quantity` accepts a tool/API JSON number or decimal string at the boundary, converts through `Decimal(str(value))`, rejects booleans, zero, negatives, non-finite values, excessive precision, and unsafe magnitude, then persists a canonical decimal string.
3. `unit` must be from the allowed vocabulary and is invalid without `quantity`.
4. `quantity` without `unit` is invalid.
5. `package_count` must be a bounded positive integer; booleans are invalid.
6. `expires_on` must be a real ISO calendar date. Past dates are allowed but explicitly surfaced as expired; they are not silently deleted.
7. `comment` is trimmed, bounded, and may be cleared explicitly with `null`.
8. Unknown fields are rejected at persistence and API boundaries.
9. Duplicate normalized names are rejected; no silent merge.
10. All mutations are atomic and preserve unrelated records.

## Migration contract

### Reading legacy data

Legacy files such as:

```json
["куриные голени", "barilla penne rigate №73"]
```

remain readable. Each valid unique string is projected as an `InventoryItem` with:

- stable deterministic migration ID derived from the normalized name;
- `quantity`, `unit`, `package_count`, `storage`, `expires_on`, and `comment` set to `null`;
- migration timestamps assigned when the v2 envelope is first persisted.

### Writing v2

- The first successful inventory mutation writes the complete v2 envelope atomically.
- Migration is all-or-nothing; no partial conversion.
- Malformed legacy entries are reported and preserved/fail closed according to the existing data-integrity policy rather than silently discarded during a write.
- A pre-migration backup/rollback test must prove the legacy file is not destroyed on validation or write failure.
- After migration, readers reject unsupported future `schema_version` values rather than guessing.

## Derived compatibility views

The structured repository exposes two projections:

1. **Detailed items** for inventory management: full `InventoryItem` records.
2. **Available names** for existing recipes/suggestions/shopping: deduplicated set/list of `item.name` where the item exists.

Existing behavior remains stable:

- `list_fridge` returns the familiar name list;
- `get_meal_suggestions` and `get_quick_shopping_list` consume available names;
- `register_cooked_meal` retains its current presence-based consumption semantics until recipe quantities exist;
- `generate_shopping_list` continues its cooking-occurrence basis and must explicitly state that structured stock quantity is not yet deducted arithmetically.

No consumer may infer quantitative sufficiency merely because `quantity` is present.

## Native tool surface

Add structured inventory tools:

### `list_inventory_items`

Returns complete records in a stable display order, including expiry status derived at read time (`unknown`, `ok`, `expiring_soon`, `expired`) without mutating persistence.

### `add_inventory_item`

Arguments:

- `name` required;
- `quantity`, `unit`, `package_count`, `storage`, `expires_on`, `comment` optional.

Rejects duplicate normalized names. Returns the persisted record.

### `edit_inventory_item`

Arguments:

- `item_id` required;
- patch fields: `name`, `quantity`, `unit`, `package_count`, `storage`, `expires_on`, `comment`;
- at least one patch field required.

Supports explicit clearing of nullable fields. Preserves `id` and `created_at`; updates `updated_at` only on a real change. Name editing uses INV-1 collision/no-op semantics.

### `remove_inventory_item`

Removes exactly one record by stable `item_id`, with a clear not-found error.

### Compatibility tools

- `update_fridge_inventory(action="add")` creates a metadata-empty record when the normalized name is absent.
- `update_fridge_inventory(action="remove")` removes the unique slot by normalized name.
- `rename_fridge_item` from INV-1 delegates to the same domain edit operation and remains a convenient conversational tool.
- `clear_fridge` removes all structured items.

The agent continues using native tools only; it never edits JSON directly or drives the web UI.

## Web UI

The **Холодильник** page evolves from plain name chips into compact structured inventory cards/chips:

- primary label: product name;
- secondary metadata: quantity + localized unit, package count, storage zone, expiry state;
- optional comment shown without overwhelming the compact view;
- edit action opens an accessible form for all structured fields;
- delete remains distinct and confirms the specific item;
- unknown fields are shown as absent/“не указано”, not fabricated defaults;
- expiring/expired styling uses both text/icon and color, never color alone;
- no automatic deletion when expired;
- filters/sorting by storage and expiry may be added only if they fit without blocking the core CRUD flow.

All user strings are inserted as text, not executable HTML/inline handlers. Desktop/mobile behavior, keyboard flow, focus restoration, 44×44 targets, contrast, and horizontal-overflow checks remain release gates.

## Web API

Introduce item-oriented endpoints backed by the same validation semantics:

```text
GET    /api/inventory/items
POST   /api/inventory/items
PATCH  /api/inventory/items/{item_id}
DELETE /api/inventory/items/{item_id}
```

Existing `/api/fridge` read/add/remove routes remain compatibility surfaces during migration. New UI code uses item-oriented endpoints and never replaces the entire inventory envelope merely to edit one record.

Responses expose exact persisted fields plus derived expiry status. Unsupported fields, invalid IDs, duplicate names, malformed decimals, and bad dates return explicit 4xx errors without mutation.

## Expiry behavior

- `expired`: `expires_on < today`;
- `expiring_soon`: configurable later, initially a documented constant (default proposal: within 3 days);
- `ok`: future date outside the threshold;
- `unknown`: no date.

Expiry is advisory. The system warns and prioritizes visibility but never removes, consumes, or marks food discarded without an explicit user action.

Notifications/reminders are a separate feature; INV-2 only provides trustworthy data and on-demand display.

## Acceptance criteria

### Model and migration

- [ ] Legacy flat-list inventory loads without data loss.
- [ ] First successful mutation atomically persists `schema_version: 2` and complete items.
- [ ] Stable IDs survive rename, metadata edits, process restart, and web/native round trips.
- [ ] Missing metadata remains null/unknown; no invented quantity, location, or expiry.
- [ ] Duplicate names, malformed records, unsupported versions, invalid dates, bad decimals, and unknown fields fail closed.
- [ ] Migration/write failure leaves the original inventory recoverable and unchanged.

### Native operations

- [ ] Agent can add, list, edit, and remove a structured item through native tools.
- [ ] Reverse reads prove `2 kg` chicken, package counts, expiry, storage, and comments persist.
- [ ] Explicit field clearing works and does not clear unrelated metadata.
- [ ] Existing `list_fridge`, suggestions, cooking, and weekly shopping remain backward-compatible.
- [ ] Model-visible schemas expose every argument correctly through real plugin registration.

### Web

- [ ] Structured fields render for known data and remain unobtrusive when unknown.
- [ ] Dima can edit all supported fields from the web UI and see native reverse-read parity.
- [ ] Quantity/unit dependency, duplicate-name conflicts, dates, and comments are validated consistently.
- [ ] Expired and expiring-soon states are accessible and never trigger automatic deletion.
- [ ] User content remains XSS-safe across names and comments.
- [ ] Desktop/mobile keyboard, focus, touch-target, contrast, overflow, and console checks pass.

### Quality and release

- [ ] Strict RED → GREEN slices cover model, migration, native CRUD, compatibility projections, web API, and UI.
- [ ] Full unit/integration/web/browser gates pass with Chromium required.
- [ ] Persistence corruption and concurrent unrelated-update regression probes pass.
- [ ] Independent fail-closed review passes on the frozen final snapshot.
- [ ] Documentation, manifest, skills, tool counts, architecture, and board are synchronized.
- [ ] Commit/push, service refresh, live native CRUD, and live web read/edit verification complete.
- [ ] Meal Planning receives a completion report explaining what quantity does and does not affect.

## Dependencies and sequencing

- TOOLS-1 function-schema envelope fix: completed.
- INV-1 rename/edit UX contract: compatible and should reuse INV-2’s domain mutation once both exist.
- Recommended implementation order: domain model + migration → compatibility projections → native structured tools → web API → web UI → integrate INV-1 rename affordance.

## Non-goals

- Multiple lots/batches for the same normalized product name.
- Automatic unit conversion or aggregation across `kg`/`g`, `l`/`ml`, etc.
- Quantity-aware recipe requirements or automatic quantitative shopping subtraction.
- Barcode scanning, receipt OCR, price history, nutrition, or calorie tracking.
- Automatic expiry deletion, consumption, discard, or reminders.
- Treating comments as agent instructions.
- Agent-driven web editing or direct JSON writes.
