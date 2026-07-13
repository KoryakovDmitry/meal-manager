# Meal Manager — Development Board

> Kanban for the weekly meal planning extension.
> See `ARCHITECTURE.md` for design details.

---

## ✅ DONE
- [x] Architecture decisions Q1–Q16 (July 2026 session)
- [x] Notes system (`data/meal_planning_notes.md`)
- [x] Seasonal notes (`data/seasonal_notes.md`)
- [x] Architecture design document (`ARCHITECTURE.md`)

---

## 📐 DESIGN (ready to implement)

### Phase 1: Data Layer
- [ ] Prep-items entity + `data/prep_items.json`
- [ ] Prep-item repository (`src/repositories/json_prep.py`)
- [ ] Fridge storage tags (backward-compatible: flat array → tagged dict)
- [ ] Dish `prep_depends` field

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
