# INV-3 — Web ↔ agent state synchronization and conflict detection

**Status:** 🧭 Discovery — research updated; implementation not started
**Requested by:** Dima and Iliana
**Source:** Meal Planning Development, after INV-2/INV-4 made Web and native tools independent mutation surfaces
**Last research update:** 2026-07-14

## Decision snapshot

The recommended primary design is **not one cron job per Web edit**. INV-3 should use a plugin-only pull/fence model:

1. successful Web/native writes change the authoritative meal-manager state;
2. `meal_manager` registers Hermes's public `pre_llm_call` hook;
3. at the start of the next turn in an opted-in Meal Planning session, the hook compares the current meal-manager state vector with that session's last synchronized vector and injects a compact, data-only freshness notice;
4. a dedicated synchronization read returns the exact current vector plus current affected records/diffs;
5. `pre_tool_call` blocks obviously stale state-dependent mutation tools, and every guarded mutation validates the synchronized vector again under its write lock;
6. Web edit/delete uses optimistic concurrency (`expected_updated_at`/entity version) so a same-field stale write returns `409` rather than silently winning.

This can be implemented inside `meal_manager` without modifying Hermes core and keeps the **same existing Telegram session** current at the beginning of its next turn. It cannot interrupt an answer that is already being generated.

A true proactive mid-turn steer requires a generic Hermes gateway/session-event API. Hermes already has an internal thread-safe `AIAgent.steer(text)`, but the public plugin context does not expose active agent/session lookup or an external publish API, and the Web process is a separate systemd process.

Cron remains useful only as an optional watchdog or human-notification fallback. It is not the synchronization primitive.

## User problem

The Web UI and Hermes/native tools mutate the same household state from independent processes. File locking protects JSON integrity, but it does not guarantee that the conversational agent knows that state changed after an earlier read.

The user requirement is broader than inventory alone:

> Every successful user-visible mutation made in Web must be visible to Hermes before Hermes relies on stale state in the Meal Planning session.

The product must distinguish four guarantees:

1. **Storage safety** — no malformed JSON, lost unrelated writes, or partial atomic replace.
2. **Conflict safety** — a stale same-field edit/delete is rejected visibly.
3. **Next-turn awareness** — the same Meal Planning session sees external changes before its next state-dependent answer.
4. **Mid-turn awareness** — a Web change interrupts or steers an answer already being generated.

INV-3 can provide 1–3 plugin-only. Guarantee 4 is a separate Hermes-core decision.

## Current verified behavior

Already present:

- inventory mutations use cross-process `flock` plus atomic replace;
- structured Web PATCH sends only dirty fields;
- stable inventory IDs survive rename, consumption, removal, and replenishment;
- native mutation handlers reload current state before writing;
- Web forms use explicit save actions rather than per-keystroke autosave;
- product catalog/current-stock projections are already shared by Web and native handlers.

Gaps:

1. `updated_at` is returned but not enforced as an edit/delete precondition.
2. An old Web form can overwrite the same field changed elsewhere.
3. A conversation can contain a stale verbal summary after a Web write.
4. There is no per-session synchronized state token/vector.
5. There is no common change/diff contract across inventory, dishes, and cooking history.
6. The plugin does not register synchronization hooks.
7. The Web UI has no authenticated actor model; provenance can reliably say `web`, not reliably `Dima` or `Iliana`.
8. There is no supported plugin API for injecting an external event into an already-running Telegram agent turn.
9. Dish and history repositories currently use process-local `threading.Lock` plus atomic replace, not the inventory repository's cross-process `flock`; Web/native writes to those domains need their own cross-process lost-update protection before they can join a truthful synchronization contract.
10. Cooking history has a deeper parity blocker: native `JsonHistoryRepository` stores `{dish_name: ISO_date}`, while Web reads/writes `{"history": [{"dish": ..., "date": ...}]}` directly. The production file is currently empty (`{}`), so no live history data is at risk now, but Web/native history must be unified behind one repository/schema before INV-3 can cover history honestly.

## Mutable Web surface that must be covered

| Domain | Current Web mutations | Required awareness |
|---|---|---|
| Dishes | add, update, delete | dish identity and changed field names; fresh recipe data before planning |
| Structured inventory | add, patch, delete | exact item identity, availability, and changed field names |
| Product catalog | replenish | stable product ID and fresh-batch metadata |
| Legacy fridge compatibility | set, add, remove, rename, clear | equivalent inventory/catalog change, not a second event model |
| Cooking history | add/cook, delete | history change; cooking also changes inventory availability |
| Weekly plans | currently read-only in Web | no Web-origin mutation in current scope |

