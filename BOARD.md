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

Активных промежуточных issues сейчас нет.

## 📐 READY INTERMEDIATE ISSUES

Готовых промежуточных issues сейчас нет.

## ✅ COMPLETED INTERMEDIATE ISSUES

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

### INV-3 — Web ↔ agent inventory synchronization and conflict detection 🧊

**Приоритет:** super low / дальний ящик. Автоматическую синхронизацию сейчас не разрабатываем.

**Временный рабочий процесс:** после ручного изменения кухонного запаса через Web Дима или Илиана прямо пишут Hermes, что именно изменилось. Перед важным планированием Hermes может заново прочитать актуальный запас нативным инструментом. Существующие `flock` и dirty-field PATCH продолжают защищать storage и несвязанные поля.

**Когда вернуться:** только если ручное уведомление начнёт регулярно забываться, появятся реальные same-field коллизии или Web станет основным способом ведения запаса.

Подробный ticket: [`docs/issues/INV-3-web-agent-inventory-synchronization.md`](docs/issues/INV-3-web-agent-inventory-synchronization.md).

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
