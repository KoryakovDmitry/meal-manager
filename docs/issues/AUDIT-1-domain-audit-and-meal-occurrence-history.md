# AUDIT-1 — Domain audit trail and meal occurrence history

**Статус:** design accepted / implementation queued
**Приоритет:** следующий основной vertical slice  
**Инициатор:** production feedback по плану `2026-W29`

## Проблема

Текущий weekly plan хранит только настоящее содержимое сетки. После приготовления агент зарегистрировал блюда в cooking history, а затем отдельно вызвал `remove_meal_from_plan`. В результате план потерял сам факт, что блюдо было запланировано и приготовлено: Web показывает день как `свободно`, то есть не отличает завершённую occurrence от дня, где блюда не было никогда.

Это не только UX-дефект. Система теряет материал для аналитики:

- plan adherence: что запланировали, приготовили, перенесли, заменили или отменили;
- planned vs actual portions/yield;
- время между планом, покупкой и готовкой;
- leftovers и срок их жизни;
- повторяемость блюд и реальную нагрузку prep-day;
- причины изменений и ручных корректировок;
- историю inventory, recipes, shopping, prep и plan mutations.

## Подтверждённый production incident

Источник истины для расследования — production JSON, backup snapshots и реальные tool calls из Meal Planning.

1. `register_cooked_meal("рамен tanoshi soja caramel с тофу и овощами")` записал cook date `2026-07-15`.
2. Затем агент вызвал `remove_meal_from_plan(week="2026-W29", day="wed", meal_index=0)`.
3. Tool вернул удалённую occurrence: dish `рамен tanoshi soja caramel с тофу и овощами`, `portions=3`.
4. `register_cooked_meal("паста с томатным соусом, чечевицей и кабачком")` записал cook date `2026-07-17`.
5. Затем агент вызвал `remove_meal_from_plan(week="2026-W29", day="thu", meal_index=0)`.
6. Tool вернул удалённую occurrence: dish `паста с томатным соусом, чечевицей и кабачком`, `portions=5`.
7. Current `2026-W29.json` содержит пустые `wed` и `thu`; Web корректно, но вводяще в заблуждение, показывает `свободно`.

Важное ограничение: historical backfill не должен выдавать догадки за исходные события. Восстановленные записи получают `backfilled=true`, provenance (`backup`, `tool_call`, `manual_confirmation`) и confidence. Полная история до внедрения audit trail по определению неполна.

## Дополнительный найденный дефект

Cooking history уже расходится между поверхностями:

- native `JsonHistoryRepository` хранит `{dish_name: latest_date}` и перезаписывает предыдущие готовки того же блюда;
- Web `/api/history` читает и пишет envelope `{history: [events...]}`;
- модели не являются одним canonical event store и не могут честно поддерживать повторные cooking occurrences или общую аналитику.

AUDIT-1 должен устранить это расхождение, а не добавить третью историю.

## Продуктовые принципы

1. **Никаких silent disappearances.** Приготовление, перенос, замена, пропуск и отмена — lifecycle transitions, не удаление строки.
2. **Occurrence важнее названия блюда.** Одинаковое блюдо в разные дни/недели — разные stable meal occurrence IDs.
3. **Current state и history разделены.** Текущие JSON projections остаются быстрыми read models; audit/event records объясняют, как они получились.
4. **Corrections are events.** Undo не удаляет историю, а создаёт compensating/correction event и новую актуальную проекцию.
5. **Native и Web используют одну domain command path.** Нельзя сохранять разные history schemas.
6. **Analytics не строится из Telegram transcript.** Transcript/prod backups допустимы только для явно помеченного migration backfill.
7. **Audit completeness измерима.** Каждая мутация имеет operation ID и committed audit record либо recovery state; best-effort logging недостаточен.
8. **Безопасный payload.** Secrets/credentials не журналируются; большие free-text snapshots и инструкции требуют redaction/size policy.

## Meal occurrence model — требуемый vertical slice

Каждый `MealEntry` получает stable `occurrence_id` и lifecycle:

- `planned` — входит в будущий план и shopping demand;
- `cooked` — приготовлено, остаётся в сетке и больше не создаёт будущий shopping demand;
- `skipped` — не приготовлено в этот раз;
- `cancelled` — сознательно удалено из намерения;
- `moved` — исходная occurrence закрыта ссылкой на новую occurrence/date;
- `substituted` — исходная occurrence связана с фактически приготовленной заменой.

