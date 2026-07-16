# Meal Manager — Product & Engineering Board

> Рабочий roadmap семейной системы планирования питания для Димы и Илианы.
> Архитектурные контракты: [`ARCHITECTURE.md`](ARCHITECTURE.md).
> Операционные правила разработки: [`AGENTS.md`](AGENTS.md) и [`CLAUDE.md`](CLAUDE.md).

## Как читать board

| Статус | Значение |
|---|---|
| ✅ Done | Реализовано, протестировано и опубликовано в `main` |
| 🔨 Doing | Активная промежуточная issue или текущая фаза |
| 📐 Ready | Контракт понятен, можно начинать TDD-реализацию |
| 🧭 Discovery | Нужны продуктовые решения или дополнительные данные |
| 📋 Backlog | Зафиксировано, но пока не приоритизировано |

## Продуктовые ориентиры

- Домохозяйство: два человека, небольшая кухня и холодильник, Люксембург.
- Питание plant-forward с допустимым гибридным белком; без тяжёлых смесей специй и высокой остроты.
- Ориентир тарелки: около ½ овощей, ¼ зерновых и ¼ белка.
- Один недельный план хранится в `data/plans/YYYY-WXX.json`.
- План содержит ссылки на блюда, а не копии рецептов.
- Lifecycle плана: `draft → approved → active → archived`; менять можно только draft.
- Бюджет мягкий: ориентировочно €100 на поездку и €150 на неделю, без блокировки approval.
- Web — личная поверхность просмотра Димы с ограниченными ручными действиями; meal-planning domain flow остаётся в plugin tools.

---

## 🔨 ACTIVE INTERMEDIATE ISSUES

### INV-5 — Product categories across kitchen inventory and catalog 🔨

**Проблема.** Текущий кухонный запас и «Каталог продуктов» смешивают обычные ингредиенты, заранее подготовленные полуфабрикаты и уже готовую еду. По названию или комментарию это не всегда очевидно, а отфильтровать запас по степени готовности невозможно.

**Продуктовый контракт.** Каждая catalog identity получает обязательную категорию: `product` («Обычный продукт»), `prep` («Заготовка») или `ready_meal` («Готовая еда»). Заготовка — домашний или покупной полуфабрикат/компонент, подготовленный заранее для последующей готовки. Категория описывает продукт, но сама по себе не создаёт domain `PrepItem`, не меняет наличие и не выполняет cooking flow. Все старые записи мигрируют в `product` без изменения stable ID, количества, хранения, сроков, комментариев и availability.

**Каталог и identity.** Категорию можно назначить позиции `in_stock`, `out_of_stock` или `recipe_only`. Для recipe-only продукт получает persisted catalog identity, но не считается ранее закупленным и не переходит ложно в `out_of_stock`; replenishment переводит его в обычный stocked lifecycle с сохранением identity и категории. Recipe-only metadata, которая больше не встречается в рецептах и никогда не была в запасе, может оставаться скрытой persisted identity для восстановления категории при повторном появлении.

**Native/API contract.** `add_inventory_item`, `edit_inventory_item` и `replenish_product` принимают category; отдельный `set_product_category` назначает категорию любой видимой catalog position, включая recipe-only. `list_inventory_items` и `list_product_catalog` возвращают category, а catalog list поддерживает category filter. Все mutations проходят canonical `InventoryItem` и общий cross-process lock. Web category/replenish/edit/delete используют OCC: persisted identity обязана передавать `product_id + expected_updated_at`, а `name + expected_updated_at:null` разрешено только для ещё отсутствующей ephemeral recipe-only identity; stale/ABA mutation отвечает `409` или contract `400` без записи. Native tools сериализованы тем же lock и выражают последнее явное намерение агента без Web-style version token.

**Web UX.** В форме добавления, editor и replenish modal появляется selector категории. Карточки запаса и каталога получают текстовый цветной badge, доступное нецветовое обозначение и визуальный accent; оба раздела фильтруются по категории. В каталоге категорию можно менять без replenishment. Фильтры и controls имеют labels, keyboard/focus semantics и touch targets не меньше 44 px; пользовательский текст остаётся XSS-safe.

