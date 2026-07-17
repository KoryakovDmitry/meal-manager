# AUDIT-1 — Domain audit trail and meal occurrence history

**Статус:** discovery / design  
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
  "dish": "...",
  "portions_planned": 5,
  "status": "cooked",
  "planned_for": "2026-07-16",
  "created_at": "...",
  "updated_at": "...",
  "cooked_at": "...",
  "actual_yield_portions": null,
  "replacement_occurrence_id": null,
  "cook_event_id": "audit_..."
}
```

Названия и окончательный набор status должны быть проверены против shopping, repeat-week и leftovers semantics до schema freeze.

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

## Audit event contract — discovery target

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

Финальный persistence protocol должен доказать crash semantics. Простой `state write → best-effort JSONL append` запрещён: crash оставит изменение без аудита. Простой `event append → state write` также запрещён: журнал может ложно утверждать неслучившийся commit. Рассматриваемые варианты:

- operation journal с `prepared`/`committed` и recovery;
- transactional outbox, встроенный в canonical aggregate write;
- per-aggregate append-only history + global analytics projection;
- полноценный event sourcing — только если меньшие варианты не обеспечивают требования без несоразмерного риска.

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
- tuning events — отдельно решить, нужны ли raw learner updates или только deploy decisions.

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

## Open design decisions

1. Final crash-safe persistence pattern after architecture spike.
2. Whether plan schema stores closed occurrences inline or references a separate occurrence store.
3. Minimal actor identity model for a two-person household without unnecessary PII.
4. Payload redaction and size limits for recipe instructions/comments.
5. Retention/export policy and hash-chain/tamper-evidence requirement.
6. Whether status `skipped` and `cancelled` are distinct in household UX.
7. How to represent an occurrence moved across ISO weeks.
