# INV-1 — Edit/rename an item in kitchen inventory

**Status:** 📐 Ready
**Requested by:** Dmitrii Koriakov
**Source:** Meal Planning Development, after successful live inventory intake for week 2026-W29

## User problem

The kitchen inventory now accepts additions correctly, but a stored item can only be deleted and re-added. The web screenshot shows each inventory chip with its label, recipe-use indicator, and an `✕` delete action; there is no edit/rename action. The native `meal_manager` surface has the same gap: `update_fridge_inventory` supports only `add` and `remove`.

This makes small corrections unnecessarily destructive and error-prone—for example correcting a brand, pasta shape/number, typo, or naming convention requires remembering the old value, deleting it, and adding the replacement as two separate operations.

In household terminology, “fridge” means the complete kitchen stock (fridge, freezer, pantry/cupboards, dry goods and cans), so the feature applies to every ordinary inventory item.

## Observed workflow and reproduction

1. Add an inventory item through `update_fridge_inventory` or the web input.
2. Open **Холодильник** in the web UI.
3. Locate the item chip.
4. Observe that the only per-item control is `✕` (delete).
5. Ask the agent to correct the item name.
6. Observe that no atomic native rename/edit tool exists; the agent must approximate the change with remove + add.

## Expected result

- The agent can atomically rename one existing kitchen-inventory item through a native `meal_manager` tool.
- Dima can edit the same item label directly from its web chip without deleting and recreating it.
- Both surfaces apply identical normalization, not-found, no-op, and duplicate-target rules.
- The resulting inventory remains the shared source viewed by Telegram/native tools and the web UI.

## Product decision

This issue edits **only the item name/label**. It does not introduce quantity, package count, weight, storage zone, expiry, or arbitrary item metadata. A precise public name is therefore preferred over a vague future-facing edit contract:

- Native tool: `rename_fridge_item`
- Arguments: `old_ingredient`, `new_ingredient`
- Web action label: `Редактировать название продукта …`

Quantity-aware inventory remains a separate feature. In particular, `2 кг` of chicken is still contextual planning information rather than a structured field after INV-1.

## Domain contract

1. Normalize both names with the existing ingredient rule (`strip().lower()` plus current length/blank validation).
2. Perform the operation as one lock-protected load → validate → replace → save transaction.
3. If the old item does not exist, return a clear not-found error and do not write.
4. If normalized old and new names are equal, return an explicit no-op and do not write.
5. If the new normalized name already belongs to a different inventory item, reject the rename as a collision; do not silently merge or delete either item.
6. Preserve the position of the renamed item in native persistence. The web may continue displaying its existing sorted representation if sorting is part of the current web contract.
7. Rename inventory only. Do not rename recipe ingredients, prep-item source ingredients, shopping snapshots, history, or plan references.
8. After a successful rename, availability-based suggestions and fridge utility naturally recalculate against the new name on the next read/refresh.
9. Persist through `FridgeRepository`/atomic JSON writing on the native path; the agent must never edit `fridge.json` or drive the web UI as a fallback.

## Native vertical slice

- Add `rename_fridge_item` handler with a complete body schema and unified error envelope.
- Reuse existing normalization and argument-limit helpers.
- Extend `FridgeRepository` only if a repository-level atomic rename primitive is needed; do not implement rename as two public tool calls.
- Add the tool to `plugin.yaml`, `skill.md`, README/tool counts, and operational skill documentation.
- Verify the registered model-visible schema exposes and requires both arguments through the real `register(ctx)` seam.

## Web API contract

Add a dedicated mutation route rather than replacing the complete inventory list from the browser:

```http
PUT /api/fridge/item
Content-Type: application/json

{
  "old_ingredient": "паста barilla penne lisce №71",
  "new_ingredient": "паста barilla penne lisce n°71"
}
```

Success returns the normalized renamed item and current inventory. Not-found and duplicate-target conflicts return explicit 4xx responses. Empty, overlong, malformed, or non-string values are rejected without changing persistence.

The route must preserve the existing web/native shared-data behavior and use atomic writing. It must not expose a generic arbitrary-file or whole-list replacement primitive for this UI action.

## Web UX contract

Each inventory chip gets a separate edit button in addition to delete:

- visible pencil/edit affordance with contextual accessible name;
- activating it turns only that chip into an inline text editor prefilled with the current value;
- `Enter` saves, `Escape` cancels, and explicit Save/Cancel controls are available;
- focus moves into the editor and returns to the edited chip/control after save or cancel;
- only one chip can be edited at a time;
- while saving, prevent duplicate submissions and show a clear error/toast on conflict or network failure;
- on success, refresh inventory, stats, suggestions, and shopping-derived views through the existing refresh flow;
- deletion remains a distinct control and cannot be triggered accidentally while editing;
- user-controlled names are rendered as text, never interpolated into executable inline handlers/HTML;
- edit/delete touch targets remain at least 44×44 px;
- chip wrapping must not create horizontal overflow on desktop or mobile.

## Acceptance criteria

### Native tool

- [ ] Agent can rename an existing inventory item in one call.
- [ ] Old name disappears and new normalized name appears exactly once.
- [ ] Not-found, blank, overlong, same-name, and duplicate-target cases are deterministic and non-destructive.
- [ ] Handler schema exposes and requires `old_ingredient` and `new_ingredient` after real plugin registration.
- [ ] `list_fridge` verifies the mutation by reverse read.

### Web API/UI

- [ ] Every inventory chip has separate edit and delete controls.
- [ ] Inline edit supports mouse, keyboard, Enter, Escape, Save, and Cancel.
- [ ] Focus behavior and contextual accessible names pass browser accessibility checks.
- [ ] Rename errors do not remove or duplicate an inventory item.
- [ ] Successful rename refreshes all availability-derived views.
- [ ] User-provided names remain XSS-safe, including quotes, angle brackets, ampersands, and Unicode.
- [ ] Desktop and mobile layouts remain free of horizontal overflow.

### Verification and release

- [ ] Focused RED → GREEN tests cover native and web rename paths.
- [ ] Full `python3 test_unit.py` and `python3 test_integration.py` pass.
- [ ] Focused web/API tests and `web/test_web_a11y.py` pass with Chromium as a required gate.
- [ ] Manual desktop/mobile browser QA confirms edit, cancel, conflict, and refresh behavior with no console errors.
- [ ] Independent fail-closed review passes for the final frozen staged snapshot.
- [ ] Commit/push, service refresh, live native-tool verification, and live web verification complete.
- [ ] Completion report is sent to Meal Planning with availability and any remaining quantity limitation.

## Dependencies

- Existing flat `fridge.json` inventory model and normalization rules.
- Existing native repository locking/atomic-write contract.
- Existing web fridge add/remove API and chip renderer.
- Correct function-tool schema envelope delivered by TOOLS-1.

## Non-goals

- Structured quantities such as `2 кг`, package counts, or units.
- Storage zones (fridge/freezer/pantry) for ordinary ingredients.
- Batch rename or search-and-replace.
- Renaming ingredient references inside recipes, prep items, plans, shopping snapshots, or history.
- Agent-driven clicking or editing through the web UI.
- Redesigning the complete inventory data model or solving unrelated cross-process concurrency work.
