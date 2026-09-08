# RECEIPT-1 — Receipt ledger and purchase analytics

## Проблема

Сейчас фотография или текст чека используется только как одноразовое evidence для подтверждения конкретной покупки и обновления кухонного запаса. Содержимое чека после чтения не сохраняется как самостоятельный факт покупки, поэтому невозможно надёжно ответить:

- что и когда покупали;
- в каком магазине;
- сколько стоила позиция, упаковка и вся корзина;
- как менялись цены;
- сколько потрачено за неделю/месяц и по магазинам;
- какие покупки вообще не относились к текущему холодильнику или shopping request.

Архив чеков — отдельный домен. Он не является inventory snapshot и не должен зависеть от того, добавляется ли покупка в холодильник.

## Пользовательский контракт

Когда Дима или Илиана присылают читаемый чек для обработки, система сохраняет его структурированную расшифровку в receipt ledger. Минимально сохраняются:

- магазин и, если виден, филиал/адрес;
- дата и отдельное время покупки, если оно присутствует;
- валюта;
- полный список строк покупки в исходной формулировке чека;
- количество/вес/единица, если они напечатаны или однозначно выводятся;
- цена за единицу/кг и итог строки, если они доступны;
- скидки, купоны, возвраты и залог за тару отдельными signed-строками;
- subtotal/total и другие напечатанные итоги;
- дата внесения, источник и качество распознавания.

Неуверенные значения не додумываются. Сохраняется исходный текст строки, а неизвестное структурированное поле остаётся `null`. Неоднозначный чек можно записать как `needs_review`, показать сомнительные строки пользователю и позже исправить без потери первой расшифровки.

## Implementation status — 2026-08-09

Native MVP реализован. Canonical source of truth — `data/receipts.json` schema v1; создание, corrections, analytical links и retraction проходят через общий crash-safe AUDIT transaction manager и optimistic revision check. Доступны tools `record_purchase_receipt`, `get_purchase_receipt`, `list_purchase_receipts`, `correct_purchase_receipt`, `link_purchase_receipt_line`, `retract_purchase_receipt` и `get_purchase_analytics`.

Реализованный native scope сохраняет ordered raw lines, integer cents, nullable unknowns, evidence hash/reference, confidence, stable receipt/line IDs и immutable revisions. Повтор одного и того же exact intake идемпотентен даже при cross-process race; evidence и canonical semantics имеют одного владельца во всей append-only history. Semantic replay с новым, ранее не закреплённым evidence hash отклоняется и требует явной correction revision, поэтому новый source нельзя молча потерять. `needs_review` и `retracted` исключены по умолчанию. Никакая receipt-команда не мутирует inventory, plan или shopping request.

Web upload/editor остаётся отдельным следующим surface и не входит в этот native MVP; когда он появится, он обязан использовать этот же command/repository, OCC и sanitized 503 path.

## Жёсткая граница с холодильником и shopping

- Запись чека **никогда автоматически не добавляет** товар в inventory, не меняет availability и не закрывает shopping request.
- Подтверждение купленного товара через `receive_shopping_item` остаётся отдельной явной командой.
- Receipt line может позднее получить optional links на `product_id` и/или shopping occurrence, но link нужен только для аналитики и не является inventory mutation. Product identity merge блокируется, если source встречается в любой receipt revision: append-only history не должна получать dangling links.
- В чек сохраняются все позиции: продукты не из плана, бытовые товары, залог за тару и прочие строки тоже не отбрасываются.
- Browser shopping checkbox остаётся заметкой и не считается ни чеком, ни подтверждением покупки.

## Canonical data model

### Receipt

- stable immutable `receipt_id` (`receipt_*`);
- `status`: `needs_review | confirmed | corrected | retracted`;
- `merchant_name_raw` и optional normalized merchant identity;
- optional branch/address;
- `purchased_at` с declared precision и timezone; отдельно `recorded_at`;
- ISO currency (для первого релиза ожидается `EUR`);
- ordered `lines`;
- optional printed `subtotal_cents`, `total_cents`, tax/payment-neutral summary fields;
- computed `lines_total_cents` и `reconciliation_delta_cents`;
- evidence metadata: source kind, optional source reference/content hash и OCR/transcription confidence;
- immutable creation provenance и append-preserving corrections/retraction.

### Receipt line

- stable `receipt_line_id` и исходный порядок;
- `description_raw` — обязательная дословная строка товара;
- optional normalized label/category;
- optional quantity represented as decimal string, unit and package description;
- optional `unit_price_cents`, `price_per_base_unit_cents`, `line_total_cents`;
- signed line type: `product | discount | coupon | return | deposit | fee | other`;
- optional confidence/ambiguity note;
- optional analytical links только к существующим catalog/shopping identities, без побочных mutation; create/correction не могут подменить server-owned links.

