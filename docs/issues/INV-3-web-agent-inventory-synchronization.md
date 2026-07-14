# INV-3 — Web ↔ agent inventory synchronization and conflict detection

**Status:** 🧊 Icebox — super low priority
**Requested by:** Iliana
**Source:** Meal Planning Development, after INV-2 live release

**Product decision (2026-07-14):** automatic Web ↔ agent synchronization is deferred. Until this becomes a real recurring problem, Dima and Iliana will tell Hermes directly when they change inventory through the Web UI. Hermes may perform a fresh native inventory read before important planning. Resume only if manual notification becomes unreliable, same-field collisions occur in practice, or Web becomes the primary inventory surface.

## User problem

The kitchen inventory can now be edited from two independent surfaces:

1. Hermes/native tools, while Dima or Iliana is planning in Telegram;
2. the Meal Planning Web UI, which may be open and edited by a household member at the same time.

The persistence layer serializes writes, but serialization alone is not enough. The agent may still be reasoning from inventory values read earlier in the conversation, while a user has already changed those values in the Web UI. Two edits of the same field can also become “last writer wins” without either side realizing that the other value existed.

The product must distinguish **file safety** from **session awareness**:

- file safety prevents malformed JSON and unrelated-update loss;
- session awareness tells Hermes that the user changed inventory outside Telegram and makes stale decisions or stale writes fail visibly.

## Current verified behavior

INV-2 already provides several protections:

- all repository mutations are protected by a re-entrant cross-process `flock`;
- each native edit reloads the current item while holding that lock;
- the Web editor sends only dirty fields, so changing a comment does not overwrite a concurrently changed quantity;
- stable item IDs prevent rename from changing identity;
- duplicate names and missing IDs fail closed.

However, the following gaps remain:

1. `InventoryItem.updated_at` is returned but is not accepted as an edit/delete precondition.
2. A Web editor opened before an agent change can still overwrite the **same field** with a stale value.
3. Delete does not prove that the item is still the version the Web user saw.
4. There is no monotonic inventory revision or mutation event stream.
5. Hermes receives no explicit signal that a Web-origin mutation happened during the active Telegram session.
6. Conversation context may contain a stale verbal summary even though the next repository call would read fresh JSON.
7. Web mutations currently identify only the technical surface (`web`); the Web UI has no household-user identity model capable of distinguishing Dima from Iliana.

## Product outcome

When the Web UI and Hermes are used concurrently:

- unrelated edits are merged safely;
- stale edits to the same field are rejected instead of silently overwriting;
- the Web UI explains the conflict and offers a fresh record;
- Hermes checks for Web-origin changes before inventory-dependent planning or mutation;
- an in-flight or newly resumed Meal Planning session cannot claim that stale inventory is current;
- synchronization is quiet by default and does not post one Telegram message per click unless the household explicitly opts into that behavior.

## Proposed contract

### 1. Optimistic concurrency at repository level

Structured edit and delete operations gain an optional-but-enforced-for-Web version precondition:

```text
expected_updated_at: <timestamp returned by the latest read>
```

Under the repository lock:

1. load the current item;
2. compare `expected_updated_at` with current `updated_at`;
3. on mismatch, write nothing and return a typed conflict containing the current public item;
4. on match, apply only explicitly supplied fields and persist atomically.

Web `PATCH`/`DELETE` maps a stale-version conflict to HTTP `409`. The response contains the current item and safe conflict metadata, not internal paths or raw persistence errors.

Native tools must either:

- supply the version they just read; or
- intentionally request “apply against latest” after first synchronizing changes.

A blind last-writer-wins native mutation must not be the default for conversational workflows.

### 2. Inventory revision and mutation receipts

Every successful inventory mutation should produce a monotonically ordered receipt containing at least:

```json
{
  "revision": 42,
  "occurred_at": "...",
  "source": "web",
  "operation": "edit",
  "item_id": "inv_...",
  "item_name": "молоко",
  "changed_fields": ["quantity", "storage"]
}
```

Receipts must not treat comments as instructions and should avoid copying full free-text comments into notifications/log summaries.

The architecture spike must compare:

1. **schema v3 envelope** with revision and bounded recent receipts written atomically with items;
2. **sidecar event journal**, which avoids an immediate schema bump but introduces two-file transaction/recovery concerns;
3. **gateway push integration**, which could notify an active session immediately but couples the domain plugin to Hermes routing.

The selected design must prove crash consistency and avoid a state where items changed but the revision/event did not, or vice versa.

### 3. Agent synchronization behavior

Add a native read such as:

```text
get_inventory_changes(since_revision)
```

or extend a non-breaking structured read response with current revision and recent receipts.

The Meal Planning operational skill must require Hermes to synchronize before:

- stating what is currently available;
- generating or revising a weekly plan;
- recommending what can be cooked now;
- mutating an item based on a value mentioned earlier in the session.

