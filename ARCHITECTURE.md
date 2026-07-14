# Meal Manager — Architecture: Planned Extensions

> Status: **PHASES 1–3 IMPLEMENTED** — prep items, weekly plans, and
> shopping/budget flows are live. Phases 4–5 remain planned. This document is
> the source of truth for the agreed entities, data models, and tool surface.

---

## Context

The plugin currently manages a static recipe catalog, fridge inventory,
cooking history, and a self-tuning suggestion engine. The household needs
**weekly meal planning** with prep-day strategy, budget tracking, and
leftover management. This document specifies the extensions.

---

## New Entities

### 1. Prep Item

A **semi-finished product** made on prep-day (Sunday). Not a dish — an
intermediate ingredient that other dishes depend on.

```json
{
  "hybrid-meatballs": {
    "ingredients": {
      "говядина": true,
      "чечевица": true,
      "лук": true,
      "яйцо": true
    },
    "yield": 40,
    "yield_unit": "шт",
    "storage": "freezer"
  }
}
```

**Lifecycle:**
1. `add_prep_item(...)` defines the prep item; inventory remains unchanged and
   `remaining` starts at 0.
2. `make_prep("hybrid-meatballs")` consumes essential source ingredients from
   the fridge and sets `remaining` to the configured yield.
3. When a dish with `prep_depends: ["hybrid-meatballs"]` is cooked,
   the prep-item quantity is decremented.

**File:** `data/prep_items.json`

### 2. Weekly Plan

A flexible meal plan for one ISO week. No fixed meal slots (no
breakfast/lunch/dinner binding) — each day is a list of dish references
with portion counts.

```json
{
  "week": "2026-W03",
  "status": "draft",
  "prep": ["hybrid-meatballs", "lentil-bolognese-sauce"],
  "days": {
    "mon": {
      "meals": [
        {"dish": "суп с фрикадельками", "portions": 4},
        {"dish": "йогурт с вареньем", "portions": 2}
      ]
    },
    "tue": {
      "meals": [
        {"dish": "суп с фрикадельками", "portions": 2}
      ],
      "note": "leftovers"
    },
    "wed": {"meals": []}
  },
  "leftovers": {
    "суп с фрикадельками": {"remaining": 2}
  },
  "shopping": {
    "list": [],
    "estimated_cost": null,
    "trips": []
  }
}
```

**Plan statuses:** `draft` → `approved` → `active` → `archived`

**File:** `data/plans/2026-WXX.json` (one file per week)

### 3. Structured Kitchen Inventory and Product Catalog

The flat fridge list has evolved into one versioned inventory/catalog envelope. One persisted identity exists per normalized product name; current stock is a projection of identities with `available: true`.

```json
{
  "schema_version": 4,
  "items": [
    {
      "id": "inv_01J...",
      "name": "готовые маринованные куриные ножки",
      "quantity": "11",
      "unit": "pcs",
      "package_count": 1,
      "storage": "fridge",
      "expires_on": "2026-07-17",
      "comment": "разогреть перед подачей",
      "created_at": "2026-07-14T01:15:00+02:00",
      "updated_at": "2026-07-14T01:15:00+02:00",
      "available": true,
      "category": "ready_meal",
      "ever_stocked": true
    }
  ]
}
```

Categories are `product` (ordinary product/ingredient), `prep` (semi-finished component prepared in advance), and `ready_meal` (ready to eat or reheat). Legacy/v2/v3 records migrate to `product`; recipe-only category metadata can persist with `available: false` and `ever_stocked: false`, which preserves `recipe_only` status until replenishment. The category is descriptive and does not replace the production-tracked `PrepItem` domain.

Storage values are `fridge`, `freezer`, `pantry`, and `counter`. Quantity is persisted as a canonical decimal string with an explicit unit; package count, expiry, and comment are optional. The lifecycle invariant is `available => ever_stocked`, and `ever_stocked` is monotonic. Removal and consumption preserve identities by setting `available: false`; replenishment retains identity/category and starts a fresh current batch. Web category/replenish/edit/delete mutations carry OCC preconditions checked inside the inventory lock; an absent recipe-only identity uses an explicit null precondition, while native tools are serialized latest-intent operations.

**Compatibility:** Legacy flat arrays and schema v2/v3 envelopes remain readable and migrate atomically on first mutation. Existing recipes, suggestions, cooking, and weekly shopping consume a derived set/list of available names. Structured quantity is persisted and displayed but does not drive recipe sufficiency or shopping arithmetic until recipes gain quantity requirements.

**Detailed contracts:** [`docs/issues/INV-2-structured-inventory-item-model.md`](docs/issues/INV-2-structured-inventory-item-model.md), [`docs/issues/INV-4-product-catalog-and-replenishment.md`](docs/issues/INV-4-product-catalog-and-replenishment.md), and INV-5 in [`BOARD.md`](BOARD.md).