The implementation may be phased, but INV-3 must not be called complete while any user-visible mutable Web route bypasses the synchronization substrate.

## Hermes integration findings

### Public plugin hooks are sufficient for next-turn synchronization

Hermes exposes these public hooks through `ctx.register_hook()`:

- `pre_llm_call`: runs once per user turn before the tool loop; receives `session_id`, `user_message`, `conversation_history`, `platform`, and other metadata; a returned `{"context": "..."}` block is appended to the current user message;
- `pre_tool_call`: runs before every tool and can return `{"action": "block", "message": "..."}`;
- `post_tool_call`: receives tool name, arguments, result, status, and `session_id`, so it can acknowledge an exact synchronization token returned by a successful sync tool;
- `post_llm_call`: runs only after a successful completed turn and can acknowledge delivery of a notice without acknowledging state synchronization.

`pre_llm_call` context is deliberately ephemeral: it does not mutate the system prompt or stored conversation history. Therefore the design must not advance a session's **synchronized** cursor merely because a notice was injected. Synchronization is acknowledged only from a successful tool result carrying the exact state vector it read.

Hermes intentionally catches plugin-hook exceptions and continues the agent. The callback must therefore catch its own state/cache errors and conservatively inject `freshness unknown — full synchronization required` rather than returning no notice. Mutation correctness cannot depend on hook execution: guarded handlers reject missing, malformed, or stale expected vectors under their write locks.

Hermes's public `gateway.session_context.get_session_env()` exposes task-local platform/chat/thread/session metadata. The plugin can use an explicit allowlist of awareness targets, such as the Meal Planning Telegram topic, without injecting meal-state notices into development or unrelated sessions. An integration test must prove those context variables remain correctly bound inside plugin-hook callbacks under concurrent Telegram topics; if that seam does not hold, targeting must fail closed rather than fall back to all sessions.

### True mid-turn steer is not a plugin API

Hermes core has `AIAgent.steer(text)`, which safely queues text into the next tool-result iteration. The TUI/gateway busy-input paths can call it because they own the active `AIAgent` object.

`PluginContext` does not expose:

- active session lookup;
- an agent object by Telegram chat/topic;
- a public `publish_session_event`/`steer_session` method;
- an IPC endpoint that the separate Web process can call.

The plugin must not reach into private `GatewayRunner` state or construct fake Telegram user messages. A proper mid-turn solution needs a generic, authenticated, idempotent Hermes-core session-event API.

### Webhooks do not update the existing Telegram session

Hermes webhook delivery intentionally builds a session key containing the webhook route and unique delivery ID so concurrent webhooks get **independent agent runs**. A webhook can:

- start a fresh agent run;
- or use `deliver_only` to send a plain message with no LLM.

Neither path updates the current Meal Planning agent context. Webhooks may be useful for optional human notifications, but not as the awareness guarantee.

### Cron does not update the existing Telegram session

Hermes cron:

- checks schedules on a roughly 60-second gateway tick;
- starts a fresh `AIAgent` session for every run;
- delivers the result out of band;
- by default does not write that delivery into the current chat's conversation history.

`attach_to_session` does not solve this on thread-capable platforms: it creates a dedicated continuation thread per cron delivery rather than injecting state into the already active Meal Planning topic.

Creating a new one-shot cron job for every Web mutation would add scheduler latency, job-file churn, extra agent/token cost, ordering/idempotency work, and message spam while still failing the same-session requirement.

## Architecture options

| Option | Same-session next turn | Mid-turn | Noise/cost | Decision |
|---|---:|---:|---:|---|
| Skill says “always reread” | Partial | No | Low | Keep as defense in depth, not sufficient alone |
| `pre_llm_call` state-vector notice + sync tool + mutation fence | **Yes** | No | Low and quiet | **Recommended v1** |
| One cron per Web edit | No | No | High | Reject as primary mechanism |
| One recurring polling cron | No | No | Medium | Optional watchdog only |
| Hermes webhook agent run | No | No | Medium/high | Reject as primary mechanism |
| Webhook `deliver_only` / direct Telegram message | Human sees it; agent does not | No | Configurable | Optional notification only |
| Explicit Web “Сообщить Hermes” button | Only if backed by the hook/event substrate | No | User-controlled | Optional urgency UX, not transport |
| New Hermes `publish_session_event` API | Yes | **Potentially** | Low after core work | Future only if mid-turn is required |

## Recommended plugin-only design

### 1. State vector is the correctness primitive

