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

### Phase 2: Weekly Plans
- [ ] Plan model + `data/plans/` directory
- [ ] Plan repository (`src/repositories/json_plan.py`)
- [ ] `create_week_plan` handler
- [ ] `get_week_plan` handler
- [ ] `list_week_plans` handler
- [ ] `add_meal_to_plan` handler
- [ ] `remove_meal_from_plan` handler
- [ ] `set_plan_status` handler
- [ ] `repeat_week_plan` handler

### Phase 3: Shopping & Budget
- [ ] `generate_shopping_list` handler
- [ ] `split_shopping_list` handler
- [ ] `estimate_plan_cost` handler (soft, price DB optional)

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
- [ ] Web UI dashboard for plans
