"""Behavioral browser checks for the responsive navigation shell."""

from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading


WEB_DIR = Path(__file__).resolve().parent
INDEX = WEB_DIR / "static" / "index.html"
FAVICON = WEB_DIR / "static" / "favicon.svg"


class _QuietHandler(SimpleHTTPRequestHandler):
    requests: list[str] = []
    response_log: list[tuple[str, int]] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        self.requests.append(self.path)
        super().do_GET()

    def send_response(self, code: int, message: str | None = None) -> None:
        self.response_log.append((self.path, code))
        super().send_response(code, message)

    def log_message(self, format: str, *args) -> None:
        _ = (format, args)


def _browser() -> str:
    for candidate in ("brave-browser", "brave", "chromium", "google-chrome"):
        found = shutil.which(candidate)
        if found:
            return found
    raise RuntimeError("Chromium browser is required for accessibility tests")


def _dump_dom(browser: str, url: str, width: int) -> str:
    result = subprocess.run(
        [
            browser,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            f"--window-size={width},844",
            "--virtual-time-budget=3000",
            "--dump-dom",
            url,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def _qa_source(source: str, probe: str) -> str:
    source = source.replace("refreshAll();\nsetInterval(refreshAll, 30000);", "")
    error_capture = """<script>
window.__qaErrors = [];
const originalConsoleError = console.error.bind(console);
console.error = (...args) => {
  window.__qaErrors.push('console:' + args.map(String).join(' '));
  originalConsoleError(...args);
};
window.addEventListener('error', event => {
  const source = event.target?.src || event.target?.href || event.message || 'resource-error';
  window.__qaErrors.push(String(source));
}, true);
window.addEventListener('unhandledrejection', event => window.__qaErrors.push(String(event.reason)));
</script>
"""
    return source.replace("<script>", error_capture + "<script>", 1).replace(
        "</body>", probe + "</body>"
    )


def main() -> None:
    browser = _browser()
    source = INDEX.read_text(encoding="utf-8")
    mobile_probe = r"""<script>
setTimeout(async () => {
  const sidebar = document.getElementById('app-sidebar');
  const trigger = document.getElementById('mobile-menu-toggle');
  const close = document.getElementById('mobile-menu-close');
  const content = document.querySelector('.content-shell');
  const last = sidebar.querySelector('.sidebar-refresh');
  last.id = 'qa-last';
  const initial = [sidebar.hasAttribute('inert'), sidebar.getAttribute('aria-hidden'), trigger.getAttribute('aria-expanded')].join('|');
  trigger.focus();
  trigger.click();
  await new Promise(resolve => setTimeout(resolve, 50));
  const opened = [document.activeElement.id, sidebar.hasAttribute('inert'), content.hasAttribute('inert'), trigger.getAttribute('aria-expanded')].join('|');
  last.focus();
  const forward = new KeyboardEvent('keydown', {key: 'Tab', bubbles: true, cancelable: true});
  last.dispatchEvent(forward);
  const forwardFocus = document.activeElement.id;
  close.focus();
  const reverse = new KeyboardEvent('keydown', {key: 'Tab', shiftKey: true, bubbles: true, cancelable: true});
  close.dispatchEvent(reverse);
  const reverseFocus = document.activeElement.id;
  close.click();
  await new Promise(resolve => setTimeout(resolve, 50));
  const closed = [document.activeElement.id, sidebar.hasAttribute('inert'), content.hasAttribute('inert'), trigger.getAttribute('aria-expanded')].join('|');

  const attack = `<img src=x onerror="console.error('QA_XSS_EXECUTED')">`;
  CACHE.dishes = [
    {name: 'суп', ingredients: {'лук': true, 'укроп': false}, instructions: attack + '\nВарить 10 минут.'},
    {name: 'лук', ingredients: {'лук': true}},
    {name: attack, ingredients: {[attack]: true}}
  ];
  CACHE.dishesVersion = 'sha256:dish-v1';
  CACHE.fridge = ['лук', attack];
  CACHE.stats = {
    total_dishes: 2, total_fridge_items: 2, cookable_now: 1, recently_cooked: 0,
    fridge_utility: {'лук': 1, [attack]: 0},
    unused_fridge_items: [attack], top_ingredients: [[attack, 1]]
  };
  CACHE.suggestions = [
    {name: 'суп', score: 1, can_cook: true, recently_cooked: false, missing_essentials: [], missing_optional: []},
    {name: attack, score: 0, can_cook: false, recently_cooked: true, missing_essentials: [attack], missing_optional: [attack]}
  ];
  CACHE.shopping = [{
    id:'shop_xss', ingredient:attack, kind:'abstract_request', product_id:null,
    required_uses:1, available_uses:0, to_buy:1,
    required_by:[{kind:'dish',name:attack,uses:1}]
  }];
  const shoppingBefore = CACHE.shopping;
  CACHE.history = [{dish: attack, date: '2026-07-13T12:00:00'}];
  CACHE.plans = [{week: attack, status: attack, meals_count: 1, prep_count: 0}];
  const attackedPlanDays = Object.fromEntries(['mon','tue','wed','thu','fri','sat','sun'].map(day => [day, {meals: []}]));
  attackedPlanDays.mon.meals = [{dish: attack, portions: 2}];
  CACHE.selectedPlan = {week:'2026-W29', status:'draft', prep:[attack], days:attackedPlanDays, shopping:{}};
  CACHE.selectedPlanVersion = 'sha256:xss';
  CACHE.products = [{id:null, name:attack, status:'recipe_only', available:false,
    category:'ready_meal', updated_at:null,
    quantity:null, unit:null, package_count:null, storage:null, expires_on:null,
    comment:null, expiry_status:'unknown', recipe_count:1, in_recipes:true}];
  renderStats();
  renderDishes();
  const dishInstructionsCard = document.querySelector('.dish-instructions');
  const dishInstructionsVisible = dishInstructionsCard?.textContent.includes('Как готовить') &&
    dishInstructionsCard.textContent.includes('Варить 10 минут.') &&
    dishInstructionsCard.querySelectorAll('img').length === 0;
  renderFridge();
  renderSuggestions();
  renderShopping();
  localStorage.removeItem('meal-shopping-checked');
  let shoppingApiCalls = 0;
  api = async () => { shoppingApiCalls += 1; return {}; };
  const shoppingCheckbox = document.querySelector('#shopping-container input[type="checkbox"]');
  shoppingCheckbox?.click();
  const shoppingChecklistLocal = shoppingCheckbox?.checked === true &&
    JSON.parse(localStorage.getItem('meal-shopping-checked') || '{}').shop_xss === true;
  renderShopping();
  const shoppingChecklistSurvivesRender =
    document.querySelector('[data-shopping-check="shop_xss"]')?.checked === true;
  CACHE.shoppingProjectionError = 'corrupt recipe';
  renderShopping();
  const shoppingProjectionFailsClosed =
    document.getElementById('shopping-container').textContent.includes('временно недоступен') &&
    document.querySelectorAll('#shopping-container [data-shopping-id]').length === 0 &&
    !document.getElementById('shopping-container').textContent.includes('покупать ничего не нужно');
  const planShoppingProjectionFailsClosed =
    renderPlanShopping({items: []}, 'corrupt recipe').includes('Позиции скрыты') &&
    !renderPlanShopping({items: []}, 'corrupt recipe').includes('Ничего');
  CACHE.shoppingProjectionError = null;
  CACHE.shopping = shoppingBefore;
  renderShopping();
  const shoppingCheckboxDoesNotMutateInventory = shoppingApiCalls === 0 &&
    !document.getElementById('shopping-container').textContent.includes('Добавлено в холодильник');
  renderHistory();
  renderPlans();
  renderPlanDetail();
  const planDetailXssSafe = document.querySelectorAll('#plan-detail img').length === 0 &&
    document.getElementById('plan-detail').textContent.includes(attack);
  renderProductCatalog();

  document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
  document.getElementById('page-fridge').classList.add('active');
  const fridgeEdit = document.querySelector('#fridge-container [data-action="edit"]');
  fridgeEdit.focus();
  fridgeEdit.click();
  const fridgeInput = document.getElementById('fridge-edit-input');
  const fridgeEditFocus = document.activeElement === fridgeInput;
  fridgeInput.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  const fridgeCancelRestore = document.activeElement ===
    document.querySelector('#fridge-container [data-action="edit"]');

  let resolveRename;
  api = async (path) => {
    if (path === '/api/fridge/item') {
      return new Promise(resolve => { resolveRename = resolve; });
    }
    return {};
  };
  refreshAll = async () => {};
  document.querySelector('#fridge-container [data-action="edit"]').click();
  document.getElementById('fridge-edit-input').value = 'лук новый';
  const pendingRename = saveFridgeItemEdit();
  cancelFridgeItemEdit();
  document.querySelectorAll('#fridge-container [data-action="edit"]')[1]?.click();
  const inFlightEditLocked = editingFridgeItem === 'лук';
  resolveRename({ingredients: ['лук новый', attack]});
  await pendingRename;
  const fridgeSaveRestore = document.activeElement ===
    document.querySelector('#fridge-container [data-action="edit"][data-value="лук новый"]');
  const expiredRecord = {
    id: 'inv_expired', name: 'молоко', quantity: '1', unit: 'l',
    category: 'ready_meal',
    package_count: null, storage: 'fridge', expires_on: '2026-07-13',
    comment: null, expiry_status: 'expired', updated_at: 'v-expired'
  };
  const expiringSoon = {id:'inv_soon', name:'йогурт', quantity:null, unit:null, package_count:null,
    category:'prep', storage:'fridge', expires_on:'2026-07-16', comment:null,
    expiry_status:'expiring_soon', updated_at:'v-soon'};
  CACHE.inventory = [expiredRecord, expiringSoon];
  CACHE.fridge = CACHE.inventory.map(item => item.name);
  renderFridge();
  const expiryText = document.getElementById('fridge-container').textContent;
  const expiryAccessible = expiryText.includes('Просрочено') && expiryText.includes('Скоро истекает срок');
  const inventoryCategoryBadges = [...document.querySelectorAll('#fridge-container .category-badge')]
    .map(node => node.textContent.trim()).sort().join('|') === 'Готовая еда|Заготовка';
  document.getElementById('fridge-category-filter').value = 'prep';
  renderFridge();
  const inventoryCategoryFilter = document.querySelectorAll('#fridge-container .fridge-item').length === 1 &&
    document.getElementById('fridge-container').textContent.includes('йогурт');
  document.getElementById('fridge-category-filter').value = 'all';
  CACHE.inventory = [expiredRecord];
  const salt = {id:'inv_salt', name:'соль', quantity:null, unit:null, package_count:null,
    storage:'pantry', expires_on:null, comment:'старый', expiry_status:'unknown', updated_at:'v-salt'};
  CACHE.inventory.push(salt);
  CACHE.fridge = CACHE.inventory.map(item => item.name);
  renderFridge();
  let confirmationText = '';
  let inventoryCall = null;
  confirm = text => { confirmationText = text; return true; };
  api = async (path, method, body) => {
    inventoryCall = {path, method, body};
    if (method === 'DELETE') {
      CACHE.inventory = CACHE.inventory.filter(item => item.id !== 'inv_expired');
      CACHE.fridge = CACHE.inventory.map(item => item.name);
      return {item: {id:'inv_expired'}};
    }
    return {item: {...salt, ...body}};
  };
  refreshAll = async () => renderFridge();
  await removeFridgeItem('молоко');
  const deleteVersioned = inventoryCall.path.includes('expected_updated_at=v-expired');
  const deleteAccessible = confirmationText.includes('молоко') && document.activeElement ===
    document.querySelector('#fridge-container [data-action="edit"][data-value="соль"]');
  startFridgeItemEdit('соль');
  document.getElementById('fridge-edit-comment').value = 'новый';
  await saveFridgeItemEdit();
  const dirtyPatchOnly = inventoryCall.method === 'PATCH' &&
    JSON.stringify(Object.keys(inventoryCall.body)) === JSON.stringify(['comment','expected_updated_at']) &&
    inventoryCall.body.expected_updated_at === 'v-salt';
  startFridgeItemEdit('соль');
  document.getElementById('fridge-edit-input').value = 'соль локальная';
  api = async () => {
    const conflict = new Error('conflict');
    conflict.status = 409;
    conflict.detail = {code:'inventory_conflict', current_item:{...salt, name:'соль сервер', updated_at:'v-server'}};
    throw conflict;
  };
  await saveFridgeItemEdit();
  const conflictRenamePreserved = editingFridgeItem === 'соль сервер' &&
    document.getElementById('fridge-edit-input').value === 'соль локальная' &&
    CACHE.inventory.some(item => item.name === 'соль сервер' && item.updated_at === 'v-server') &&
    !document.getElementById('fridge-edit-input').disabled;
  cancelFridgeItemEdit();
  CACHE.inventory = [salt];
  CACHE.fridge = ['соль'];
  renderFridge();
  startFridgeItemEdit('соль');
  document.getElementById('fridge-edit-comment').value = 'локальный после удаления';
  api = async () => {
    const conflict = new Error('deleted conflict');
    conflict.status = 409;
    conflict.detail = {code:'inventory_conflict', current_item:{...salt, available:false, updated_at:'v-deleted'}};
    throw conflict;
  };
  await saveFridgeItemEdit();
  const deletedConflictResolved = editingFridgeItem === null &&
    !CACHE.inventory.some(item => item.id === 'inv_salt') &&
    !document.getElementById('fridge-edit-input');
  CACHE.inventory = [salt];
  CACHE.fridge = ['соль'];
  renderFridge();

  const planDays = Object.fromEntries(['mon','tue','wed','thu','fri','sat','sun'].map(day => [day, {meals: []}]));
  document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
  document.getElementById('page-plans').classList.add('active');
  planDays.wed.meals = [{dish:'суп', portions:2}];
  CACHE.selectedPlan = {week:'2026-W29', status:'draft', prep:[], days:planDays, shopping:{}};
  CACHE.selectedPlanVersion = 'sha256:plan-v1';
  renderPlanDetail();
  const planEdit = document.querySelector('#plan-detail [data-action="edit-meal"]');
  planEdit.focus();
  planEdit.click();
  const planDialog = document.querySelector('#modal-container [role="dialog"]');
  const planModalSemantics = planDialog?.getAttribute('aria-modal') === 'true' &&
    planDialog?.getAttribute('aria-labelledby') === 'plan-meal-modal-title' &&
    document.activeElement === document.getElementById('plan-meal-dish') &&
    document.getElementById('plan-meal-portions').getBoundingClientRect().height >= 44;
  document.getElementById('plan-meal-portions').value = '3';
  let planCall = null;
  api = async (path, method, body) => {
    planCall = {path, method, body};
    if (method === 'DELETE') return {status:'ok', week:'2026-W29'};
    planDays.wed.meals = [{dish:'суп', portions:3}];
    return {plan:{week:'2026-W29', status:'draft', prep:[], days:planDays, shopping:{}}, version:'sha256:plan-v2'};
  };
  refreshAll = async () => renderAll();
  await savePlanMeal('wed', 0);
  const planSaveVersioned = planCall.method === 'PATCH' &&
    planCall.path.endsWith('/days/wed/meals/0') &&
    planCall.body.expected_version === 'sha256:plan-v1' &&
    planCall.body.portions === 3;
  const planSaveFocus = document.activeElement?.id === 'plan-detail-title';

  showPlanMealModal('wed', '0');
  document.getElementById('plan-meal-portions').value = '4';
  const authoritativeDays = Object.fromEntries(['mon','tue','wed','thu','fri','sat','sun'].map(day => [day, {meals: []}]));
  authoritativeDays.wed.meals = [{dish:'паста', portions:1}];
  api = async () => {
    const conflict = new Error('plan conflict');
    conflict.status = 409;
    conflict.detail = {
      code:'plan_conflict',
      current_plan:{week:'2026-W29', status:'draft', prep:[], days:authoritativeDays, shopping:{}},
      current_version:'sha256:plan-v3'
    };
    throw conflict;
  };
  await savePlanMeal('wed', 0);
  const planConflictModalClosed = !document.querySelector('#modal-container [role="dialog"]') &&
    CACHE.selectedPlan.days.wed.meals[0].dish === 'паста' &&
    CACHE.selectedPlanVersion === 'sha256:plan-v3' &&
    document.activeElement?.id === 'plan-detail-title';

  CACHE.plans = [
    {week:'2026-W29', status:'draft', meals_count:1, prep_count:0},
    {week:'2026-W28', status:'archived', meals_count:1, prep_count:0}
  ];
  renderPlans();
  api = async (path, method, body) => {
    planCall = {path, method, body};
    return {status:'ok', week:'2026-W29'};
  };
  refreshAll = async () => {
    CACHE.plans = [{week:'2026-W28', status:'archived', meals_count:1, prep_count:0}];
    renderAll();
  };
  await deleteWeekPlan();
  const planDeleteVersioned = planCall.method === 'DELETE' &&
    planCall.path.includes('expected_version=sha256%3Aplan-v3');
  const planDeleted = CACHE.selectedPlan === null && CACHE.selectedPlanVersion === null;
  const planDetailCleared = document.getElementById('plan-detail-card').style.display === 'none';
  const planListFocus = document.activeElement === document.querySelector('#plans-history .plan-row');

  CACHE.selectedPlan = {week:'2026-W30', status:'draft', prep:[], days:authoritativeDays, shopping:{}};
  CACHE.selectedPlanVersion = 'sha256:external';
  renderPlanDetail();
  CACHE.plans = [{week:'2026-W28', status:'archived', meals_count:1, prep_count:0}];
  syncSelectedPlanWithList();
  renderPlanDetail();
  const externallyDeletedPlanCleared = CACHE.selectedPlan === null &&
    document.getElementById('plan-detail-card').style.display === 'none' &&
    document.getElementById('plan-detail').childElementCount === 0;

  const dishesPage = document.getElementById('page-dishes');
  document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
  dishesPage.classList.add('active');
  const dynamicContainers = [
    'stats-grid', 'unused-list', 'top-ing-list', 'dishes-container',
    'fridge-container', 'product-catalog-container', 'suggestions-container', 'shopping-container',
    'plans-history', 'plan-detail', 'history-container'
  ].map(id => document.getElementById(id));
  const xssImages = dynamicContainers.flatMap(container => [...container.querySelectorAll('img')]);
  const xssImageCount = xssImages.length;
  const xssTextPresent = dynamicContainers.some(container => container.textContent.includes(attack));
  const xssSafe = xssImageCount === 0 && xssTextPresent && planDetailXssSafe;
  const xssParents = xssImages.map(image => image.closest('[id]')?.id || image.parentElement?.className || 'unknown').join(',');
  document.body.setAttribute('data-qa-xss', [xssImageCount, xssTextPresent, xssParents].join('|'));
  const planRow = document.querySelector('.plan-row');
  const planNative = planRow?.tagName === 'BUTTON' && planRow.tabIndex === 0;
  const stateOpacityOk = [
    document.querySelector('.ingredient-tag.optional'),
    document.querySelector('.fridge-item.unused'),
    document.querySelector('.dish-card.recent')
  ].every(element => element && getComputedStyle(element).opacity === '1');
  dishesPage.classList.remove('active');
  document.getElementById('page-products').classList.add('active');
  const productOpener = document.querySelector('#product-catalog-container [data-action="replenish"]');
  productOpener.focus();
  productOpener.click();
  const productDialog = document.querySelector('#modal-container [role="dialog"]');
  const productModalSemantics = productDialog?.getAttribute('aria-modal') === 'true' &&
    productDialog?.getAttribute('aria-labelledby') === 'replenish-modal-title' &&
    document.querySelector('.app').hasAttribute('inert');
  const replenishFreshBatch = document.getElementById('replenish-expiry').value === '' &&
    document.getElementById('replenish-comment').value === '';
  const replenishCategoryPrefill = document.getElementById('replenish-category').value === 'ready_meal';
  productDialog.querySelector('[data-modal-action="cancel"]').click();
  const productFocusRestore = document.activeElement === productOpener;
  const categoryOpener = document.querySelector('#product-catalog-container [data-action="category"]');
  categoryOpener.focus();
  categoryOpener.click();
  const categoryDialog = document.querySelector('#modal-container [role="dialog"]');
  const categoryModalSemantics = categoryDialog?.getAttribute('aria-modal') === 'true' &&
    categoryDialog?.getAttribute('aria-labelledby') === 'product-category-modal-title' &&
    document.activeElement === document.getElementById('product-category-select') &&
    document.getElementById('product-category-select').getBoundingClientRect().height >= 44;
  let categoryCall = null;
  api = async (path, method, body) => {
    categoryCall = {path, method, body};
    CACHE.products[0] = {...CACHE.products[0], id:'inv-category', category:body.category, updated_at:'v-category'};
    return {item:CACHE.products[0]};
  };
  refreshAll = async () => renderProductCatalog();
  document.getElementById('product-category-select').value = 'prep';
  await saveProductCategory(0);
  const categoryVersioned = categoryCall.path === '/api/products/category' &&
    categoryCall.method === 'PATCH' && categoryCall.body.expected_updated_at === null &&
    categoryCall.body.category === 'prep';
  const categoryFocusRestore = document.activeElement ===
    document.querySelector('#product-catalog-container [data-action="category"]');
  const categoryBadge = document.querySelector('#product-catalog-container .category-badge')?.textContent.trim() === 'Заготовка' &&
    document.querySelector('#product-catalog-container .product-card')?.classList.contains('category-prep');
  document.getElementById('product-catalog-category').value = 'prep';
  renderProductCatalog();
  const categoryFilter = document.querySelectorAll('#product-catalog-container .product-card').length === 1;
  document.getElementById('product-catalog-category').value = 'all';
  document.getElementById('page-products').classList.remove('active');
  dishesPage.classList.add('active');
  const opener = document.querySelector('#dishes-container [data-action="edit"]');

  opener.focus();
  opener.click();
  let dialog = document.querySelector('#modal-container [role="dialog"]');
  const app = document.querySelector('.app');
  const modalSemantics = dialog?.getAttribute('aria-modal') === 'true' &&
    dialog?.getAttribute('aria-labelledby') === 'dish-modal-title' && app.hasAttribute('inert');
  const first = document.getElementById('modal-dish-name');
  const instructionsInput = document.getElementById('modal-dish-instructions');
  const dishInstructionsPrefilled = instructionsInput?.value === attack + '\nВарить 10 минут.' &&
    !instructionsInput.hasAttribute('maxlength');
  const lastModal = dialog.querySelector('[data-modal-action="cancel"]');
  first.focus();
  const modalReverse = new KeyboardEvent('keydown', {key: 'Tab', shiftKey: true, bubbles: true, cancelable: true});
  first.dispatchEvent(modalReverse);
  const modalReverseOk = document.activeElement === lastModal && modalReverse.defaultPrevented;
  const modalForward = new KeyboardEvent('keydown', {key: 'Tab', bubbles: true, cancelable: true});
  lastModal.dispatchEvent(modalForward);
  const modalForwardOk = document.activeElement === first && modalForward.defaultPrevented;
  lastModal.click();
  const cancelRestore = document.activeElement === opener && !app.hasAttribute('inert');

  opener.focus();
  opener.click();
  document.querySelector('.modal-bg').click();
  const backdropRestore = document.activeElement === opener;

  opener.focus();
  opener.click();
  document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
  const escapeRestore = document.activeElement === opener;

  let dishCall = null;
  api = async (path, method, body) => {
    dishCall = {path, method, body};
    return {};
  };
  refreshAll = async () => {};
  opener.focus();
  opener.click();
  const saveInstructionsInput = document.getElementById('modal-dish-instructions');
  const emojiInstructions = '😀'.repeat(10001);
  if (saveInstructionsInput) saveInstructionsInput.value = emojiInstructions;
  document.querySelector('[data-modal-action="save"]').click();
  await new Promise(resolve => setTimeout(resolve, 50));
  const dishInstructionsSaved = dishCall?.method === 'PUT' &&
    dishCall.body.instructions === emojiInstructions &&
    [...dishCall.body.instructions].length === 10001 &&
    dishCall.body.expected_version === 'sha256:dish-v1';
  const saveRestore = document.activeElement === document.getElementById('dish-search');

  opener.focus();
  opener.click();
  dialog = document.querySelector('#modal-container [role="dialog"]');
  const controls = [...document.querySelectorAll('.icon-action, .fridge-item .edit, .fridge-item .remove, .product-card-actions .btn, .ing-editor-row .toggle-ess, .ing-editor-row .del-ing')];
  const controlDetails = controls.map(control => {
    const page = control.closest('.page');
    const previousDisplay = page?.style.display || '';
    if (page) page.style.display = 'block';
    const rect = control.getBoundingClientRect();
    const name = (control.getAttribute('aria-label') || control.textContent || '').trim();
    if (page) page.style.display = previousDisplay;
    return {control, width: rect.width, height: rect.height, name};
  });
  const controlsOk = controlDetails.length >= 7 && controlDetails.every(detail =>
    detail.width >= 44 && detail.height >= 44 && detail.name.length > 1
  );
  document.body.setAttribute('data-qa-controls', controlDetails.map(detail =>
    [detail.control.className, detail.width, detail.height, detail.name].join('~')
  ).join('|'));
  closeModal();
  document.body.setAttribute('data-qa-mobile', [
    innerWidth, initial, opened, forwardFocus, forward.defaultPrevented,
    reverseFocus, reverse.defaultPrevented, closed, controlsOk,
    document.getElementById('sidebar-backdrop').tabIndex,
    xssSafe, planNative, modalSemantics, modalReverseOk, modalForwardOk,
    cancelRestore, backdropRestore, escapeRestore, saveRestore, stateOpacityOk,
    fridgeEditFocus, fridgeCancelRestore,
    inFlightEditLocked, fridgeSaveRestore, expiryAccessible,
    inventoryCategoryBadges, inventoryCategoryFilter,
    deleteAccessible, deleteVersioned, dirtyPatchOnly, conflictRenamePreserved, deletedConflictResolved,
    planModalSemantics, planSaveVersioned, planSaveFocus, planConflictModalClosed,
    planDeleteVersioned, planDeleted, planDetailCleared, planListFocus, externallyDeletedPlanCleared,
    productModalSemantics, replenishFreshBatch, replenishCategoryPrefill, productFocusRestore,
    categoryModalSemantics, categoryVersioned, categoryFocusRestore, categoryBadge, categoryFilter,
    shoppingChecklistLocal, shoppingChecklistSurvivesRender,
    shoppingProjectionFailsClosed, planShoppingProjectionFailsClosed,
    shoppingCheckboxDoesNotMutateInventory,
    dishInstructionsVisible, dishInstructionsPrefilled, dishInstructionsSaved,
    window.__qaErrors.length
  ].join(';'));
}, 500);
</script>"""
    desktop_probe = r"""<script>
setTimeout(() => {
  CACHE.dishes = [{name: 'суп', ingredients: {'лук': true}}];
  CACHE.fridge = ['лук'];
  CACHE.stats = {fridge_utility: {'лук': 1}};
  CACHE.suggestions = [{name: 'суп', score: 1, can_cook: true, recently_cooked: false, missing_essentials: [], missing_optional: []}];
  CACHE.history = [{dish: 'суп', date: '2026-07-13T12:00:00'}];
  renderDishes();
  renderFridge();
  renderSuggestions();
  renderHistory();
  showEditDishModal('суп');
  const toggle = document.getElementById('sidebar-toggle');
  const rect = toggle.getBoundingClientRect();
  const compact = [...document.querySelectorAll('.icon-action, .fridge-item .edit, .fridge-item .remove, .product-card-actions .btn, .ing-editor-row .toggle-ess, .ing-editor-row .del-ing')];
  const compactOk = compact.every(control => {
    const page = control.closest('.page');
    const previousDisplay = page?.style.display || '';
    if (page) page.style.display = 'block';
    const bounds = control.getBoundingClientRect();
    if (page) page.style.display = previousDisplay;
    return bounds.width >= 44 && bounds.height >= 44;
  });
  closeModal();
  document.body.setAttribute('data-qa-desktop', [rect.width, rect.height, toggle.getAttribute('aria-controls'), compactOk, window.__qaErrors.length].join('|'));
}, 500);
</script>"""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "static").mkdir()
        shutil.copy2(FAVICON, root / "static" / "favicon.svg")
        (root / "mobile.html").write_text(
            _qa_source(source, mobile_probe), encoding="utf-8"
        )
        (root / "desktop.html").write_text(
            _qa_source(source, desktop_probe), encoding="utf-8"
        )
        _QuietHandler.requests = []
        _QuietHandler.response_log = []
        handler = partial(_QuietHandler, directory=root)
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            # Chromium clamps desktop headless windows below 500px; 500 and 768
            # exercise the same production mobile breakpoint, with 768 testing its edge.
            mobile_500 = _dump_dom(browser, f"{base}/mobile.html", 500)
            mobile_768 = _dump_dom(browser, f"{base}/mobile.html", 768)
            desktop_dom = _dump_dom(browser, f"{base}/desktop.html", 1280)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    expected_paths = {"/mobile.html", "/desktop.html", "/static/favicon.svg"}
    assert set(_QuietHandler.requests) <= expected_paths, _QuietHandler.requests
    assert _QuietHandler.response_log, "browser fixture server recorded no responses"
    assert all(status == 200 for _, status in _QuietHandler.response_log), _QuietHandler.response_log
    for expected_width, mobile_dom in ((500, mobile_500), (768, mobile_768)):
        mobile = re.search(r'data-qa-mobile="([^"]+)"', mobile_dom)
        controls = re.search(r'data-qa-controls="([^"]+)"', mobile_dom)
        xss = re.search(r'data-qa-xss="([^"]+)"', mobile_dom)
        assert mobile, f"mobile {expected_width}px behavior probe did not finish"
        assert controls, f"mobile {expected_width}px control probe did not finish"
        assert xss, f"mobile {expected_width}px renderer safety probe did not finish"
        assert mobile.group(1) == (
            f"{expected_width};true|true|false;mobile-menu-close|false|true|true;"
            "mobile-menu-close;true;qa-last;true;"
            "mobile-menu-toggle|true|false|false;true;-1;"
            + ";".join(["true"] * 48) + ";0"
        ), f"{mobile.group(1)} :: {controls.group(1)} :: xss={xss.group(1)}"
    desktop = re.search(r'data-qa-desktop="([^"]+)"', desktop_dom)
    assert desktop, "desktop behavior probe did not finish"
    assert desktop.group(1) == "44|44|app-sidebar|true|0", desktop.group(1)
    print("web accessibility browser tests: PASS")


if __name__ == "__main__":
    main()
