# REC-1 — Cooking instructions in recipe cards

## Problem

A dish currently stores only its name, ingredients, and optional prep dependencies. The Web recipe card/editor therefore cannot answer “how to cook it”, and the agent has no direct read/set/clear operation for cooking instructions.

## Product contract

- `Dish.instructions` is optional free text shown as **«Как готовить»**.
- Leading/trailing whitespace is trimmed; internal case and line breaks are preserved.
- `null`, an empty string, or whitespace-only text clears the field.
- The maximum length is 20,000 characters.
- Legacy recipes without the field remain valid and serialize without a synthetic empty key.
- Instructions describe preparation only; ingredients remain the source of availability, shopping, and suggestion calculations.

## Native tools

- `add_dish` and `add_dishes_batch` accept optional instructions.
- `edit_dish` preserves instructions when omitted and replaces/clears them when provided.
- `get_dish_recipe` returns one complete recipe including an explicit nullable `instructions` value.
- `set_dish_instructions` changes or clears only the cooking instructions.
- `delete_dish` continues to delete the complete recipe.

## Web UX

- Every recipe card contains a **«Как готовить»** area; populated instructions are expandable and missing instructions are shown explicitly as “не указано”.
- Create/edit modal contains a labeled multiline textarea. The canonical limit is 20,000 Unicode code points after Python `str.strip()` in `Dish`; Web sends the raw value instead of imposing an incompatible HTML/UTF-16 `maxlength`.
- Clearing and saving the textarea removes the field.
- Search matches instruction text.
- User text is escaped, line breaks are retained, modal focus semantics and 44 px controls remain intact.

## Persistence and concurrency

- `Dish` is the canonical validation and serialization boundary.
- Agent and Web writers share the same advisory cross-process file lock.
- `GET /api/dishes` returns a semantic SHA-256 catalog version.
- Web create/update/delete sends `expected_version`; stale writes return HTTP 409 `dish_catalog_conflict` without changing the file or silently retrying.
- Malformed unrelated legacy rows continue to be preserved by `JsonDishRepository`.

## Acceptance gate

- RED→GREEN domain tests for trim/newlines/clear/legacy/length validation.
- Native add/read/set/clear persistence tests and tool manifest/schema parity.
- Web API create/edit/clear/stale conflict tests.
- Real Chromium card/modal/save/XSS/accessibility checks.
- Cross-process Web/native serialization regression.
- Full unit, integration, Web, manifest, syntax, and architecture gates.
- Production backup, coordinated Gateway/Web restart, disposable live mutation QA, and read-only production verification.
