# DATA-1 — Canonical domain models and shared Web/native persistence

**Status:** 📋 Backlog — design/refactor task; implementation not started
**Requested by:** Dima
**Source:** INV-3 synchronization research exposed divergent Web/native schemas and persistence paths
**Created:** 2026-07-14

## User problem

Meal Manager has grown through several vertical slices. Some domains now use a canonical model and repository from both Web and native tools, while others are read or written through separate Web-only JSON helpers.

This creates several classes of risk:

- Web and native code can interpret the same file using different schemas;
- validation, normalization, IDs, timestamps, and error handling can diverge;
- process-local locks do not prevent Web/native cross-process lost updates;
- one surface may silently treat malformed storage as empty while another fails closed;
- references based on mutable names or array indexes are unsafe for rename/delete;
- cross-domain operations such as cooking cannot provide one coherent transaction/snapshot contract;
- future synchronization work cannot truthfully report all changes while the underlying models disagree.

The goal is **not one giant generic JSON model**. The goal is one canonical domain model, repository contract, persistence schema, and application-service path per entity, reused by every surface.

## Confirmed current divergence

### Inventory and product catalog — reference implementation

Inventory is already closest to the target architecture:

- canonical `InventoryItem` with stable ID and structured metadata;
- versioned `fridge.json` schema;
- shared `JsonFridgeRepository` used by Web and native handlers;
- cross-process `flock`, locked read-modify-write, and atomic replace;
- active inventory and full product-catalog projections derived from one persisted identity;
- controlled Web/native validation and fail-closed corruption handling.

DATA-1 should preserve this behavior and use it as a pattern, not rewrite it merely for uniformity.

### Dishes

Native:

- uses `Dish` and `JsonDishRepository`;
- normalizes names/ingredients through the domain model;
- preserves malformed legacy rows during unrelated saves;
- protects only with process-local `threading.Lock`.

Web:

- reads/writes `dishes.json` directly through `_read_json`/`_write_json`;
- independently validates and normalizes payloads;
- uses a separate process-local `_lock`;
- identifies dishes by mutable normalized name in API paths;
- can bypass repository-specific malformed-row preservation and future repository invariants.

The persisted envelope currently looks similar on both paths, but the implementation and concurrency contracts are not shared.

### Cooking history — incompatible schema and semantics

Native `JsonHistoryRepository` stores:

```json
{
  "dish name": "2026-07-14"
}
```

This represents at most one/latest date per dish.

Web reads/writes:

```json
{
  "history": [
    {
      "dish": "dish name",
      "date": "2026-07-14T12:00:00"
    }
  ]
}
```

This represents multiple cooking events and deletes by array index.

These are incompatible models. A Web save can replace the representation expected by native code, and vice versa. Production `history.json` was `{}` when the discrepancy was discovered, so no current history data needs emergency recovery, but the divergence is a release blocker for any future all-domain synchronization claim.

### Weekly plans

- native code has `JsonPlanRepository` and plan-domain validation;
- Web currently exposes plans read-only but reads plan JSON through separate helper/validation logic;
- ISO week is a stable aggregate identity, but dish/prep references still need an explicit policy if dish identities change.

Even read-only duplicate parsing can drift. Web should consume the same repository/domain projection.

### Prep items

- native code has `JsonPrepItemRepository` and prep domain logic;
- there is currently no equivalent mutable Web surface;
- identity/reference strategy must still be included in the canonical model audit because plans can reference prep items.

### Tuning and derived data

- tuning has a repository but Web reads the JSON directly for stats;
- product catalog and shopping/stats are derived projections and should not create competing persistence models;
- DII sessions are operational state and need an explicit classification rather than accidental inclusion in household-domain migrations.

## Product outcome

After DATA-1:

1. every persisted domain has one canonical model/schema owner;
2. Web routes and native handlers call the same repository/application services;
3. mutable entities have stable identities and explicit entity/collection versions;
4. all cross-process read-modify-write operations use a shared locking contract;
5. corruption, migration, validation, conflict, and not-found behavior are consistent across surfaces;
6. cross-domain commands use an explicit transaction/rollback protocol;
7. derived views remain projections rather than secondary sources of truth;
8. no production JSON file is hand-read or hand-written by Web domain routes;
9. schema migrations are rehearsed, backed up, fail closed, and preserve exact household data;
10. INV-3 can build state vectors and synchronization on authoritative models instead of reconciling incompatible representations.

## Proposed canonical architecture

### 1. Domain models

Each mutable aggregate gets an explicit model with:

- stable ID where identity must survive rename/reorder;
- normalized display name separate from identity;
- typed fields and strict validation;
- `created_at`/`updated_at` or explicit entity version where concurrency needs it;
- deterministic public serializer;
- persistence serializer with schema-specific fields;
- migration/parser that distinguishes unsupported, corrupt, and legacy input;
- no use of mutable array position as identity.

Do not force fields onto domains that do not need them. Consistency means common guarantees and boundaries, not identical dataclasses.

### 2. Repository ownership

For each persisted domain, define one repository protocol and one configured production singleton/path:

```text
Web adapter ─┐
             ├─> application/domain service ─> repository ─> JSON storage
Native tool ─┘
```

Rules:

- Web may map domain exceptions to HTTP but must not duplicate persistence logic;
- native handlers may map domain exceptions to controlled tool errors but must not edit JSON directly;
- all paths use injectable data roots for isolated tests;
- repository reads/writes are the only persistence boundary;
- projections such as product catalog, stats, shopping, and current-stock names consume repositories/domain services.

### 3. Versioned persistence

Every mutable persisted file should have a documented versioned envelope unless a repository-specific format is explicitly justified.

Example shape:

```json
{
  "schema_version": 1,
  "items": []
}
```

The exact collection key may differ by domain. Requirements:

- strict schema discrimination;
- unknown fields/version handled according to documented compatibility rules;
- legacy migration only through tested repository code;
- first mutation may migrate only after a successful read/validation;
- unsupported/corrupt files remain byte-for-byte untouched;
- no surface silently converts corruption into an empty collection.

### 4. Stable identity strategy

Recommended direction:

- inventory/product: retain existing stable `inv_...` ID;
- dishes: introduce stable dish ID; name becomes mutable display/key material only where required for compatibility;
- history: introduce stable event ID for every cooking occurrence;
- prep items: audit whether stable ID is required before Web mutation/reference expansion;
- plans: ISO week remains stable aggregate identity; dish/prep references need migration from names to stable IDs or an explicit compatibility resolver;
- tuning: singleton aggregate with version/ETag, no artificial item ID.

Until stable IDs exist, collection ETags/revisions may protect a whole collection, but mutable names and array indexes must not be described as durable identity.

### 5. Canonical history decision

Preferred model for further design:

```json
{
  "schema_version": 1,
  "entries": [
    {
      "id": "cook_...",
      "dish_id": "dish_...",
      "dish_name_snapshot": "...",
      "cooked_at": "..."
    }
  ]
}
```

Rationale:

- preserves multiple cooking occurrences;
- supports deletion by stable event ID;
- recency and counts are derived without losing events;
- dish rename does not destroy historical display context;
- can link to stable dish ID while retaining a human-readable snapshot.

This is a design recommendation, not an implementation decision. Migration semantics for the current latest-date native representation must be explicitly approved.

### 6. Cross-process concurrency

Every mutable repository must provide:

- re-entrant in-process lock where nested domain services require it;
- cross-process `fcntl.flock` on a stable lock path;
- locked read-modify-write helpers;
- atomic replace for the final file;
- deterministic lock ordering for multi-domain operations;
- compare-and-swap/entity-version operations for stale edit/delete;
- cross-process tests using real separate processes.

A local `threading.Lock` in Web or repository code is not sufficient because `meal-web` and Hermes Gateway are separate processes.

### 7. Application services and transactions

Commands spanning domains must live above repositories and be shared by Web/native.

Examples:

- register cooked meal → append history event + consume inventory;
- delete dish → resolve policy for plans/history/prep references;
- rename dish → preserve stable identity and references;
- replenish product → update availability/fresh batch without stale metadata.

The service must define:

- lock ordering;
- before-state capture or durable transaction protocol;
- rollback/compare-and-swap behavior;
- crash recovery expectations;
- one semantic result used by both Web and native adapters.

DATA-1 should not claim database-grade multi-file atomicity unless it is actually implemented and crash-tested. If JSON transaction complexity becomes excessive, the design spike must compare SQLite/WAL rather than layering an unsafe sidecar transaction log.

### 8. Error contract

Shared domain/repository errors should map consistently:

| Condition | Web | Native |
|---|---|---|
| invalid input | controlled `400/422` | controlled error envelope |
| missing stable entity | `404` | controlled not-found error |
| stale entity/collection version | `409` | controlled conflict error |
| duplicate normalized identity | `409` | controlled conflict error |
| corrupt/unsupported storage | sanitized `503` | sanitized controlled storage error |

Internal paths, parser internals, raw malformed payloads, and comments must not leak.

## Migration and rollout constraints

