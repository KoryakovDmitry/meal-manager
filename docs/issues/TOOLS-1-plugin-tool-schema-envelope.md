# TOOLS-1 — Plugin tool schemas lose handler arguments

**Status:** 🔨 Doing
**Reported from:** Meal Planning, inventory intake for week 2026-W29
**Affected workflow:** adding 2 kg of raw chicken drumsticks through `update_fridge_inventory`

## Problem

Every handler defines a JSON input schema such as `{"type": "object", "properties": ..., "required": ...}`. The plugin entry point passed that body directly to `ctx.register_tool`. Hermes expects a complete function schema with `name`, `description`, and the JSON input body nested under `parameters`.

The handler itself accepts `action` and `ingredients`, but the model-visible tool declaration therefore exposed no callable arguments. In Meal Planning the agent could select `update_fridge_inventory` but could not supply the required `action`, so the kitchen inventory could not be recorded through the supported native flow.

This affects all 34 meal-manager tools, not only the reported inventory tool.

## Root cause and component boundary

- Handler boundary: correct body schemas in `src/handlers/*`.
- Plugin registration boundary: incorrect schema shape in `__init__.py:register`.
- Hermes registry/provider boundary: expects OpenAI function-tool shape and reads arguments from `function.parameters`.

The defect is fixed at the plugin registration boundary rather than duplicating wrappers in every handler or weakening handler validation.

## Scope

- Wrap every auto-discovered handler body schema as a complete Hermes function schema during registration.
- Preserve the handler description, JSON input properties, required fields, enums, and limits unchanged.
- Add an integration regression at the real `register(ctx)` seam.
- Verify the published schema for `update_fridge_inventory` exposes required `action` and `ingredients` arguments.
- Verify all 34 registered tools have an object-valued `parameters` schema.
- Restart/reload the live Hermes gateway so new conversations receive the corrected immutable tool surface.

## Contracts

1. Handler modules continue exporting body-level `SCHEMA` dictionaries with top-level `description`, `type`, `properties`, and optional `required`.
2. `register(ctx)` converts each one to:

   ```json
   {
     "name": "<handler NAME>",
     "description": "<handler description>",
     "parameters": {
       "type": "object",
       "properties": {},
       "required": []
     }
   }
   ```

3. The wrapper does not mutate the handler-owned schema.
4. Tool handlers and persisted meal data remain unchanged.

## Acceptance criteria

- [x] A focused regression test fails on the pre-fix registration behavior.
- [x] `update_fridge_inventory.function.parameters.properties` contains `action` and `ingredients`.
- [x] Both fields remain required.
- [x] Every one of the 34 tools exposes an object parameter schema after registration.
- [ ] Full `python3 test_unit.py` passes.
- [ ] Full `python3 test_integration.py` passes.
- [ ] Independent fail-closed review passes for the frozen staged snapshot.
- [ ] Fix is committed and pushed to `origin/main`.
- [ ] Gateway is restarted/reloaded and a fresh runtime schema is verified.
- [ ] Meal Planning receives a completion report and can resume the original chicken-drumstick inventory operation.

## Dependencies

- Existing Hermes `PluginContext.register_tool` and central tool registry contracts.
- A fresh gateway session after deployment because tool schemas are immutable within an active conversation for prompt-cache safety.

## Non-goals

- Changing fridge persistence from a flat ingredient list to quantities or storage zones.
- Recording the reported chicken directly in JSON or through the web UI.
- Weakening `require_arg` checks inside handlers.
- Changing the 34 individual handler input contracts.
- Implementing quantity-aware inventory as part of this bug fix.
