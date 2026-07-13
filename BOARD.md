# Meal Manager — Development Board

> Kanban for the weekly meal planning extension.
> See `ARCHITECTURE.md` for design details.

---

## ✅ DONE
- [x] Architecture decisions Q1–Q16 (July 2026 session)
- [x] Notes system (`data/meal_planning_notes.md`)
- [x] Seasonal notes (`data/seasonal_notes.md`)
- [x] Architecture design document (`ARCHITECTURE.md`)
- [x] Web interface moved into repo (`web/`)

---

## 📐 DESIGN (ready to implement)

### Phase 1: Data Layer ✅
- [x] PrepItem model (`src/prep_item.py`)
- [x] Prep-item repository (`src/repositories/json_prep_item.py`)
- [x] Dish `prep_depends` field (backward-compatible)
- [x] Handlers: `add_prep_item`, `list_prep_items`, `delete_prep_item`, `make_prep`
- [x] `register_cooked_meal` consumes prep items
- [x] Tests (11 new unit tests, all 115 passing)

### Phase 2: Weekly Plans ✅
- [x] Plan model + `data/plans/` directory
- [x] Plan repository (`src/repositories/json_plan.py`)
- [x] `create_week_plan` handler
- [x] `get_week_plan` handler
- [x] `list_week_plans` handler
- [x] `add_meal_to_plan` handler
- [x] `remove_meal_from_plan` handler
- [x] `set_plan_status` handler
- [x] `repeat_week_plan` handler
- [x] Read-only weekly-plan history and detail view in web UI
- [x] Model and end-to-end lifecycle tests (133 unit, 136 integration)
- [x] Focused web validation/GET-only/XSS smoke tests

### Phase 3: Shopping & Budget ✅
- [x] `generate_shopping_list` handler
- [x] `split_shopping_list` handler
- [x] `estimate_plan_cost` handler (soft, explicit prices until price DB exists)
- [x] Planned/depleted prep source aggregation
- [x] Read-only shopping, budget, and trip view in weekly-plan details
- [x] Unit and end-to-end handler coverage (185 unit, 150 integration)

### Phase 4: Leftovers & Calibration
- [ ] `record_leftovers` handler
- [ ] `get_leftovers` handler
- [ ] Auto-tracking on `register_cooked_meal`

---

## 🔨 DOING
*(nothing yet)*

---

## 📋 BACKLOG (future)
- [ ] Price DB from grocery receipts
- [ ] Suggestion engine integration for plan generation
- [ ] Plate ratio checker (Harvard Healthy Eating Plate)
- [ ] Seasonality auto-hints
- [ ] Web UI editing controls for plans (read-only view implemented in Phase 2)