Денежные значения хранятся целыми minor units (`*_cents`), не binary float. Quantity хранится как точный положительный decimal text без float conversion; исходная печатная формулировка остаётся в `description_raw`. Сумма строк не обязана молча совпадать с printed total: расхождение сохраняется и переводит запись в `needs_review`.

## Persistence and audit

- Versioned, fail-closed receipt repository строго в canonical `data/receipts.json`; tracked `data/.gitkeep` обеспечивает clean-export directory prerequisite, а malformed schema не превращается в пустую историю.
- Receipt identity и duplicate fingerprint не зависят от UI position. Повторная обработка того же source hash или полностью совпадающей canonical receipt semantics идемпотентна только когда новый evidence не теряется; новый hash для известных semantics требует explicit correction.
- Похожий магазин/дата/сумма без точного совпадения не считается автоматическим дублем — возвращаются candidates для подтверждения.
- Create, correction, link/unlink и retraction проходят общий AUDIT journal. Исправление не стирает первоначальную расшифровку.
- Receipt evidence и audit reasons не сохраняют полный номер карты, loyalty ID, QR payload, authorization/transaction reference, terminal ID, payment token или другие ненужные платёжные идентификаторы: recognizable fragments отклоняются до commit, пока caller не пришлёт безопасное значение без идентификатора.

## Native workflow — MVP

1. Агент читает фотографию/PDF/текст чека.
2. Вызывает `record_purchase_receipt` с merchant/date/currency/ordered lines/totals/evidence metadata.
3. При сомнениях запись создаётся как `needs_review`, а пользователю перечисляются только неоднозначные места.
4. После уточнения вызывается append-preserving `correct_purchase_receipt`.
5. `get_purchase_receipt` и `list_purchase_receipts` возвращают исходные строки и структурированные значения.
6. `retract_purchase_receipt` исключает ошибочную запись из аналитики, но сохраняет evidence и correction history.

Первый релиз не требует собственного OCR engine: vision/OCR может выполняться агентом, но результат обязан попасть в canonical receipt repository. Web upload/OCR pipeline остаётся следующей поверхностью над тем же контрактом.

## Purchase analytics

Read model должен поддерживать:

- расходы по дням, неделям и месяцам;
- суммы и число поездок по магазинам;
- полный список покупок за период;
- историю цен для raw/normalized товара с магазином и датой;
- package/unit-price series только при достаточных quantity/unit данных;
- скидки и reconciliation gaps;
- coverage/confidence: сколько строк и суммы нормализовано, а сколько остаётся raw-only;

Projected basket cost на основе свежести и магазина цены остаётся follow-up scope: native MVP предоставляет только исторические факты и не выдаёт receipt analytics за прогноз будущей корзины.

`needs_review` может отображаться в ledger, но не должен входить в подтверждённую аналитику по умолчанию. `retracted` никогда не входит в агрегаты.

## Web UX — следующая поверхность

- Раздел «Чеки / Покупки» со списком чеков, магазином, датой, total и review badge.
- Карточка чека показывает ordered raw lines, распознанные цены и reconciliation.
- Фильтры period/store/status и price-history view.
- Доступное подтверждение/исправление ambiguous lines; весь пользовательский/OCR-текст XSS-safe.
- Upload не создаёт inventory items без отдельного явного действия.

## Acceptance criteria

- RED→GREEN repository/model tests для точных cents, decimal quantity, ordered raw lines, discounts/returns/deposit, partial dates и unknown fields.
- Фото/текст одного чека можно записать, перечитать и получить те же raw labels, магазин, дату, line prices и total.
- Повтор одного evidence hash идемпотентен; conflicting duplicate fails closed без второй записи.
- Неоднозначная строка сохраняется raw-only и не получает выдуманную цену/quantity/category.
- Total mismatch сохраняется как review signal, а не исправляется молча.
- Correction сохраняет original revision; retraction исключает receipt из analytics.
- Receipt create/correct/retract оставляет inventory, plans, shopping requests и current shopping projection byte-for-byte неизменными.
- Отдельный explicit link или `receive_shopping_item` проверяется как самостоятельная операция и не меняет canonical receipt evidence.
- Native schemas, repository locks, AUDIT evidence, malformed-data behavior и Web 503 sanitization покрыты тестами.
- Analytics корректно фильтрует `needs_review`/`retracted`, показывает store/date provenance и не вычисляет unit price без надёжного denominator.
- Full unit/integration/Web/Chromium/compile/diff gate, independent exact-tree review, backup, coordinated Gateway/Web restart и live QA.

## Out of scope первого релиза

- автоматическая синхронизация с банковскими транзакциями;
- хранение платёжных реквизитов;
- обязательная нормализация каждой строки к inventory identity;
- автоматическое изменение холодильника;
- собственная обучаемая OCR-модель;
- налоговая/бухгалтерская отчётность.
- projected basket-cost forecasting и автоматический выбор магазина.
