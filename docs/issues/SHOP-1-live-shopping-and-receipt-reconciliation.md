# SHOP-1 — Live shopping and receipt reconciliation

## Problem

The Web Shopping tab previously read quick-shopping suggestions instead of the current weekly plan. Persisted plan shopping could also become stale after changes to plans, recipes, prep, or inventory. Browser checkmarks were at risk of being treated as inventory evidence, while generic requests such as `молоко` had no safe path to one exact purchased product.

## Shipped contract

### Sources of truth

- Weekly plan, recipes, prep definitions/state, inventory/catalog identities, and active manual shopping requests are authoritative.
- `WeekPlan.shopping` is a validated budget/trip snapshot, not an unconditional current-list source.
- Web checkbox state is browser-local under `meal-shopping-checked` and never calls a mutation API.

### Projection

Web and native readers share `src/shopping.py` and recompute the current projection. Derived rows use deterministic, occurrence-scoped `shop_*` IDs; occurrence components are hashed as a canonical JSON array so valid names/IDs containing delimiters cannot alias another row. Manual requests use persisted `shopreq_*` IDs. An untracked abstract ingredient keeps its legacy week/name ID. Once an inventory identity exists, the ID also includes that identity's current missing-state version, so reloads remain stable while a later «купил → съел → снова нужно» cycle gets a fresh receipt identity instead of colliding with the prior tombstone. Rows are classified as:

- `known_missing`: canonical name or alias resolves to a known inventory identity;
- `abstract_request`: no known identity exists yet.

Corrupt recipe/prep/manual dependencies produce an explicit projection error and an empty current item list. Stale persisted items are never presented as current. Cost and trip mutations reject snapshots that no longer match the live projection.

### Inventory aliases

Inventory schema v6 preserves normalized aliases and adds an internal monotonic `stock_cycle`. Canonical names and aliases share one globally unique identity namespace. Generic recipe ingredients remain generic, while one exact purchased product stores the generic name as an alias. The cycle advances only on available→unavailable, never for metadata edits, and scopes derived receipt IDs to a genuine missing-stock occurrence. Availability, product catalog usage, legacy add/remove, prep production, cooking consumption, and shopping subtraction all resolve aliases back to the exact stable identity.

Legacy strings and schemas v2–v5 remain readable and migrate atomically on first mutation.

### Manual request and receipt lifecycle

`add_manual_shopping_item` creates an active request without changing inventory. Duplicate normalized week/name requests are idempotent while active.

`receive_shopping_item` accepts a stable shopping ID and exact product metadata. Manual reconciliation is serialized under the cross-process shopping-request lock:

1. read active request, pending reservation, or completion tombstone;
2. durably reserve the normalized exact-name winner before inventory mutation;
3. reject any concurrent or post-crash retry naming a different exact product;
4. refine/replenish one exact inventory identity and preserve the generic alias;
5. durably write inventory;
6. persist a completion tombstone, making the request inactive;
7. refresh the plan shopping snapshot when possible.

Inventory failure leaves the request active with its exact-name reservation. A failure after inventory success leaves a recoverable reminder: only a matching retry may resume and complete it, while a conflicting exact name is rejected before any inventory write. A lost successful response for the same shopping occurrence replays as `already_received` without another inventory write. After the product becomes unavailable and the need reappears, projection emits a new occurrence-scoped `shop_*` ID so a new physical purchase is accepted. Concurrent conflicting receipts within one occurrence have one durable winner.

## Persistence

- `data/fridge.json`: schema v6 inventory/catalog envelope with internal stock cycles.
- `data/shopping_requests.json`: schema v2 active requests, pending exact-name reservations, and completed tombstones; schema v1 remains readable.
- `data/plans/<week>.json`: validated shopping/budget snapshot.

Every mutation uses atomic replacement; inventory and shopping requests use cross-process `JsonFileLock` instances.

## Verification

Required release gate:

- unit and integration scripts;
- Web plan/API tests;
- Chromium accessibility/XSS/local-checkbox tests;
- compileall and `git diff --check`;
- cross-thread conflicting receipt regression;
- schema v5→v6 migration, stock-cycle transition, and alias collision regressions;
- independent fail-closed review;
- locked production backup with SHA-256 manifest;
- coordinated Hermes + `meal-web` restart and live QA.