Минимальные поля:

```json
{
  "occurrence_id": "mealocc_...",
  "root_occurrence_id": "mealocc_...",
  "predecessor_occurrence_id": null,
  "dish": "...",
  "portions_planned": 5,
  "status": "cooked",
  "planned_for": "2026-07-16",
  "revision": 2,
  "created_at": "...",
  "updated_at": "...",
  "status_changed_at": "...",
  "cooked_at": null,
  "cooked_on": "2026-07-17",
  "cooked_time_precision": "date",
  "actual_portions": null,
  "actual_yield_portions": null,
  "replacement_occurrence_id": null,
  "cook_event_id": "cook_...",
  "leftover_lot_ids": [],
  "provenance": null
}
```

Набор status подтверждён проверкой shopping, repeat-week и leftovers semantics. `planned` — единственный demand-bearing status. Move/substitution не переписывают исходную строку: terminal source остаётся на исходном дне и ссылается на новую successor occurrence; вся цепочка объединяется через `root_occurrence_id`. Это поддерживает переносы между ISO-неделями без потери первоначального намерения.

`planned_for` — household-local calendar date, audit timestamps — UTC RFC3339. Если legacy evidence содержит только дату, миграция записывает `cooked_on` и `cooked_time_precision="date"`, оставляя `cooked_at=null`; точное время не выдумывается. `actual_portions` означает реально поданные/съеденные порции при регистрации, `actual_yield_portions` — полный выход партии; ни одно значение не выводится автоматически из plan portions.

### Поведение Web

- `cooked`: блюдо остаётся на своём дне, получает заметный status badge/check, subdued completed style и timestamp;
- `moved/substituted/cancelled/skipped`: остаётся доступным в timeline/деталях; сетка показывает понятный status, а не `свободно`;
- настоящий empty day показывается как `свободно` только если у дня нет ни active, ни historical occurrence;
- карточка открывает per-occurrence timeline: planned → edited/moved → cooked/corrected;
- фильтр недели позволяет показать active, completed и all.

### Поведение shopping

Только demand-bearing statuses участвуют в будущих покупках. `cooked/cancelled/skipped` не создают demand. `moved` переносит demand ровно в surviving occurrence. `substituted` закрывает исходную demand и использует фактическую replacement occurrence. Повторная генерация shopping не удаляет historical basis events.

### Cooking command

`register_cooked_meal` должен принимать/разрешать конкретный `occurrence_id` (или fail closed при неоднозначности), атомарно/в recovery-safe workflow:

1. записать cooking occurrence;
2. перевести planned meal occurrence в `cooked`;
3. выполнить inventory/prep consumption;
4. создать leftovers/yield observation при наличии данных;
5. записать committed audit operation;
6. пересчитать shopping projection.

Регистрация блюда вне плана остаётся допустимой как unplanned cooking occurrence, но явно помечается `plan_occurrence_id=null`.

## Audit event contract

Нужен единый typed append-only журнал минимум с полями:

```json
{
  "schema_version": 1,
  "event_id": "audit_...",
  "operation_id": "op_...",
  "sequence": 1,
  "occurred_at": "UTC ISO-8601",
  "committed_at": "UTC ISO-8601",
  "event_type": "plan.meal.cooked",
  "entity_type": "meal_occurrence",
  "entity_id": "mealocc_...",
  "actor": {"kind": "user|agent|system", "id": null},
  "surface": "native|web|migration|recovery",
  "correlation_id": null,
  "causation_id": null,
  "payload": {},
  "before_hash": null,
  "after_hash": null,
  "backfilled": false,
  "provenance": null
}
```

Persistence decision принят после трёх независимых read-only design-аудитов: **file-backed transactional operation journal с immutable terminal markers** является canonical proof, а monthly JSONL — только rebuildable redacted analytics projection. Простой `state write → JSONL append` запрещён: crash оставит изменение без аудита. `event append → state write` запрещён: журнал может ложно утверждать неслучившийся commit. Полный event sourcing и SQLite migration сейчас несоразмерны; SQLite пересматривается только по измеренному contention/event volume.