- inventory schema v5, aliases, and stable IDs must be preserved exactly;
- create verified pre-migration backups for every touched production file;
- rehearse each migration against a temporary production copy;
- verify names, IDs, metadata, references, counts, and compatibility projections;
- rollback must restore all files participating in the migration, not one aggregate only;
- schema upgrades require coordinated non-rolling restart of `meal-web` and Hermes Gateway;
- old readers must not run after a new writer performs the first incompatible mutation;
- live QA records must be removed without deleting real household identities.

## Proposed sequencing

### Phase A — inventory of contracts and target schemas

- document every persisted file, current shape, readers, writers, IDs, versions, and references;
- decide canonical history semantics;
- decide dish/history/prep stable identity policy;
- decide whether JSON remains viable for cross-domain transactions;
- write migration/rollback plans before code.

### Phase B — history parity first

- introduce canonical cooking-history model/repository;
- migrate both Web and native operations;
- replace index-based Web delete with stable event ID;
- preserve recency/cooldown/count behavior;
- add cross-process locking and corruption tests.

History is first because its Web/native representations are currently incompatible.

### Phase C — dish identity and shared repository

- stable dish ID/version or explicitly approved collection-ETag transition;
- route Web CRUD through domain/repository/application service;
- preserve normalization and malformed-row safety;
- migrate references or provide tested compatibility resolution;
- add stale rename/delete conflict behavior.

### Phase D — read-path cleanup

- route Web plans, tuning, stats, and derived projections through canonical repositories/services;
- remove duplicate domain JSON parsers from Web;
- keep weekly plans read-only until their separate editing design is approved.

### Phase E — cross-domain command services

- unify cooking and any other multi-domain command;
- prove lock ordering, rollback, and crash behavior;
- expose identical semantic operations to Web and native adapters.

### Phase F — enable INV-3 all-domain synchronization

Only after authoritative models and coherent snapshots exist for every mutable Web domain.

## Acceptance criteria

### Canonical model ownership

- [ ] A checked-in matrix lists every persisted file, model, schema version, repository, Web routes, native tools, and references.
- [ ] Every mutable domain has one canonical model/repository owner.
- [ ] Web domain routes contain no direct JSON persistence for dishes, history, inventory, plans, prep, or tuning.
- [ ] Native handlers contain no direct JSON persistence.
- [ ] Derived views are read-only projections of canonical repositories.

### Identity and versioning

- [x] SHOP-1 explicitly supersedes schema v4 with rehearsed schema v5 aliases while preserving stable IDs.
- [ ] Dish rename/delete does not depend on mutable name identity without an explicit collection precondition.
- [ ] History entries are deleted by stable identity, not array index.
- [ ] Entity/collection versions make stale edits/deletes deterministic and non-destructive.
- [ ] Plan/prep/history references survive dish rename according to the approved reference policy.

### Concurrency and transactions

- [ ] Every mutable repository uses cross-process locked read-modify-write and atomic replace.
- [ ] Web/native different-field and same-field races have deterministic tests.
- [ ] Multi-domain services use deterministic lock ordering.
- [ ] Cooking cannot leave history and inventory silently divergent after controlled failure.
- [ ] Crash-consistency claims are backed by process-kill/restart tests, not only exception rollback tests.

### Compatibility and failure behavior

- [ ] Valid production data migrates without lost entities, metadata, history occurrences, or references.
- [ ] Corrupt/unsupported files fail closed and remain byte-for-byte untouched.
- [ ] Web/native expose equivalent validation, conflict, not-found, and storage-failure semantics.
- [ ] Existing inventory/catalog, suggestions, history cooldown, plans, prep, shopping, and stats regressions pass.
- [ ] Web remains XSS-safe and accessible after stable-ID/API changes.

### Release

- [ ] Unit, integration, Web API, Chromium, cross-process, migration, rollback, and crash gates pass.
- [ ] Independent fail-closed review passes.
- [ ] Coordinated Web/Gateway rollout is rehearsed and documented.
- [ ] Live Web/native parity QA covers every mutable domain and removes all QA artifacts.
- [ ] Completion report states exact migrations, guarantees, and remaining compatibility limitations.

## Dependencies and relationship to INV-3

- DATA-1 is a prerequisite for INV-3's **all-domain** synchronization phase.
- INV-3 inventory/catalog research may continue independently because inventory already has a shared canonical repository.
- INV-3 must not emit authoritative dish/history synchronization notices until DATA-1 establishes coherent models and cross-process persistence for those domains.

## Explicit non-goals

- Implementing synchronization notifications or Hermes hooks; that remains INV-3.
- Replacing JSON with SQLite without a measured transaction need and approved migration design.
- Making every domain use identical fields or one generic base class.
- Adding Web plan editing.
- Adding user authentication.
- Performing the refactor as an unreviewable big-bang rewrite.
