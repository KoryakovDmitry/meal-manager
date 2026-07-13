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
    {name: 'суп', ingredients: {'лук': true, 'укроп': false}},
    {name: attack, ingredients: {[attack]: true}}
  ];
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
  CACHE.shopping = [{ingredient: attack, unlocks: [attack]}];
  CACHE.history = [{dish: attack, date: '2026-07-13T12:00:00'}];
  CACHE.plans = [{week: attack, status: attack, meals_count: 1, prep_count: 0}];
  renderStats();
  renderDishes();
  renderFridge();
  renderSuggestions();
  renderShopping();
  renderHistory();
  renderPlans();

  const dishesPage = document.getElementById('page-dishes');
  document.querySelectorAll('.page').forEach(page => page.classList.remove('active'));
  dishesPage.classList.add('active');
  const dynamicContainers = [
    'stats-grid', 'unused-list', 'top-ing-list', 'dishes-container',
    'fridge-container', 'suggestions-container', 'shopping-container',
    'plans-history', 'history-container'
  ].map(id => document.getElementById(id));
  const xssImages = dynamicContainers.flatMap(container => [...container.querySelectorAll('img')]);
  const xssImageCount = xssImages.length;
  const xssTextPresent = dynamicContainers.some(container => container.textContent.includes(attack));
  const xssSafe = xssImageCount === 0 && xssTextPresent;
  const xssParents = xssImages.map(image => image.closest('[id]')?.id || image.parentElement?.className || 'unknown').join(',');
  document.body.setAttribute('data-qa-xss', [xssImageCount, xssTextPresent, xssParents].join('|'));
  const planRow = document.querySelector('.plan-row');
  const planNative = planRow?.tagName === 'BUTTON' && planRow.tabIndex === 0;
  const stateOpacityOk = [
    document.querySelector('.ingredient-tag.optional'),
    document.querySelector('.fridge-item.unused'),
    document.querySelector('.dish-card.recent')
  ].every(element => element && getComputedStyle(element).opacity === '1');
  const opener = document.querySelector('#dishes-container [data-action="edit"]');

  opener.focus();
  opener.click();
  let dialog = document.querySelector('#modal-container [role="dialog"]');
  const app = document.querySelector('.app');
  const modalSemantics = dialog?.getAttribute('aria-modal') === 'true' &&
    dialog?.getAttribute('aria-labelledby') === 'dish-modal-title' && app.hasAttribute('inert');
  const first = document.getElementById('modal-dish-name');
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

  api = async () => ({});
  refreshAll = async () => {};
  opener.focus();
  opener.click();
  document.querySelector('[data-modal-action="save"]').click();
  await new Promise(resolve => setTimeout(resolve, 50));
  const saveRestore = document.activeElement === document.getElementById('dish-search');

  opener.focus();
  opener.click();
  dialog = document.querySelector('#modal-container [role="dialog"]');
  const controls = [...document.querySelectorAll('.icon-action, .fridge-item .remove, .ing-editor-row .toggle-ess, .ing-editor-row .del-ing')];
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
  const compact = [...document.querySelectorAll('.icon-action, .fridge-item .remove, .ing-editor-row .toggle-ess, .ing-editor-row .del-ing')];
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
            "true;true;true;true;true;true;true;true;true;true;0"
        ), f"{mobile.group(1)} :: {controls.group(1)} :: xss={xss.group(1)}"
    desktop = re.search(r'data-qa-desktop="([^"]+)"', desktop_dom)
    assert desktop, "desktop behavior probe did not finish"
    assert desktop.group(1) == "44|44|app-sidebar|true|0", desktop.group(1)
    print("web accessibility browser tests: PASS")


if __name__ == "__main__":
    main()
