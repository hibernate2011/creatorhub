"""Local preview fixture for comment AI browser tests."""
import os
import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest
from patchright.sync_api import sync_playwright

@pytest.fixture
def ui(tmp_path, monkeypatch):
    from preview import build_preview
    shutil.copytree(build_preview.ROOT / "assets" / "screenshots", tmp_path / "assets" / "screenshots")
    monkeypatch.setattr(build_preview, "ROOT", tmp_path)
    site = tmp_path / "site"
    build_preview.build(site)
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(site)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000},
                                          color_scheme="light", reduced_motion="reduce")
            context.route("**/*", lambda route: route.continue_() if urlparse(route.request.url).netloc == urlparse(origin).netloc else route.abort())
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin, wait_until="networkidle")
            page.get_by_role("button", name="关闭新手向导", exact=True).click()
            yield page, errors
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def capture(page, name):
    directory = os.environ.get("CREATORHUB_UI_ARTIFACTS")
    if directory:
        target = Path(directory).resolve()
        target.mkdir(parents=True, exist_ok=True)
        # Full-page capture may resize the viewport and legitimately dismiss a
        # positioned popup. Preserve the actual viewport for transient controls.
        page.screenshot(path=str(target / f"{name}.png"), full_page=not name.startswith("ios-editor-") and name not in (
            "mobile-navigation", "mobile-search", "mobile-confirm", "polish-touch-calendar",
            "polish-mobile-navigation", "polish-monitor-sheet", "polish-account-menu", "polish-failed-submit"))


def navigate(page, name, mobile=False):
    if mobile:
        page.locator("#nav-toggle").click()
    page.locator(f'.navitem[data-tab="{name}"]').click()
    page.wait_for_function("name => document.querySelector('.navitem.active').dataset.tab === name", arg=name)


def assert_no_overflow(page, label):
    result = page.evaluate("""() => ({ width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth,
      offenders: [...document.querySelectorAll('main *')].filter(el => {
        const r = el.getBoundingClientRect();
        return r.width && r.right > innerWidth + 2 && !el.closest('.table-wrap,.cs-panel,.dt-panel,.rp-thumbs,.tabbar');
      }).slice(0, 5).map(el => el.id || el.className) })""")
    if result["scroll"] > result["width"] + 2:
        capture(page, "overflow-" + label.replace("/", "-"))
    assert result["scroll"] <= result["width"] + 2, (label, result)


def assert_editor_layout(page, name):
    state = page.locator("#uimodal").evaluate("""modal => {
      const body = modal.querySelector('#ui-body'), b = body.getBoundingClientRect();
      const box = modal.querySelector('.rp-box').getBoundingClientRect();
      const head = modal.querySelector('.ui-modal-head').getBoundingClientRect();
      const title = modal.querySelector('#ui-title').getBoundingClientRect();
      const actions = modal.querySelector('#ui-actions').getBoundingClientRect();
      const fields = [...body.querySelectorAll('input:not([type=hidden]),textarea,.cs-trg,.dt-trg,label,legend')]
        .filter(el => el.getBoundingClientRect().width > 2);
      return { width:body.clientWidth, scroll:body.scrollWidth,
        boxInside:box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight + 1,
        actionsInside:actions.top >= box.top && actions.bottom <= box.bottom,
        headInside:head.top >= box.top && title.left >= head.left && title.right <= head.right && title.bottom <= head.bottom,
        outerScroll:modal.querySelector('.rp-box').scrollTop,
        overflow:fields.filter(el => { const r=el.getBoundingClientRect(); return r.left < b.left-1 || r.right > b.right+1; }).map(el => el.id || el.textContent),
        unnamed:fields.filter(el=>el.matches('input,textarea') && !el.labels?.length && !el.getAttribute('aria-label') && !el.getAttribute('aria-labelledby')).map(el=>el.id),
        buttons:[...modal.querySelectorAll('#ui-actions > button')].map(el => ({ text:el.textContent, height:el.getBoundingClientRect().height, nowrap:getComputedStyle(el).whiteSpace })) };
    }""")
    assert state["scroll"] <= state["width"] + 1, (name, state)
    assert state["boxInside"] and state["actionsInside"], (name, state)
    assert state["headInside"] and state["outerScroll"] == 0, (name, state)
    assert not state["overflow"], (name, state)
    assert not state["unnamed"], (name, state)
    assert all(button["height"] >= 44 and button["nowrap"] == "nowrap" for button in state["buttons"]), (name, state)
    before = page.locator("#ui-actions").bounding_box()
    page.locator("#ui-body").evaluate("el => el.scrollTop = el.scrollHeight")
    after = page.locator("#ui-actions").bounding_box()
    assert abs(before["y"] - after["y"]) <= 1, "Scrolling fields must not move the save action"