**Acceptance gate.** RED→GREEN domain/migration tests для schema v4 и legacy/v2/v3; fail-closed lifecycle invariant `available ⇒ ever_stocked`; recipe-only identity/status/replenish regressions; native schema parity; stale category/replenish byte-for-byte no-write; двухпроцессные materialize/materialize и materialize/replenish races с одним winner; Web API и Chromium filtering/badge/modal/focus/XSS tests; production migration rehearsal на backup; полный unit/integration/Web gate; независимое GO; coordinated Web/Gateway rollout и disposable live QA.

### PLAN-4 — Web editing and deletion of weekly plans 🔨

**Проблема.** Раздел «Планы» показывает полноценную структуру недели, но оставляет её read-only и отправляет пользователя обратно в `meal_manager` даже для простого исправления порций, замены/удаления блюда или удаления ошибочного draft. Это делает личную Web-поверхность непоследовательной относительно уже доступного ручного CRUD рецептов и кухонного запаса.

**Продуктовое решение.** Web получает ограниченный, явный редактор содержимого `draft`: добавить блюдо в день, заменить блюдо или число порций, удалить блюдо. Утверждённые/активные/архивные планы остаются read-only по содержимому согласно lifecycle. Удалить после отдельного необратимого подтверждения можно план любого статуса — это сознательное действие владельца, а не status transition.

**Контракт данных и конкурентность.** Web использует canonical `WeekPlan`/`MealEntry` и общий `JsonPlanRepository`, а не вводит вторую mutation schema. Все plan writers получают общий cross-process `flock`; Web GET возвращает semantic SHA-256 version, а POST/PATCH/DELETE проверяют `expected_version` внутри того же write lock. При stale версии API отвечает `409 plan_conflict` с authoritative plan/version, UI обновляет данные и не выполняет silent retry. Любое изменение блюд сбрасывает derived shopping snapshot, как native handlers.

**UX и безопасность.** Редактор использует каталог рецептов, целые portions ≥ 1, доступный modal/focus trap и кнопки не меньше 44×44 px. Динамический текст экранируется. Удаление блюда и всего плана требует подтверждения; конфликт или ошибка не должны менять файл. Удаление плана не редактирует inventory, history или recipes.

**Acceptance gate.** RED→GREEN API regressions для add/edit/remove/delete, stale-write и non-draft rejection; реальный cross-process lock probe; Chromium-проверка modal semantics, version propagation, удаления и XSS; полный unit/integration/Web gate; независимое review; live API/browser QA после restart `meal-web`.

## 📐 READY INTERMEDIATE ISSUES

Готовых промежуточных issues сейчас нет.

## ✅ COMPLETED INTERMEDIATE ISSUES

### REC-1 — Cooking instructions in recipe cards ✅

**Результат.** `Dish` получил optional multiline `instructions` («Как готовить») с canonical trim/clear и лимитом 20 000 Unicode code points. Агент умеет добавлять инструкции вместе с рецептом, читать их через `get_dish_recipe`, менять/очищать через `set_dish_instructions` и удалять рецепт целиком существующим `delete_dish`. Web показывает отдельный блок в карточке, ищет по инструкциям и редактирует их в accessible textarea.

**Надёжность.** Native и Web writers используют общий re-entrant cross-process file lock; Web create/update/delete защищены semantic catalog-version OCC. Stale create/update/delete не меняют файл. Legacy и unrelated malformed rows сохраняются; пользовательский текст XSS-safe. Gate: 260 unit, 322 integration, Web API и реальный Chromium; три fail-closed review cycles завершились `GO`.

Подробный ticket: [`docs/issues/REC-1-cooking-instructions.md`](docs/issues/REC-1-cooking-instructions.md).

### INV-3 — Web ↔ agent state synchronization and conflict detection ✅

**Результат.** Exact-topic `pre_llm_call` передаёт authoritative inventory token/count metadata без свободного текста, а `sync_meal_manager_state` выполняет обязательное актуальное чтение для inventory-dependent запросов. Web PATCH/DELETE защищены монотонным `updated_at`, under-lock precondition и понятным HTTP `409` без silent overwrite. Полный gate, два независимых ревью, post-restart Gateway/Web/native QA и exact/off-target проверки пройдены; release `76f8e33` опубликован.

