"""Real-browser checks: pages render without script errors, the interests dialog works, phones do not
scroll sideways, the language switch and the service worker work. Needs Playwright with Chromium:
    pip install playwright pytest && python -m playwright install --with-deps chromium
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
LITE = Path(__file__).resolve().parent.parent


def _seed(db_path: Path) -> None:
    """A handful of stories and one map, written the way the app writes them."""
    env = {**os.environ, "WS_DB_PATH": str(db_path)}
    env.pop("DATABASE_URL", None)
    now = datetime.now(UTC)
    arts = []
    for i, (title, pub, cs) in enumerate(
        [
            ("Uzbekistan and China sign gas export agreement", "Reuters", ["UZB", "CHN"]),
            ("Uzbekistan, China agree on new gas exports", "Kun.uz", ["UZB", "CHN"]),
            ("China and Uzbekistan gas export deal signed", "AP", ["UZB", "CHN"]),
            ("Russia and Ukraine hold prisoner exchange talks", "BBC", ["RUS", "UKR"]),
            ("Ukraine and Russia prisoner exchange talks continue", "DW", ["RUS", "UKR"]),
            ("Kazakhstan raises oil output", "Trend", ["KAZ"]),
        ]
    ):
        arts.append(
            (f"https://example.org/{i}", title, pub, (now - timedelta(hours=i + 1)).isoformat(), json.dumps(cs))
        )
    links = [
        {"from": "RUS", "to": "UKR", "type": "conflict", "title": "Urush", "bubble": "Janglar davom etmoqda"},
        {"from": "UZB", "to": "CHN", "type": "trade", "title": "Gaz", "bubble": "Gaz savdosi"},
        {"from": "UZB", "to": "KAZ", "type": "cooperation", "title": "Suv", "bubble": "Suv kelishuvi"},
    ]
    script = f"""
import json, db
db.init(json.load(open("sources_default.json")))
with db.tx() as c:
    for url, title, pub, when, cs in {arts!r}:
        c.execute("INSERT INTO articles (url, title, publisher, published, fetched, kind, countries, categories) "
                  "VALUES (?,?,?,?,?,'rss',?,'[\\"trade\\"]')", (url, title, pub, when, when, cs))
    c.execute("INSERT INTO maps (created, lang, hours, focus, model, data, auto) VALUES (?,?,?,?,?,?,1)",
              ({now.isoformat()!r}, "uz", 24, "", "test", json.dumps({{"summary": "", "links": {links!r}}})))
"""
    subprocess.run([sys.executable, "-c", script], cwd=LITE, env=env, check=True)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("browser") / "b.db"
    _seed(db_path)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "WS_DB_PATH": str(db_path), "WS_LOOP": "0", "WS_SANCTIONS_AUTO": "0", "WS_EMBED": "0",
           "ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "", "TELEGRAM_BOT_TOKEN": ""}  # fmt: skip
    env.pop("DATABASE_URL", None)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--port", str(port)], cwd=LITE, env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/healthz", timeout=2)
            break
        except OSError:
            time.sleep(0.5)
    yield base
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _page(browser, base, mobile=False):
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844} if mobile else {"width": 1366, "height": 900},
        is_mobile=mobile, has_touch=mobile,
    )  # fmt: skip
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.goto(base + "/offline")
    page.evaluate("localStorage.setItem('ws-intro', '1')")
    return page, errors


PAGES = ["/", "/news", "/map", "/map?c=UZB", "/country/UZB", "/sanctions", "/risk", "/tools", "/quiz",
         "/predictions", "/login", "/register", "/feedback"]  # fmt: skip


def test_pages_render_without_script_errors(site, browser):
    page, errors = _page(browser, site)
    for path in PAGES:
        page.goto(site + path)
        page.wait_for_timeout(600)
        assert page.locator("main").count() == 1, path
    assert not errors, errors


def test_interests_dialog_and_for_you(site, browser):
    page, errors = _page(browser, site)
    page.goto(site + "/")
    page.wait_for_selector("dialog#prefs[open]", timeout=5000)
    page.click("dialog#prefs label:has(input[value=UZB])")
    page.click("dialog#prefs button:not([data-close])")
    page.wait_for_load_state()
    assert "Siz tanlaganlar" in page.content()
    assert page.locator("dialog#prefs[open]").count() == 0  # asked once
    assert not errors, errors


def test_phone_pages_do_not_scroll_sideways(site, browser):
    page, _errors = _page(browser, site, mobile=True)
    page.evaluate("localStorage.setItem('ws-prefs-asked', '1')")
    for path in ["/", "/news", "/sanctions", "/risk", "/tools", "/country/UZB", "/watch", "/register"]:
        page.goto(site + path)
        page.wait_for_timeout(500)
        width = page.evaluate("document.documentElement.scrollWidth")
        assert width <= 391, (path, width)
    assert page.locator(".bottomnav a").count() == 6


def test_language_switch_and_service_worker(site, browser):
    page, errors = _page(browser, site)
    page.evaluate("localStorage.setItem('ws-prefs-asked', '1')")
    page.goto(site + "/")
    page.click("a.langbtn")
    page.wait_for_load_state()
    assert page.locator("html").get_attribute("lang") == "ru" and "Что произошло" in page.content()
    assert page.evaluate("navigator.serviceWorker.getRegistration().then(r => !!r)")
    page.click("a.langbtn")
    page.wait_for_load_state()
    assert page.locator("html").get_attribute("lang") == "uz"
    assert not errors, errors
