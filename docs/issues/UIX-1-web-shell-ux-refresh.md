# UIX-1 — Web shell UX refresh

**Type:** repository board issue
**Status:** completed
**Requested by:** Dima

## Problem

The current meal-planning web UI uses a wide horizontal tab bar that scales poorly, consumes vertical space, and makes the application feel like a rough prototype. The browser tab also has no recognizable icon.

The web interface is Dima's personal viewing surface with limited manual controls. This issue must not bypass or replace the `meal_manager` plugin workflow for meal-planning domain changes.

## Scope

- Replace horizontal tabs with a vertical sidebar.
- Collapse desktop navigation to a compact icon rail and remember the preference.
- Use a hidden drawer on mobile/tablet.
- Add a local favicon and matching application mark.
- Improve visual hierarchy, spacing, focus/hover/active states, and empty states.
- Make recipe dialogs modal for assistive technology, contain keyboard focus, and restore focus on every close path.
- Keep weekly-plan rows keyboard-native and compact action controls at least 44×44px on every viewport.
- Render persisted dish/ingredient text safely without embedding it in inline event handlers.
- Preserve all existing sections, API calls, and behaviors.

## Acceptance criteria

- [x] Desktop navigation is vertical, with content in a separate main column.
- [x] Desktop sidebar can collapse/expand and persists state in `localStorage`.
- [x] Mobile sidebar is hidden by default and closes via backdrop, explicit close, Escape, or navigation selection.
- [x] Active section uses both a visible state and `aria-current="page"`.
- [x] Keyboard focus is visible and all interactive targets are at least 44×44px.
- [x] Recipe dialogs expose modal semantics, trap focus, inert the background, and restore focus after cancel, backdrop, Escape, and save.
- [x] Weekly-plan rows are native keyboard-operable buttons.
- [x] Persisted dish and ingredient values render as text and are never embedded in inline event handlers.
- [x] Populated fridge items display recipe-use counts from `/api/stats`; only zero-use items receive unused styling.
- [x] Favicon is local, served by FastAPI, and visible in the browser tab.
- [x] All seven existing sections and refresh actions remain available.
- [x] Weekly-plan web routes remain GET-only/read-only.
- [x] Full unit/integration/web/JS verification passes.
- [x] Desktop expanded/collapsed and mobile closed/open states pass visual QA.
- [x] Independent fail-closed review passes.
- [x] Commit/push and live deployment verification pass.

## Non-goals

- Changes to meal-planning domain logic or schemas.
- Direct plan JSON editing from the web application.
- New backend APIs.
- Replacing the plugin's intended meal-planning flow.