**Явная граница.** Покрыты текущий запас и persisted in-stock/out-of-stock identities. Recipe-only projection, dishes/history, native mutation fence и mid-turn push остаются следующими фазами; DATA-1 остаётся prerequisite.

Подробный ticket: [`docs/issues/INV-3-web-agent-inventory-synchronization.md`](docs/issues/INV-3-web-agent-inventory-synchronization.md).

### INV-4 — Product catalog and replenishment ✅

**Результат.** Schema v3 сохраняет product identity после приготовления, ручного удаления и очистки запаса. Web и native tools дают симметричные catalog list/search/filter и replenish flows для `in_stock`, `out_of_stock` и `recipe_only`; stable ID сохраняется, а expiry/comment новой партии не наследуются. Полный gate, migration rehearsal, cross-process tests и live Web/native/cook QA пройдены на production; восстановлена запись `готовые маринованные куриные ножки`, 11 pcs, fridge.

Подробный ticket: [`docs/issues/INV-4-product-catalog-and-replenishment.md`](docs/issues/INV-4-product-catalog-and-replenishment.md).

### INV-2 — Structured kitchen inventory item model ✅

**Результат.** Кухонный запас мигрирован на versioned `InventoryItem` (`schema_version: 2`) со stable ID, quantity/unit, package count, storage, expiry и comment. Нативный и Web CRUD, compatibility projection, межпроцессная блокировка, fail-closed migration и accessible expiry UX прошли полный gate, независимое ревью и live-проверку на 28 позициях.

Подробный ticket: [`docs/issues/INV-2-structured-inventory-item-model.md`](docs/issues/INV-2-structured-inventory-item-model.md).

### INV-1 — Edit/rename kitchen inventory item ✅

**Результат.** Атомарное переименование доступно через `rename_fridge_item` и отдельный accessible inline edit в Web UI; stable ID и structured metadata сохраняются, collision/not-found/no-op обрабатываются недеструктивно. Native/Web live QA завершён.

Подробный ticket: [`docs/issues/INV-1-edit-fridge-item.md`](docs/issues/INV-1-edit-fridge-item.md).

---

### TOOLS-1 — Plugin tool schema envelope ✅

**Проблема.** Все 34 handler-схемы передавались в Hermes как body JSON Schema без обязательной function-tool оболочки `name/description/parameters`. В результате модель видела инструменты без аргументов; `update_fridge_inventory` нельзя было вызвать с обязательными `action` и `ingredients`.

**Результат.** Единый registration boundary исправлен и покрыт регрессией на реальном `register(ctx)` seam. Полный gate и независимое fail-closed review пройдены; fix опубликован, gateway обновлён, а live registry подтвердил корректные object-valued `parameters` у всех 34 tools.

Подробный ticket: [`docs/issues/TOOLS-1-plugin-tool-schema-envelope.md`](docs/issues/TOOLS-1-plugin-tool-schema-envelope.md).

---

### UIX-1 — Web shell UX refresh ✅

**Проблема.** Горизонтальные вкладки занимают много места, плохо масштабируются и визуально делают приложение похожим на прототип. Во вкладке браузера нет узнаваемой иконки.

**Цель.** Сделать спокойную рабочую оболочку типа *Operate/Inspect*: навигация быстро считывается, но не конкурирует с содержимым.

**Scope**

- [x] Заменить горизонтальные вкладки вертикальным desktop sidebar.
- [x] Добавить сворачивание до icon rail и сохранять выбор в `localStorage`.
- [x] На mobile/tablet использовать скрываемый drawer.
- [x] Закрывать drawer по backdrop, `Escape`, выбору раздела и явной кнопке `×`.
- [x] Добавить локальный SVG favicon и совпадающий app mark.
- [x] Улучшить hierarchy, spacing, active/hover/focus states и empty states.
- [x] Обеспечить все interactive targets не меньше 44×44px и `aria-current` для активного раздела.
- [x] Сделать recipe modal семантическим, удерживать focus внутри, inert-ить фон и восстанавливать focus после всех close paths.
- [x] Сделать строки weekly plans нативными keyboard-operable buttons.
- [x] Рендерить persisted dish/ingredient values только как text, без подстановки user data в inline handlers.
- [x] Передавать `fridge_utility` через `/api/stats`: used items показывают recipe count, unused styling получает только zero-use item.
- [x] Сохранить семь существующих разделов, refresh actions и текущие API contracts.
- [x] Сохранить weekly-plan routes строго GET-only.
- [x] Выполнить desktop expanded/collapsed и mobile closed/open visual QA.
- [x] Исключить horizontal overflow и ошибки browser console.
- [x] Получить независимый fail-closed review текущего staged snapshot.
- [x] Commit/push, live restart и финальная проверка favicon/UI.