Canonical layout под injectable data root:

```text
data/audit/
  .txn.lock
  transactions/YYYY-MM/<tx_id>/
    prepare.json
    targets/000.before
    targets/000.after
    commit.json | abort.json | conflict.json
  events/YYYY-MM.jsonl
  events/YYYY-MM.checkpoint.json
  retention.json
```

Transaction protocol:

1. взять global re-entrant cross-process `audit/.txn.lock`;
2. recovery всех non-terminal transactions;
3. взять affected domain locks в едином порядке;
4. strict-load, OCC/business validation и расчёт exact before/after bytes;
5. записать before/after blobs, затем durable `prepare.json` с whitelist-relative target paths и SHA-256;
6. применить atomic replaces/unlinks;
7. перечитать и проверить все after hashes;
8. создать immutable `commit.json` через exclusive create и fsync;
9. экспортировать redacted events в JSONL idempotently by `event_id`;
10. вернуть success только после durable commit marker.

JSONL export failure после `commit.json` не отменяет mutation: exporter восстанавливает projection из committed transaction directories. Recovery сравнивает каждый target с before/after fingerprints: all-after → commit; all-before → abort; mixture → restore all-before и abort; unknown fingerprint → `conflict.json` и fail-closed block последующих mutations. Recovery запускается at startup и перед каждой mutation; она idempotent.

Global household-scale serialization признана приемлемой. Domain lock order фиксируется один раз и проверяется механически. History, prep, tuning и durable DII writes должны перейти с process-local locks на shared cross-process `JsonFileLock`; DII disk snapshot становится authoritative, memory — cache.

## Покрытие «везде»

Audit completeness matrix должна включать:

- weekly plans и meal occurrences;
- cooking/yield/leftovers;
- inventory/catalog identities, quantities, availability, merge/replenish;
- recipes и cooking instructions;
- prep definitions, production и consumption;
- shopping manual requests, receipt reservations/completions, cost/trips;
- plan status и deletion;
- history corrections/undo;
- Web CRUD и native handlers;
- migrations/recovery/admin repair;
- tuning events — raw learner updates остаются отдельным rebuildable child transaction; deployment decisions и source cook event журналируются обязательно.

Mechanical audit текущего дерева выявил **38 native mutating handlers** и **19 mutating Web routes**. Release gate обязан auto-discover обе группы, сравнивать их с machine-readable mutation registry и падать при появлении неинструментированного writer. Прямой public-surface вызов repository save/remove в обход shared mutation command запрещается coverage test.

Наивысший риск представляют compound workflows: cooking (`history + inventory + prep`), prep production (`inventory + prep`), recipe deletion, DII finalization и shopping receipt (`reservation + inventory + completion + plan projection`). Они становятся одной transaction или явно связанными parent/child transactions. Tuning остаётся rebuildable child transaction, caused by committed cook event, и не блокирует core cook commit.

## Read surfaces

Native:

- `list_audit_events` с filters `entity_type`, `entity_id`, `event_type`, period, actor/surface, operation ID;
- `get_entity_history`;
- plan response включает occurrence lifecycle, но не бесконечный полный audit payload.

Web:

- общий раздел «История изменений»;
- timeline в карточке plan/meal/product/recipe;
- фильтры и pagination/cursor;
- экспорт JSON/CSV для аналитики;
- human-readable event labels без утраты typed payload.

## Analytics foundation

Первичные метрики после появления качественных событий:

- planned vs cooked adherence;
- moved/substituted/skipped frequency;
- planned portions vs actual yield and leftovers;
- dish repetition and time-to-repeat;
- inventory turnover/waste/expiry corrections;
- shopping planned/received/unused;
- prep production→consumption latency;
- mutation source split (Web/native/system) и correction rate.

Analytics views являются derived read models и должны быть rebuildable from committed events plus canonical snapshots.

## Phased delivery

### AUDIT-1A — foundation + plan/cooking vertical slice

- schema/protocol operation journal;
- stable meal occurrence IDs/statuses;
- canonical cooking occurrence history;
- native/Web shared command;
- Web completed status instead of disappearance;
- W29 provenance-marked backfill;
- audit read API/tool.

### AUDIT-1B — all mutating writers