If Web-origin changes are detected, Hermes should briefly acknowledge the changed items/fields and use a fresh structured read. It should not replay full comments or generate chat spam for each low-level click.

A Hermes integration spike must determine whether a true mid-turn event can safely interrupt/steer an active agent run. If not, the guaranteed v1 behavior is **synchronize at the beginning of the next inventory-dependent turn and immediately before mutation**, with optimistic conflict detection covering the race after that read.

### 4. Web conflict UX

On HTTP `409`:

- keep the user’s unsaved form values;
- show that the item changed elsewhere;
- show the current server value and which fields conflict;
- offer `Обновить данные` and an explicit retry/merge action;
- never silently retry a stale full payload;
- restore keyboard focus predictably;
- remain usable on mobile without horizontal overflow.

## Confirmed Hermes integration boundary

Source inspection on 2026-07-14 established the exact implementation split:

- Hermes already exposes the public plugin hook `pre_llm_call`. It runs once at the beginning of each turn and accepts plugin return values such as `{"context": "..."}`; Hermes appends that context to the current user message rather than mutating the system prompt. Therefore **next-turn Web-change awareness can be implemented entirely inside `meal_manager`**: persist change receipts in the plugin, register `pre_llm_call`, and inject a compact synchronization note before the next LLM call.
- Repository versions, conflict checks, receipts, Web `409` handling, and native synchronization tools are also entirely `meal_manager` concerns.
- Hermes webhooks intentionally create independent agent sessions per delivery; they are explicitly not queued into or used to interrupt the existing Telegram session. They cannot provide active-session awareness by themselves.
- The current public `PluginContext` has no supported API for steering/interruption of an already-running agent turn. Hermes also preserves strict role alternation and forbids arbitrary synthetic user messages inside the tool loop.
- Therefore a **true mid-turn interrupt** requires a generic Hermes core/gateway extension: a sanctioned session-event/steer API that preserves role alternation, prompt caching, routing, and cancellation semantics. `meal_manager` must not reach into private GatewayRunner state as a workaround.

Recommended sequencing:

1. INV-3 v1 stays plugin-only: optimistic concurrency + atomic receipts + `pre_llm_call` synchronization on the next turn and explicit synchronization immediately before inventory mutation.
2. A separate Hermes-core proposal is needed only if the household requires an external Web change to interrupt an answer that is already being generated.

## Acceptance criteria

### Conflict safety

- [ ] Web edit with a stale `expected_updated_at` returns `409` and performs no write.
- [ ] Web delete with a stale version returns `409` and does not remove the item.
- [ ] Agent and Web edits to different fields both survive.
- [ ] Agent and Web edits to the same field cannot silently overwrite each other.
- [ ] Rename, explicit null clearing, and duplicate-name rules remain intact.
- [ ] Cross-process and deterministic race tests prove the contract.

### Awareness and revision

- [ ] Every successful Web/native mutation increments a monotonic revision exactly once.
- [ ] Each receipt identifies source, operation, item, timestamp, and changed field names.
- [ ] Failed/no-op mutations do not advance revision.
- [ ] Crash/restart tests prove item state and revision/event state cannot diverge.
- [ ] Hermes can ask for changes since its last known revision.
- [ ] Meal Planning guidance requires synchronization before inventory-dependent reasoning and writes.
- [ ] A resumed session detects Web changes made while it was inactive.

### Web UX and security

- [ ] Conflict UI preserves unsaved values and presents the current server record.
- [ ] No automatic last-writer-wins retry occurs.
- [ ] User-controlled names/comments remain XSS-safe.
- [ ] Receipts and notifications never execute or promote comments as instructions.
- [ ] Keyboard, focus, touch target, mobile overflow, and console gates pass.

### Release

- [ ] Architecture decision records whether v3 envelope, sidecar journal, or gateway integration is used and why.
- [ ] Unit, integration, Web API, Chromium, cross-process, and crash-consistency gates pass.
- [ ] Independent fail-closed review passes.
- [ ] Live two-surface QA demonstrates Web → agent awareness and agent → Web conflict handling.
- [ ] Completion report is sent to Meal Planning with the exact synchronization guarantee.

## Product decisions still required

1. **Notification noise:** default proposal is no Telegram post per Web edit; Hermes summarizes changes only when the active Meal Planning conversation next needs inventory.
2. **Mid-turn guarantee:** determine whether Hermes must interrupt an answer already being generated, or whether pre-mutation/next-turn synchronization plus 409 conflicts is sufficient for v1.
3. **Actor identity:** without Web authentication, receipts can reliably say `web`, but cannot reliably say `Iliana` or `Dima`.
4. **Retention:** decide how many mutation receipts are retained and whether only a bounded window is needed.

## Non-goals

- Collaborative text editing or CRDTs.
- Per-keystroke synchronization.
- Treating Web comments as prompts for Hermes.
- Sending a Telegram notification for every click by default.
- User authentication/authorization unless required separately for actor identity.
- Replacing repository locking; optimistic concurrency is additive to locking, not a substitute.