**Не входит в issue**

- новые meal-planning domain operations;
- прямая запись plan JSON из web;
- новый backend API;
- перенос управления системой из `meal_manager` tools в web.

Подробный ticket: [`docs/issues/UIX-1-web-shell-ux-refresh.md`](docs/issues/UIX-1-web-shell-ux-refresh.md).

---

## ✅ COMPLETED PHASES

### Foundation — Architecture, notes and repository web

**Результат.** Создана единая архитектурная база, зафиксированы бытовые ограничения и web-код перенесён внутрь repository.

- [x] Архитектурные решения и слои Handlers → Domain → DII → Repositories.
- [x] Meal-planning notes и сезонные заметки для Люксембурга.
- [x] Repository-level architecture и development board.
- [x] Web application размещён в `web/` и запускается через `meal-web.service`.
- [x] Старый отдельный `/home/dima-hermes/meal-web/` выведен из эксплуатации.

### Phase 1 — Prep items and dish dependencies ✅

**Цель.** Моделировать полуфабрикаты отдельно от готовых блюд и правильно учитывать их производство/расход.

**Domain contracts**

- Prep item содержит source ingredients, yield, unit, storage zone и remaining.
- `add_prep_item` только описывает сущность и не расходует продукты.
- `make_prep` расходует essential source ingredients.
- Новая партия **заменяет** `remaining` свежим yield, а не прибавляется к старому остатку.
- При готовке блюда расходуются указанные prep dependencies.

**Deliverables**

- [x] `PrepItem` model и JSON repository.
- [x] Поле `Dish.prep_depends` с backward-compatible загрузкой старых рецептов.
- [x] Tools: `add_prep_item`, `list_prep_items`, `delete_prep_item`, `make_prep`.
- [x] Интеграция prep consumption в `register_cooked_meal`.
- [x] Rollback/error coverage и unit/integration tests.

### Phase 2 — Weekly plans, lifecycle and history ✅

**Цель.** Создавать, обсуждать, утверждать, активировать, архивировать и повторять недельные планы без копирования recipe data.

**Domain contracts**

- ISO week ID и один JSON-файл на неделю.
- Каждый день содержит упорядоченный гибкий список блюд без жёстких breakfast/lunch/dinner slots.
- Нормализованные dish references, portions и optional day note.
- Lifecycle переходы последовательны; archived plan immutable.
- Повтор недели создаёт новый draft и заново оценивает текущие shortages.
- Любое изменение meals инвалидирует derived shopping snapshot.

**Deliverables**

- [x] `MealEntry`, `DayPlan`, `WeekPlan` и plan repository.
- [x] Tools: create/get/list/add/remove/status/repeat.
- [x] Path traversal, embedded-week mismatch и malformed persistence guards.
- [x] GET-only web history/detail view с HTML escaping.
- [x] Lifecycle, repeat/adaptation, persistence и XSS regression coverage.

### Phase 3 — Whole-week shopping and soft budget ✅

**Цель.** Получать объяснимый список покупок на неделю, честную partial cost estimate и разбиение на поездки без ложной точности.

**Quantity contracts**

- Базовая единица — cooking occurrence, пока recipes не содержат граммы/package quantities.
- Учитываются только essential ingredients.
- Одна flat fridge entry покрывает один aggregated use.
- Planned/depleted prep добавляет essential source ingredients одной новой партии.
- Unknown price остаётся unknown и не превращается в €0.

**Persistence and budget contracts**