- inventory, recipes, prep, shopping, plan CRUD/status/delete;
- shared audit context propagation across native/Web;
- completeness test that mechanically enumerates writers;
- per-entity timelines.

### AUDIT-1C — analytics and exports

- rebuildable projections;
- dashboards/filters;
- JSON/CSV export;
- retention/compaction policy without deleting canonical committed events.

## Acceptance criteria

- Cooking a planned meal leaves the row in the same day with `cooked` status.
- A truly untouched empty day remains distinguishable from cooked/cancelled/moved history.
- Repeated cooking of the same dish creates separate occurrences; no date overwrite.
- Shopping counts only demand-bearing occurrences and does not regress after status transitions.
- Native/Web produce identical event and current-state semantics.
- Crash/fault at every journal/state boundary recovers to either committed state+event or no state+noncommitted attempt; never committed state without auditable recovery evidence.
- Concurrent Web/native mutations serialize/OCC correctly and receive distinct monotonic operation/event identities.
- Undo/correction preserves original event.
- Corrupt audit/journal fails closed for mutations but read-only current state has an explicit degraded-audit signal rather than fabricated completeness.
- W29 backfill restores only evidence-supported occurrences and labels provenance/confidence.
- Full unit/integration/Web/Chromium, migration rehearsal, independent review, locked backup, coordinated rollout and live analytics QA pass.

## Design decisions after independent audit

1. Crash-safe persistence: prepared transaction directory + before/after fingerprints + immutable commit/abort/conflict marker; JSONL derived only.
2. Plan schema v2 stores active and closed occurrences inline so the weekly grid remains historically truthful; cross-week successor links use immutable IDs.
3. New IDs are opaque random stable IDs. Migration-only IDs are deterministic hashes of canonical JSON input and persisted once; names, dates and indexes are never runtime identity.
4. `planned`, `cooked`, `skipped`, `cancelled`, `moved`, `substituted` remain distinct domain states. Public “remove meal” becomes cancellation; physical purge is not a normal API/tool action.
5. Repeat-week copies effective leaf occurrences with fresh IDs and resets them to `planned`; source tombstones and actual/yield/history fields are not copied.
6. Cooking history becomes one canonical stable-ID occurrence collection. Repeated cooks append separate events; undo retracts/corrects by cook event ID and never deletes by dish name/list index.
7. Actor model is minimal: `user|agent|system|unknown`, surface and operation are recorded, identifiers are pseudonymized only when grouping is needed. Raw IP, headers, cookies, tokens, prompts and stack traces are excluded.
8. Long-lived event payloads are allowlisted. Recipe instructions/comments are represented by changed flag, bounded length and/or digest rather than unrestricted text. Full recovery blobs use `0600` files/`0700` directories and shorter retention.
9. Current suggested retention defaults are 30 days for prepared/aborted blobs, 90 days for committed before/after blobs, and configurable long-lived redacted event segments. Conflict transactions are never auto-deleted. Final household retention duration and optional monthly hash chain remain release-policy decisions, not schema blockers.
10. W29 backfill restores only evidence-supported Wed ramen (`planned=3`, cooked date `2026-07-15`) and Thu pasta (`planned=5`, cooked date `2026-07-17`) as `cooked`, `backfilled=true`, high-confidence provenance. Unknown exact time, actual portions/yield and leftovers remain null. Monday/Tuesday remain genuinely empty; Saturday/Sunday remain planned.

## Additional release blockers found by writer audit

- native/Web history schemas can overwrite each other today;
- history, prep, tuning and DII lack universal cross-process locking;
- cooking can commit history+inventory and then fail prep without rollback;
- prep production can consume inventory and fail before prep save;
- native and Web cooking differ in validation, prep consumption and tuning;
- whole-plan Web deletion is a hard unlink without restore;
- recipes and prep items still lack stable IDs/revisions;
- permissive prep/history/day parsing can silently drop malformed rows on a later save;
- native and Web plan lifecycle edit rules differ;
- migration must preserve shopping receipt tombstones, inventory IDs/aliases/stock cycles and both legacy history shapes.

None of these may be hidden behind “audit complete”. AUDIT-1 is complete only when every successful public mutation has durable committed evidence and every failed/OCC/no-op path has the documented absence/outcome semantics.