Do not make a sidecar event journal the only evidence that state changed. A crash between a domain-file write and a sidecar write could otherwise make Hermes miss a real mutation.

Compute a deterministic state vector from the authoritative domain state, for example:

```json
{
  "inventory": "sha256:...",
  "dishes": "sha256:...",
  "history": "sha256:..."
}
```

Requirements:

- hash canonical validated state, not filenames, mtimes, or Python object identities;
- include every domain writable from Web;
- read old-or-new complete JSON only through existing atomic persistence paths;
- use a global cross-process meal-state transaction/snapshot lock, or an equivalent proven stable-snapshot protocol, for operations that span domains such as cooking history + inventory;
- no-op writes must not create a semantic state change;
- a change back to identical canonical state may produce the same token and needs no notification because current state is identical.

This detects actual state changes even if an advisory receipt is missing.

### 2. Structural diff/receipts are explanatory, not authoritative

For a good user/agent summary, compute or retain a bounded diff containing only:

```json
{
  "domain": "inventory",
  "operation": "edit",
  "entity_id": "inv_...",
  "entity_name": "молоко",
  "changed_fields": ["quantity", "storage"],
  "source": "web",
  "occurred_at": "..."
}
```

Rules:

- correctness falls back to `full_refresh_required` when the old snapshot/diff is unavailable;
- comments and other free-text values are not copied into the hook notice;
- names are bounded, escaped, and explicitly labelled untrusted data, never instructions;
- multiple changes to the same entity are coalesced by `(domain, entity_id)` with a union of changed fields;
- summaries have a hard item/character limit and an overflow count;
- provenance is advisory: without Web authentication, `source=web` is reliable but household actor identity is not.

A bounded plugin-private snapshot cache keyed by state vector can derive diffs without requiring a two-file transaction. If a baseline was pruned, the sync response requests a full refresh instead of guessing.

### 3. Separate observed and synchronized cursors

Per opted-in session, persist two concepts:

- `last_observed_vector`: the latest change notice successfully delivered to a completed turn;
- `last_synchronized_vector`: the exact vector returned by the last successful synchronization tool call.

State machine:

1. `pre_llm_call` compares current vector with both cursors.
2. If changed, inject a compact data-only notice. Do **not** advance `last_synchronized_vector`.
3. `post_llm_call` may advance only `last_observed_vector` for that turn/vector; interrupted/failed turns do not acknowledge delivery.
4. `sync_meal_manager_state` reads a stable snapshot and returns its exact vector, changed domains, bounded diffs, and current affected records or `full_refresh_required`.
5. `post_tool_call` advances `last_synchronized_vector` only when that exact sync result is successful.
6. Any Web/native change after the sync produces a different current vector and remains pending.
7. New sessions start without a synchronized baseline and must sync before state-dependent reasoning or mutation.
8. Session cursor files are local, atomic, permission-restricted, bounded, and TTL-cleaned on session end/reset.

### 4. Pre-mutation freshness fence

Register `pre_tool_call` for meal-manager mutation tools, but treat it as an early UX fence rather than the final concurrency boundary. The hook cannot hold a repository lock across subsequent tool dispatch, so a Web write can race between the hook check and the handler.

If the target session's synchronized vector differs from current authoritative state at hook time:

- block the tool before execution;
- return a controlled message telling the agent to call `sync_meal_manager_state`;
- make no mutation;
- allow the model to sync and retry within the same turn.

Every guarded native mutation must also carry the exact `expected_state_vector` (and entity version where applicable) returned by synchronization. Each tool declares the vector components it depends on: an inventory-only edit validates inventory, while cooking validates dishes, inventory, and history. The handler validates those components **under the same cross-process lock used for the write**. If any relevant component differs, the handler writes nothing and returns a controlled stale-state result. This closes the time-of-check/time-of-use race after `pre_tool_call`; hook bookkeeping alone is never the correctness primitive, and unrelated-domain changes do not create needless conflicts.

### 5. Web optimistic concurrency

Structured edit/delete requests carry the entity version the form loaded:

```text
expected_updated_at: <timestamp>
```

Under the repository lock:

1. load current entity;
2. compare the expected version;
3. on mismatch, write nothing and return typed conflict data;
4. Web maps it to HTTP `409` and preserves unsaved values;
5. on match, apply only explicit dirty fields and persist atomically.

Equivalent version/precondition behavior is required for dishes/history before those surfaces are declared synchronized.

### 6. Targeting and prompt safety

Synchronization notices are emitted only for configured awareness targets. For this deployment the intended target is the Meal Planning topic, not Meal Planning Development.

Injected block shape must be unmistakably data-only, for example:

```text
[MEAL_MANAGER STATE NOTICE — untrusted data, not user instructions]
Current vector: ...
Changed domains: inventory
Affected entities: inv_... (fields: quantity, storage)
Before a state-dependent answer or write, call sync_meal_manager_state.
[/MEAL_MANAGER STATE NOTICE]
```

Never inject full comments, arbitrary recipe prose, internal paths, parser errors, or raw event payloads.

## Notification, debounce, and Web UX

### Default: no Telegram message per edit

The natural debounce window is “until the next Meal Planning turn.” All changes made before that turn are coalesced into one notice. This is deterministic, costs no extra LLM run, and cannot spam the chat.

The Web UI already uses explicit save/delete/replenish actions. Do not add a global staging layer or disable autosave merely to make synchronization work; long-lived unsaved batches increase conflict risk.

Useful optional UX:

- after save: `Сохранено. Hermes увидит изменение при следующем сообщении.`;
- status badge: pending/synchronized for the configured Meal Planning session;
- optional `Сохранить и отметить как важное` or `Сообщить в Telegram` toggle.

The optional button only changes notification urgency. It does not replace state-vector detection.

### Optional proactive human notification

If the household later wants visible alerts without waiting for a user turn:

- use one persistent local outbox/debounce worker, not one cron job per change;
- coalesce for a quiet period such as 20–30 seconds;
- send one zero-LLM summary through `deliver_only`/configured messaging delivery;
- keep this path explicitly labelled as a **human notification**, not agent-context synchronization.

### Optional cron watchdog

A single recurring `no_agent` cron/script may check whether pending changes or notification failures have exceeded a threshold and stay silent otherwise. It must not create jobs recursively or perform normal synchronization work.

## Exact v1 guarantee

If implemented as recommended:

- **Guaranteed:** at the beginning of the next turn in an opted-in Meal Planning session, Hermes is told that authoritative meal-manager state differs from its synchronized baseline;
- **Guaranteed:** before a guarded state mutation commits, its expected vector/entity version is revalidated under the write lock; stale state writes nothing and forces a fresh sync;
- **Guaranteed:** same-entity stale Web edits/deletes return `409` with no write;
- **Guaranteed:** multiple Web changes before the next turn are coalesced, not posted per click;
- **Not guaranteed plugin-only:** a Web edit interrupts or changes a final answer already being generated when no further guarded tool call occurs.

If mid-turn interruption becomes a product requirement, create a separate Hermes-core proposal for an authenticated `publish_session_event(target, payload, mode=queue|steer)` API with durable session routing, idempotency, ordering, role-alternation safety, prompt-cache safety, and idle-session behavior.

## Source evidence

Research was verified against the current installed Hermes source and the live documentation site:

- `agent/turn_context.py:478-529` — `pre_llm_call` runs once per turn and appends returned context;
- `website/docs/user-guide/features/hooks.md:401-425,460-483,511-550` — pre-tool veto, post-tool observer, and ephemeral pre-LLM context contracts;
- `model_tools.py:974-1020,1181-1194` — post/pre tool hooks carry `session_id`, exact result, and correlation metadata;
- `gateway/session_context.py:24-36,73-139` — task-local public session metadata bridge;
- `run_agent.py:2762-2812` — internal thread-safe steer queue and its tool-result drain semantics;
- `gateway/platforms/webhook.py:660-789` — unique delivery IDs intentionally create independent webhook sessions;
- `website/docs/user-guide/features/cron.md:201-224,301-332` — fresh cron sessions and thread-preferred continuable deliveries;
- `web/main.py:612-908` — the current mutable Web route inventory used for scope.

The live Hermes docs at `https://hermes-agent.nousresearch.com/docs` were reachable during research; local checked-out docs/source were used for exact line-level verification.

## Implementation sequencing (not started)

### Phase A — synchronization substrate

- canonical state-vector builder and stable snapshot locking;
- plugin-private bounded snapshot/cursor storage;
- `pre_llm_call`, `post_llm_call`, `pre_tool_call`, and `post_tool_call` registration;
- `sync_meal_manager_state` native tool;
- configured Meal Planning target allowlist;
- prompt-injection and overflow protections.

### Phase B — inventory/catalog conflict safety

- `expected_updated_at` for Web structured edit/delete;
- current-record conflict response and accessible HTTP `409` UX;
- guarded inventory/catalog native mutations;
- legacy fridge routes mapped to the same change model.

### Phase C — all remaining mutable Web routes