- Shopping хранится как derived snapshot внутри `WeekPlan.shopping`.
- Fresh estimate удаляет stale item prices и все stale trip fields.
- Partial coverage возвращает известный subtotal, но budget status остаётся `unknown`.
- Complete estimate сравнивается с мягким weekly limit €150.
- Split выполняется после estimate и использует мягкий trip limit €100.
- Persisted shopping валидируется fail-closed: exact schemas, counts, sums, warnings, trip assignment, safe numeric bounds и non-empty trips.

**Deliverables**

- [x] `generate_shopping_list` — whole-week aggregation и prep expansion.
- [x] `estimate_plan_cost` — explicit prices, partial coverage и soft warning.
- [x] `split_shopping_list` — deterministic trips, unpriced/oversized reporting.
- [x] Read-only shopping/budget/trip view в weekly-plan detail.
- [x] Invalid UTF-8, huge integers, `NaN`/Infinity, stale state и contradictory payload guards.
- [x] Verification gate: **185 unit + 150 integration**, focused web tests, JS/Python syntax и independent review.

### SHOP-1 — Live plan-backed shopping and receipt reconciliation ✅

**Цель.** Сделать вкладку «Покупки» актуальной проекцией текущего недельного плана и безопасно связывать абстрактный запрос с точным купленным товаром. Детальный контракт: [`docs/issues/SHOP-1-live-shopping-and-receipt-reconciliation.md`](docs/issues/SHOP-1-live-shopping-and-receipt-reconciliation.md).

- [x] Web/native readers строят live projection из plan, recipes, prep, inventory aliases и manual requests; stale persisted snapshot явно маркируется.
- [x] Stable `shop_*`/`shopreq_*` IDs и категории `known_missing` / `abstract_request`.
- [x] Browser checkbox хранится только в `localStorage`, переживает повторный render и не вызывает inventory API.
- [x] Inventory schema v6 сохраняет aliases и отдельный stock cycle; generic recipe ingredient удовлетворяется и списывает exact product identity.
- [x] Agent receipt flow сохраняет durable exact-name reservation до inventory write, затем generic → exact alias и completion tombstone; post-crash conflicting retry отклоняется.
- [x] Manual requests входят в live и persisted budget snapshot; estimate/split отвергают stale snapshot.
- [x] Malformed shopping dependencies дают explicit stale/error либо fail closed, а не пустой «актуальный» список.
- [x] Финальный independent GO, production backup, commit/push, coordinated Gateway/Web restart и live QA: 15 live needs, no projection error/stale snapshot, inventory v5 semantic integrity.
- [ ] HOTFIX — повторная потребность после `купил → съел → снова нужно` получает новый occurrence-scoped `shop_*` ID и не конфликтует с tombstone прошлой покупки; RED/GREEN regression готов, release gate ожидается.

---

## 📐 READY / NEXT

### Phase 4 — Leftovers and household calibration

**Цель.** Перейти от предположения «две тарелки на человека» к данным конкретного домохозяйства: сколько реально приготовили, съели и осталось.

**Planned entities and tools**

- [ ] `record_leftovers` — записать remaining portions после готовки/приёма пищи.
- [ ] `get_leftovers` — показать доступные leftovers и возраст записи.
- [ ] Связать cooked occurrence с produced portions и leftovers.
- [ ] Автоматически учитывать leftovers при следующем планировании недели.
- [ ] Добавить safe expiry/consumed/discarded states без автоматического удаления данных.

**Решения перед реализацией**

- [ ] Определить: leftovers принадлежат dish, cooking event или конкретному week/day meal.
- [ ] Определить единицу: portions, containers или оба уровня.
- [ ] Определить ручной flow для «съели», «заморозили», «выбросили».
- [ ] Решить, как калибровать planned portions без скрытого автотюнинга.

**Acceptance criteria**

- Leftovers не списываются молча.
- История калибровки объяснима и undoable.
- Планирование использует leftovers до покупки новых ингредиентов, но показывает это явно.
- Старые plans и history остаются backward-compatible.

---

## 🧭 PLANNED / DEPENDENCY-BLOCKED

### Phase 5 — Weekly suggestion and ranking engine

**Цель.** Предлагать не одно блюдо, а сбалансированный draft недели с учётом fridge, prep, leftovers, истории, разнообразия, seasonality и бюджета.