**File:** `data/fridge.json` (schema v4 envelope after migration)

### 4. Extended Dish — Prep Dependencies

Dishes can declare which prep-items they require:

```json
{
  "name": "суп с фрикадельками",
  "ingredients": {
    "бульон": true,
    "овощи": true,
    "картофель": true,
    "морковь": true
  },
  "prep_depends": ["hybrid-meatballs"]
}
```

`prep_depends` is optional. Dishes without it work exactly as before.

---

## New Tools

### Prep Items

| Tool | Purpose |
|------|---------|
| `add_prep_item` | Define a prep-item (does not consume inventory) |
| `list_prep_items` | Show all prep-items with remaining quantities |
| `delete_prep_item` | Remove a prep-item definition |
| `make_prep` | Produce a batch, consuming source ingredients |

### Weekly Plans

| Tool | Purpose |
|------|---------|
| `create_week_plan` | Create empty plan for a week (status: draft) |
| `get_week_plan` | Show plan for given week (default: current) |
| `list_week_plans` | List all weeks with status |
| `add_meal_to_plan` | Add dish + portions to a day |
| `remove_meal_from_plan` | Remove a meal from a day |
| `set_plan_status` | Transition: draft → approved → active → archived |
| `repeat_week_plan` | Copy structure from a past week, adapt to current fridge |

### Shopping & Budget

| Tool | Purpose |
|------|---------|
| `generate_shopping_list` | Aggregate essential ingredient uses from plan + depleted/planned prep, subtract one current fridge use, persist snapshot |
| `split_shopping_list` | First-fit priced items into soft €100 trips; retain unpriced/oversized warnings |
| `estimate_plan_cost` | Apply explicit unit prices until receipt price DB exists; soft €150 weekly status |

### Leftovers

| Tool | Purpose |
|------|---------|
| `record_leftovers` | Set remaining portions for a dish |
| `get_leftovers` | Show current leftovers across the active plan |

---

## Budget Rules

- Max **€100** per shopping trip
- Max **€150** per week
- Enforcement: **soft** — warn, don't block. Tune over time.
- Recipes currently have no gram quantities. Phase 3 therefore counts one
  ingredient use per planned cooking occurrence; one fridge entry covers one use.
- Only essential ingredients are included automatically.
- Planned prep and depleted prep dependencies contribute one batch of essential
  source ingredients. `make_prep` replaces the old remaining quantity with the
  fresh batch yield, so projected availability is the yield (not remaining +
  yield); capacity shortfalls remain explicit warnings.
- Until receipt ingestion provides a price DB, `estimate_plan_cost` accepts an
  explicit ingredient → EUR-per-shopping-unit map. Partial coverage reports
  `weekly_budget_status: unknown`, never a false within-budget result.
- `split_shopping_list` runs after a cost-estimate snapshot and uses deterministic
  first-fit decreasing for priced items; unpriced items remain outside
  cost-limited trips and are reported explicitly.
- Price data source: uploaded grocery receipts (future feature)

---

## Leftover Tracking

- When a meal is cooked with N portions, `remaining` is set to N.
- As portions are eaten (reported during planning or explicitly),
  `remaining` decrements.
- Purpose: **calibrate** portion estimates during the first weeks.
- May simplify later once patterns are established.

---

## Implementation Phases

See `BOARD.md` for the live kanban board.

### Phase 1: Prep Data Layer ✅
- Prep-items entity + repository + handler
- Dish `prep_depends` field
- Structured ordinary inventory was deferred and is now specified separately as INV-2

### INV-2: Structured Kitchen Inventory (Ready)
- Versioned `InventoryItem` model and legacy flat-list migration
- Quantity/unit, package count, storage, expiry, and comment metadata
- Backward-compatible name projection for existing recipe/suggestion/shopping flows
- Native structured CRUD plus item-oriented web API/UI

### Phase 2: Weekly Plans
- Plan model + repository
- CRUD tools (create, add/remove meal, status, get, list, repeat)

### Phase 3: Shopping & Budget ✅
- Aggregated essential shopping snapshot persisted in each week plan
- Planned/depleted prep source expansion and capacity warnings
- Optional explicit-price estimate with incomplete/within/over soft status
- Deterministic soft-limit trip splitting
- Read-only shopping/budget/trips view in the weekly-plan UI

### Phase 4: Leftovers & Calibration
- Auto-tracking on cook
- Feedback loop during planning

### Phase 5 (Future): Suggestion Engine for Plans
- Integrate existing ranker into plan generation
- Plate ratio checking
- Price DB from receipts