- cross-process read-modify-write locks for dishes and history before notification work;
- one canonical history entity/schema and shared Web/native repository path, with migration rehearsal before enabling history writes;
- dishes add/update/delete;
- cooking history add/delete;
- compound cook → history + inventory stable-snapshot/transaction behavior;
- no mutable route may bypass state-vector/diff tests.

### Phase D — optional notification policy

- quiet-period proactive summaries only if requested;
- optional Web urgency control;
- silent watchdog for stuck pending state;
- no per-edit cron job creation.

### Phase E — optional Hermes-core proposal

Only if real use demonstrates that next-turn synchronization and mutation fences are insufficient.

## Acceptance criteria

### Awareness

- [ ] Every mutable Web route changes a covered canonical domain vector or is proven semantic no-op.
- [ ] A Web change made while the Meal Planning session is idle is detected at that same session's next turn.
- [ ] A missing/pruned diff baseline returns `full_refresh_required`; it never reports “no changes.”
- [ ] Multiple edits to one entity are coalesced with bounded output.
- [ ] Comments/free-text values are never promoted into instructions.
- [ ] Development and unrelated sessions receive no Meal Planning notices.
- [ ] Concurrent-topic E2E proves hook target metadata cannot leak between Telegram sessions.
- [ ] Interrupted/failed turns do not falsely acknowledge synchronization.

### Mutation and conflict safety

- [ ] A stale session cannot execute guarded meal-manager mutation tools before synchronization.
- [ ] A Web edit/delete with stale entity version returns `409` and performs no write.
- [ ] Agent and Web edits to different fields both survive.
- [ ] Agent and Web edits to the same field cannot silently overwrite each other.
- [ ] A Web change after sync, including one racing between `pre_tool_call` and handler execution, is rejected by under-lock token/version validation.
- [ ] Compound history/inventory operations cannot expose a falsely synchronized mixed snapshot.

### Resilience

- [ ] Correctness depends on authoritative state/vector, not successful sidecar receipt delivery.
- [ ] Crash/restart between state mutation and notice processing cannot hide a real state change.
- [ ] Cursor/snapshot files use atomic writes, restrictive permissions, bounded retention, and TTL cleanup.
- [ ] Corrupt cursor/cache state fails open to `full_refresh_required`, never to “already synchronized.”
- [ ] Hook read/cache failures inject a conservative sync-required notice; guarded handlers still fail closed if the hook is skipped or crashes.
- [ ] Cross-process deterministic race tests cover Web writes, sync reads, and agent mutations in every mutable domain.
- [ ] Dish/history repositories cannot lose concurrent Web/native updates before their synchronization notices are enabled.
- [ ] Web and native cooking history use one canonical schema/repository; neither surface can overwrite the other's representation.

### Web UX

- [ ] Conflict UI preserves unsaved values and shows current server values/fields.
- [ ] No automatic last-writer-wins retry occurs.
- [ ] Save confirmation explains next-turn Hermes awareness without claiming mid-turn push.
- [ ] Optional proactive notification is coalesced and clearly distinct from agent synchronization.
- [ ] Keyboard, focus, mobile, XSS, and console gates pass.

### Release

- [ ] Unit, integration, Web API, Chromium, cross-process, crash, and migration gates pass.
- [ ] Live same-session QA proves Web → next-turn awareness in Meal Planning.
- [ ] Live race QA proves stale mutation block and `409` behavior.
- [ ] Exact guarantee and non-guarantee are reported in Meal Planning.
- [ ] Independent fail-closed review passes.

## Product decisions still required

1. **Scope/order:** implement inventory/catalog first behind an incomplete feature flag, or implement every mutable Web domain before enabling notices?
2. **Notification policy:** next-turn only by default, with optional human Telegram summary?
3. **Mid-turn requirement:** is a possibly stale already-generating answer acceptable if mutation tools still fail safely?
4. **Target configuration:** exact topic allowlist versus “sessions that used a meal-manager tool.”
5. **Retention:** number/age of snapshots and diffs before `full_refresh_required`.
6. **Actor identity:** keep `web`/`native` only until Web authentication exists.
7. **Core proposal threshold:** what observed failure would justify Hermes gateway/session API work?
8. **History semantics:** preserve multiple cooking events as Web currently models, or intentionally keep one latest date per dish as native currently models?

## Non-goals

- Per-keystroke synchronization.
- One Telegram message or one cron job per click.
- Treating comments, names, or webhook payloads as agent instructions.
- Reaching into private GatewayRunner/AIAgent state from the plugin.
- CRDT/collaborative text editing.
- Web authentication unless separately approved.
- Claiming true mid-turn synchronization without a supported Hermes session-event API.