**Planned capabilities**

- [ ] Candidate generation из catalog с hard compatibility filters.
- [ ] Ranking по availability, recency, seasonality, prep reuse и projected shopping cost.
- [ ] Soft diversity rule: избегать одного блюда более двух дней подряд без hard prohibition.
- [ ] Harvard Plate signal на уровне недели, а не отдельного блюда.
- [ ] Объяснение каждого выбора и альтернатив.
- [ ] Генерация только draft; approval всегда остаётся явным решением Димы/Илианы.

**Dependencies — Phase 5 не переходит в Ready, пока они не закрыты**

- Phase 4 leftovers/calibration.
- Более полный recipe catalog.
- Quantity/unit/package model вместо одних cooking occurrences.
- Ingredient classification и nutrition contract для Harvard Plate signal.
- Usable price-input contract: подтверждённая price DB либо explicit-price provider с coverage/confidence semantics.
- Seasonality data contract для Luxembourg hints.

---

## 🧭 DISCOVERY

### Receipt ingestion and price history

**Зачем.** Уйти от ручной explicit price map к реальным люксембургским ценам и видеть динамику бюджета.

- [ ] Upload flow для чеков от Илианы/Димы.
- [ ] OCR/parser с ручным подтверждением неоднозначных строк.
- [ ] Нормализация store product → ingredient/package.
- [ ] История unit/package prices с датой и магазином.
- [ ] Projected basket cost с указанием confidence и даты последней цены.
- [ ] Weekly/trip budget reports без превращения soft budget в blocker.

### Recipe quantities and nutrition

- [ ] Ingredient quantities/units/package conversion.
- [ ] Portions/yield на уровне recipe.
- [ ] Protein/fiber/vegetable coverage и Harvard Plate checks.
- [ ] Soup satiety signal: protein и energy density, а не только название категории.

---

## 📋 BACKLOG

### DATA-1 — Canonical domain models and shared Web/native persistence 📋

**Зачем.** Привести накопившиеся модели и persistence paths к одной архитектуре: один canonical model/repository/application-service path на домен, используемый и Web, и native tools. Не делать один гигантский универсальный JSON.

**Подтверждённый blocker.** Web/native history сейчас используют несовместимые представления (`{"history": [...]}` против `{dish: latest_date}`), dishes/history имеют только process-local locks, а Web частично читает и пишет domain JSON напрямую. Production history сейчас пустой, поэтому экстренного восстановления данных не требуется.

**Результат задачи.** Stable identity/versioning для mutable entities, единая history semantics, Web без прямого domain JSON persistence, cross-process locking, общие cross-domain command services, fail-closed migrations и проверенный coordinated rollout. Существующий inventory schema v6 с aliases и stock-cycle occurrence semantics остаётся reference implementation и не переписывается без явно отрепетированной миграции.

**Связь с INV-3.** DATA-1 блокирует all-domain synchronization для dishes/history; inventory/catalog slice INV-3 может проектироваться отдельно.

Подробный ticket: [`docs/issues/DATA-1-canonical-domain-models-and-shared-persistence.md`](docs/issues/DATA-1-canonical-domain-models-and-shared-persistence.md).

- [ ] Luxembourg seasonality auto-hints и market availability.
- [ ] Prep-day workflow и aggregated weekend batch plan.
- [ ] Pantry staples и low-stock thresholds.
- [ ] Budget trend view по неделям и магазинам.
- [ ] Export/share shopping trips для телефона.
- [ ] Web editing controls для планов — только после отдельного domain/API design; текущий weekly-plan view остаётся read-only.

---

## Quality gate для каждой issue/phase

1. Зафиксировать domain/UI contract и acceptance criteria в board/ticket.
2. Добавить regression test и увидеть ожидаемый RED.
3. Реализовать минимальный GREEN без обхода plugin flow.
4. Запустить focused tests, затем full unit/integration/web gate.
5. Проверить persistence boundaries, malformed inputs и security/XSS implications.
6. Для UI — desktop/mobile visual QA, keyboard/focus и console errors.
7. Заморозить staged snapshot и получить независимый fail-closed review.
8. Только после `passed:true`: commit, push, local/remote sync и live verification.
