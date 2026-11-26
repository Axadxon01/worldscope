"""Offline tests: parsers on realistic samples, tagging, grouping, and every page."""

import importlib
import json
import os
import re
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote_plus

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze
import collectors

NOW = datetime.now(UTC)
# modules that hold a database handle or settings read at import: reloaded for every test
MODULES = ("db", "facts", "debt", "telegram_bot", "sanctions", "risk", "framing", "accounts", "watch",
           "predictions", "trade", "scheduler", "embed", "app")  # fmt: skip


def use_test_db(monkeypatch, tmp_path, name):
    """SQLite by default; set TEST_DATABASE_URL to run the same tests on PostgreSQL."""
    monkeypatch.setenv("WS_DB_PATH", str(tmp_path / f"{name}.db"))
    pg = os.environ.get("TEST_DATABASE_URL")
    if pg:
        monkeypatch.setenv("DATABASE_URL", pg)
        monkeypatch.setenv("WS_DB_SCHEMA", f"t_{uuid.uuid4().hex[:10]}")
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)


def rfc822(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


GNEWS = f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>"Uzbekistan" - Google News</title>
<item><title>Uzbekistan and China sign gas export agreement - Reuters</title>
<link>https://news.google.com/rss/articles/CBMiAAA?oc=5</link>
<pubDate>{rfc822(NOW - timedelta(hours=2))}</pubDate>
<description>&lt;a href="https://news.google.com/rss/articles/CBMiAAA"&gt;Uzbekistan and China sign gas export agreement&lt;/a&gt;&amp;nbsp;&amp;nbsp;&lt;font color="#6f6f6f"&gt;Reuters&lt;/font&gt;</description>
<source url="https://www.reuters.com">Reuters</source></item>
<item><title>China, Uzbekistan agree on new gas export deal in Tashkent - Kun.uz</title>
<link>https://news.google.com/rss/articles/CBMiBBB?oc=5</link>
<pubDate>{rfc822(NOW - timedelta(hours=3))}</pubDate><description>x</description>
<source url="https://kun.uz">Kun.uz</source></item>
</channel></rss>""".encode()

TELEGRAM = f"""<html><body>
<div class="tgme_channel_info_header_title"><span dir="auto">Kun.uz | Расмий канал</span></div>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="kunuzofficial/175202">
 <div class="tgme_widget_message_text js-message_text" dir="auto">⚡️Mirziyoyev Pekinga tashrif buyurdi<br/><br/>Prezident Xitoy rahbari bilan savdo va energetika bo'yicha muzokara o'tkazdi.</div>
 <a class="tgme_widget_message_date" href="https://t.me/kunuzofficial/175202"><time datetime="{(NOW - timedelta(minutes=30)).isoformat()}">16:33</time></a>
</div></div>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="kunuzofficial/175201">
 <a class="tgme_widget_message_date"><time datetime="{NOW.isoformat()}">16:30</time></a></div></div>
</body></html>"""

GDELT = {
    "articles": [
        {
            "url": "https://example.com/a?utm_source=x",
            "title": "Kazakhstan oil output rises as pipeline reopens",
            "seendate": (NOW - timedelta(hours=1)).strftime("%Y%m%dT%H%M%SZ"),
            "domain": "example.com",
            "language": "English",
            "sourcecountry": "Kazakhstan",
        },
    ]
}

ATOM = f"""<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>UK FCDO</title>
<entry><title>Foreign Secretary statement on Ukraine ceasefire talks</title>
<link href="https://www.gov.uk/government/news/x"/><updated>{(NOW - timedelta(hours=5)).isoformat()}</updated>
<summary>The Foreign Secretary welcomed talks.</summary></entry></feed>""".encode()


def test_gnews_strips_publisher_and_duplicate_summary():
    items = collectors.parse_gnews(GNEWS)
    assert items[0]["title"] == "Uzbekistan and China sign gas export agreement"
    assert items[0]["publisher"] == "Reuters"
    assert items[0]["summary"] == ""


def test_telegram_message_with_and_without_text():
    items = collectors.parse_telegram(TELEGRAM, "kunuzofficial")
    assert len(items) == 1  # the photo post without a caption is skipped
    it = items[0]
    assert it["title"] == "⚡️Mirziyoyev Pekinga tashrif buyurdi"
    assert it["url"] == "https://t.me/kunuzofficial/175202"
    assert it["publisher"] == "Telegram: Kun.uz | Расмий канал"
    assert "muzokara" in it["summary"]


def test_gdelt_and_tracking_params():
    items = collectors.parse_gdelt(GDELT)
    assert items[0]["url"] == "https://example.com/a"
    assert items[0]["published"].startswith(str(NOW.year))


def test_atom_feed():
    items = collectors.parse_feed(ATOM)
    assert items[0]["publisher"] == "UK FCDO" and items[0]["summary"]


def test_feed_garbage_raises():
    with pytest.raises(collectors.CollectError):
        collectors.parse_feed(b"<html>not a feed")


@pytest.mark.parametrize(
    "text,cats,countries",
    [
        ("US troops strike drone base in Syria", {"conflict", "defense"}, {"USA", "SYR"}),
        ("Warning issued over warm weather in Warsaw", set(), {"POL"}),
        ("Газета пишет о погоде", set(), set()),
        ("Узбекистан и Казахстан подписали соглашение", {"diplomacy", "central-asia"}, {"UZB", "KAZ"}),
        ("O'zbekiston prezidenti Mirziyoyev Xitoyga tashrif buyurdi", {"diplomacy", "central-asia"}, {"UZB", "CHN"}),
        ("Aid convoy reaches Gaza", set(), {"PSE"}),
    ],
)
def test_tagging(text, cats, countries):
    c, k = analyze.tag(text, "")
    assert set(c) == cats and set(k) == countries


def test_cluster_groups_same_story_only():
    arts = [dict(a, publisher=p) for a, p in zip(collectors.parse_gnews(GNEWS), ["Reuters", "Kun.uz"], strict=True)]
    arts.append({"title": "Kazakhstan oil output rises", "publisher": "X", "published": NOW.isoformat()})
    groups = analyze.cluster(arts)
    assert len(groups) == 2 and len(groups[0]) == 2


# ------------------------------------------------------------------ the app end to end


@pytest.fixture
def client(tmp_path, monkeypatch):
    use_test_db(monkeypatch, tmp_path, "t")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    monkeypatch.setenv("WS_SANCTIONS_AUTO", "0")  # no 80 MB downloads in tests
    monkeypatch.setenv("WS_LOOP", "0")  # the test runs refresh itself; a second one would race it
    for m in MODULES:
        sys.modules.pop(m, None)
    import db  # noqa: F401

    app_mod = importlib.import_module("app")

    async def fake(client, value, label, lang, _kind=None):
        return {
            "gnews": collectors.parse_gnews(GNEWS),
            "telegram": collectors.parse_telegram(TELEGRAM, value),
            "gdelt": collectors.parse_gdelt(GDELT),
        }.get(_kind, collectors.parse_feed(ATOM, label))

    async def broken(*a):
        raise collectors.CollectError("HTTP 403")

    monkeypatch.setitem(collectors.FETCHERS, "gnews", lambda *a: fake(*a, _kind="gnews"))
    monkeypatch.setitem(collectors.FETCHERS, "telegram", lambda *a: fake(*a, _kind="telegram"))
    monkeypatch.setitem(collectors.FETCHERS, "gdelt", lambda *a: fake(*a, _kind="gdelt"))
    monkeypatch.setitem(collectors.FETCHERS, "rss", broken)
    monkeypatch.setattr(app_mod, "REFRESH_MIN", 10_000)
    monkeypatch.setenv("WS_EMBED", "0")  # no meaning vectors from the real API in tests

    async def no_facts(a, b):  # tests stay offline; a test that needs figures patches its own
        return {"countries": {}, "trade": None}

    monkeypatch.setattr(app_mod.facts, "pair_facts", no_facts)

    async def no_debt(debtor, creditor):
        return []

    monkeypatch.setattr(app_mod.debt, "bilateral", no_debt)

    from fastapi.testclient import TestClient

    with TestClient(app_mod.app, headers={"origin": "http://testserver"}) as c:
        import asyncio

        asyncio.run(app_mod.refresh())
        yield c, app_mod


def test_pages_render_with_data(client):
    c, _ = client
    r = c.get("/")
    assert r.status_code == 200
    assert "Uzbekistan and China sign gas export agreement" in r.text
    assert "2 ta manba" in r.text  # Reuters and Kun.uz grouped into one story
    assert c.get("/?view=list&country=UZB").status_code == 200
    assert "Mirziyoyev" in c.get("/?kind=telegram").text
    s = c.get("/sources").text
    assert "HTTP 403" in s and "✓" in s
    b = c.get("/brief").text
    assert "ANTHROPIC_API_KEY" in b


def test_dedup_on_second_refresh(client):
    _, app_mod = client
    import asyncio

    assert asyncio.run(app_mod.refresh()) == 0


def test_add_and_validate_sources(client):
    c, _ = client
    r = c.post("/sources/add", data={"kind": "telegram", "value": "https://t.me/s/gazetauz", "lang": "uz"})
    assert r.status_code == 200 and "gazetauz" in r.text
    r = c.post("/sources/add", data={"kind": "telegram", "value": "bad name!", "lang": "uz"})
    assert "noto" in r.text
    r = c.post("/sources/add", data={"kind": "rss", "value": "ftp://x", "lang": "en"})
    assert "http" in r.text


def test_brief_with_fake_model(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")

    class Block:
        type = "text"
        text = "Qisqacha: O'zbekiston va Xitoy gaz bo'yicha kelishdi [1][2]. Noto'g'ri havola [99].\n\n## Asosiy voqealar\n- Kelishuv [1]"

    class Resp:
        content = (Block(),)
        model = "claude-haiku-5-5"

    class FakeClient:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            assert "[1]" in kw["messages"][0]["content"]
            return Resp()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    r = c.post("/brief", data={"lang": "uz", "hours": "24", "focus": ""})
    assert r.status_code == 200
    assert 'class="cite"' in r.text and "[99]" not in r.text
    assert "Manbalar" in r.text


def gated_app(tmp_path, monkeypatch, password="s3cret"):
    use_test_db(monkeypatch, tmp_path, "p")
    monkeypatch.setenv("APP_PASSWORD", password)
    monkeypatch.setenv("WS_LOOP", "0")
    monkeypatch.setenv("WS_SANCTIONS_AUTO", "0")
    for m in MODULES:
        sys.modules.pop(m, None)
    app_mod = importlib.import_module("app")
    return app_mod


def test_setup_password_makes_the_first_admin_then_stops_working(tmp_path, monkeypatch):
    app_mod = gated_app(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    with TestClient(app_mod.app, headers={"origin": "http://testserver"}) as c:
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
        assert c.get("/healthz").status_code == 200
        assert c.get("/api/articles").status_code == 401
        assert "Birinchi sozlash" in c.get("/login").text
        bad = c.post("/login", data={"setup": "wrong", "username": "akhad", "password": "longpassword1",
                                     "password2": "longpassword1"}, follow_redirects=False)  # fmt: skip
        assert "error=" in bad.headers["location"] and not app_mod.accounts.any_users()
        ok = c.post("/login", data={"setup": "s3cret", "username": "akhad", "password": "longpassword1",
                                    "password2": "longpassword1", "next": "/watch"}, follow_redirects=False)  # fmt: skip
        assert ok.status_code == 303 and ok.headers["location"] == "/watch"
        assert app_mod.accounts.COOKIE in ok.cookies
        assert c.get("/").status_code == 200  # the session cookie works
        page = c.get("/admin").text
        assert "akhad" in page and "Rejali ishlar" in page
        c.post("/logout")
        assert c.get("/", follow_redirects=False).status_code == 303
        # the setup password no longer opens anything, a wrong password does not, the right one does
        again = c.post("/login", data={"setup": "s3cret", "username": "intruder", "password": "x" * 10,
                                       "password2": "x" * 10}, follow_redirects=False)  # fmt: skip
        assert "error=" in again.headers["location"]
        assert [u["username"] for u in app_mod.accounts.list_users()] == ["akhad"]
        assert c.get("/", auth=("any", "s3cret"), follow_redirects=False).status_code == 303
        r = c.post("/login", data={"username": "AKHAD", "password": "longpassword1"}, follow_redirects=False)
        assert r.headers["location"] == "/" and c.get("/").status_code == 200


def test_admin_adds_people_and_only_admins_manage(tmp_path, monkeypatch):
    app_mod = gated_app(tmp_path, monkeypatch)
    acc = app_mod.accounts
    from fastapi.testclient import TestClient

    with TestClient(app_mod.app, headers={"origin": "http://testserver"}) as c:
        acc.create("boss", "bosspassword", "admin")
        c.post("/login", data={"username": "boss", "password": "bosspassword"})
        page = c.post("/admin/users", data={"username": "ali", "role": "user"}).text
        temp = re.search(r"Vaqtinchalik parol: <code>([^<]+)</code>", page).group(1)
        assert len(temp) == 12
        me = acc.list_users()[0]
        assert "error=" in c.post(f"/admin/users/{me['id']}/delete", follow_redirects=False).headers["location"]
        c.post("/logout")
        c.post("/login", data={"username": "ali", "password": temp})
        assert c.get("/admin").status_code == 403
        assert c.get("/watch").status_code == 200
        # changing the password ends other sessions but keeps this one
        r = c.post("/account/password", data={"old": temp, "new": "alis-new-pass", "new2": "alis-new-pass"})
        assert "Parol o" in r.text and acc.authenticate("ali", "alis-new-pass")
    for bad in [("ab", "longenough"), ("good_name", "short"), ("same_as_pw", "same_as_pw")]:
        with pytest.raises(acc.AccountError):
            acc.create(*bad)


def test_login_is_throttled(tmp_path, monkeypatch):
    app_mod = gated_app(tmp_path, monkeypatch)
    from fastapi.testclient import TestClient

    with TestClient(app_mod.app, headers={"origin": "http://testserver"}) as c:
        app_mod.accounts.create("boss", "bosspassword", "admin")
        for _ in range(app_mod.accounts.FAIL_LIMIT):
            c.post("/login", data={"username": "boss", "password": "nope"})
        r = c.post("/login", data={"username": "boss", "password": "bosspassword"}, follow_redirects=False)
        assert "error=" in r.headers["location"] and "urinish" in unquote_plus(r.headers["location"])


def test_passwords_and_sessions(tmp_path, monkeypatch):
    app_mod = gated_app(tmp_path, monkeypatch)
    acc = app_mod.accounts
    from fastapi.testclient import TestClient

    with TestClient(app_mod.app):
        h = acc.hash_password("correct horse")
        assert acc.check_password("correct horse", h) and not acc.check_password("wrong", h)
        assert not acc.check_password("x", "garbage")
        u = acc.create("someone", "a-long-password")
        token = acc.start_session(u["id"])
        assert acc.session_user(token)["username"] == "someone"
        stored = app_mod.db.one("SELECT token FROM sessions")["token"]
        assert stored != token and len(stored) == 64  # only the hash is kept
        acc.end_session(token)
        assert acc.session_user(token) is None


def test_undated_item_in_dated_feed_is_dropped():
    feed = ATOM.replace(
        b"</feed>", b"<entry><title>Old undated item</title><link href='https://x/old'/></entry></feed>"
    )
    titles = [i["title"] for i in collectors.parse_feed(feed)]
    assert "Old undated item" not in titles and len(titles) == 1


@pytest.mark.parametrize("text", ["Ферганская область обсудила сотрудничество", "Farg‘ona viloyatida yangi zavod"])
def test_uzbek_regions(text):
    assert "UZB" in analyze.tag(text, "")[1]


GEMINI_MODELS = {
    "models": [
        {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.5-flash", "supportedGenerationMethods": ["generateContent", "countTokens"]},
        {"name": "models/gemini-3.5-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.5-pro", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.8-flash-preview", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-3.5-flash-image", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
    ]
}


def test_pick_gemini_model_prefers_newest_stable_flash():
    assert analyze.pick_gemini_model(GEMINI_MODELS["models"]) == "gemini-3.5-flash"
    assert analyze.pick_gemini_model([GEMINI_MODELS["models"][4]]) == "gemini-3.8-flash-preview"
    assert analyze.pick_gemini_model([]) is None


def _gemini_transport(calls, status=200):
    import httpx

    def handler(request):
        calls.append((request.method, request.url.path, request.headers.get("x-goog-api-key")))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=GEMINI_MODELS)
        if status != 200:
            return httpx.Response(
                status, json={"error": {"message": "API key not valid. Please pass a valid API key."}}
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "thinking…", "thought": True},
                                {"text": "Gemini brifi: kelishuv imzolandi [1]."},
                            ]
                        },
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    real = httpx.Client
    return lambda **kw: real(transport=httpx.MockTransport(handler), **kw)


def test_falls_back_from_claude_without_credit_to_gemini(client, monkeypatch):
    c, _ = client
    import anthropic
    import httpx

    class NoCredit:
        def __init__(self):
            self.messages = self

        def create(self, **kw):
            req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
            body = {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low to access the Anthropic API.",
                },
            }
            raise anthropic.BadRequestError("400", response=httpx.Response(400, request=req), body=body)

    calls = []
    monkeypatch.setattr(anthropic, "Anthropic", NoCredit)
    monkeypatch.setattr(analyze.httpx, "Client", _gemini_transport(calls))
    analyze._gemini_cache.pop("model", None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    r = c.post("/brief", data={"lang": "uz", "hours": "24"})
    assert "Gemini brifi" in r.text and "thinking" not in r.text
    assert "gemini-3.5-flash" in r.text
    assert calls[-1] == ("POST", "/v1beta/models/gemini-3.5-flash:generateContent", "g-key")


def test_all_providers_fail_shows_each_reason(client, monkeypatch):
    c, _ = client
    analyze._gemini_cache.pop("model", None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "bad")
    monkeypatch.setattr(analyze.httpx, "Client", _gemini_transport([], status=400))
    r = c.post("/brief", data={"lang": "uz", "hours": "24"})
    assert "kalit noto" in r.text


def test_busy_gemini_model_falls_through_to_next(monkeypatch):
    import httpx

    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=GEMINI_MODELS)
        if "gemini-3.5-flash:" in request.url.path:
            return httpx.Response(503, json={"error": {"message": "This model is currently experiencing high demand."}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok [1]"}]}}]})

    real = httpx.Client
    monkeypatch.setattr(analyze.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(analyze.time, "sleep", lambda s: None)
    analyze._gemini_cache.clear()
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    text, model = analyze._gemini("sys", "prompt")
    assert text == "ok [1]" and model == "gemini-3.5-flash-lite"
    assert sum("gemini-3.5-flash:" in c for c in calls) == 2  # one retry before moving on


LINKS_JSON = """```json
{"summary": "Markaziy Osiyo ko'p vektorli siyosat yuritmoqda.",
 "links": [
  {"from": "KAZ", "to": "USA", "type": "cooperation", "title": "Qozog'iston AQSh bilan hamkorlikni kengaytirdi",
   "bubble": "Qozog'iston Rossiyaga qaramlikni kamaytirish uchun AQSh bilan aloqalarni kuchaytirmoqda",
   "logic": "Tahlil: ko'p vektorli siyosat.", "evidence": [1, 99, "x"], "confidence": "high"},
  {"from": "UZB", "to": "CHN", "type": "trade", "title": "Gaz", "bubble": "b", "logic": "l", "evidence": [2]},
  {"from": "UZB", "to": "CHN", "type": "trade", "title": "duplicate", "bubble": "b", "logic": "l", "evidence": [2]},
  {"from": "XXX", "to": "USA", "type": "trade", "title": "bad code", "evidence": [1]},
  {"from": "RUS", "to": "UKR", "type": "made-up", "title": "no evidence", "evidence": []},
  {"from": "USA", "to": "USA", "type": "trade", "title": "self", "evidence": [1]}
 ]}
```"""


def test_clean_links_drops_bad_rows():
    items = [{"id": i, "title": f"t{i}", "url": f"https://x/{i}", "publisher": "p"} for i in (1, 2)]
    out = analyze.clean_links(analyze._json_from(LINKS_JSON), items)
    assert [(link["from"], link["to"]) for link in out["links"]] == [("KAZ", "USA"), ("UZB", "CHN")]
    first = out["links"][0]
    assert [e["n"] for e in first["evidence"]] == [1] and first["confidence"] == "high"
    assert out["links"][1]["confidence"] == "medium"  # missing confidence defaults safely


def test_co_mentions_pairs_countries():
    arts = [
        {"title": "a", "url": "u1", "publisher": "A", "countries": ["UZB", "CHN"], "categories": ["trade"]},
        {"title": "b", "url": "u2", "publisher": "B", "countries": ["CHN", "UZB", "KAZ"], "categories": ["trade"]},
        {"title": "c", "url": "u3", "publisher": "C", "countries": ["RUS"], "categories": []},
    ]
    co = analyze.co_mentions(arts)
    assert co[0]["from"] == "CHN" and co[0]["to"] == "UZB" and co[0]["count"] == 2 and co[0]["category"] == "trade"


def test_map_page_and_generation(client, monkeypatch):
    c, _ = client
    r = c.get("/map")
    assert r.status_code == 200 and "leaflet.js" in r.text and "const DATA" in r.text
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seen = {}

    def fake(system, prompt, json_mode=False):
        seen["json"] = json_mode
        assert "ISO 3166-1 alpha-3" in system and "[1]" in prompt
        return LINKS_JSON, "gemini-test"

    monkeypatch.setitem(analyze.PROVIDERS, "gemini", fake)
    r = c.post("/map", data={"lang": "uz", "hours": "48"})
    assert r.status_code == 200 and seen["json"] is True
    assert "Rossiyaga qaramlikni kamaytirish" in r.text and "gemini-test" in r.text
    assert "</script><script>" not in r.text  # data is embedded safely


def test_map_error_when_nothing_usable(client, monkeypatch):
    c, _ = client
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setitem(analyze.PROVIDERS, "gemini", lambda *a: ('{"links": []}', "m"))
    r = c.post("/map", data={"lang": "uz", "hours": "48"})
    assert "bog" in r.text and "warn" in r.text


def test_slow_gemini_model_is_skipped(monkeypatch):
    import httpx

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=GEMINI_MODELS)
        if "gemini-3.5-flash:" in request.url.path:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    real = httpx.Client
    monkeypatch.setattr(analyze.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    analyze._gemini_cache.clear()
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert analyze._gemini("s", "p") == ("ok", "gemini-3.5-flash-lite")


@pytest.mark.parametrize(
    "a,b,text,bad",
    [
        ("TUR", "UKR", "Belarus rahbariyati Ukraina mavzusida fikr bildirdi, Minsk muvozanat saqlamoqda", True),
        ("KAZ", "CHN", "Qozog'iston va Xitoy energetika markazini tashkil etmoqda", False),
        ("JOR", "UZB", "O‘zbekiston va Iordaniya strategik sherikligi", False),
        ("KAZ", "RUS", "Tokayev Putin bilan uchrashdi; Qozog'iston Xitoy bilan ham muvozanat saqlaydi", False),
        ("CHE", "UZB", "Shveysariya O'zbekistonga mablag'larni qaytarmoqda", False),
    ],
)
def test_mismatched_links(a, b, text, bad):
    assert analyze.mismatched(a, b, text) is bad


def test_map_retries_broken_json(monkeypatch):
    replies = iter(['{"links": [ broken', LINKS_JSON])
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(analyze.PROVIDERS, "gemini", lambda *a: (next(replies), "m"))
    arts = [
        {
            "id": i,
            "title": f"Kazakhstan US talks {i}",
            "url": f"https://x/{i}",
            "publisher": f"P{i}",
            "published": "2026-11-26T00:00:00+00:00",
            "summary": "",
            "countries": ["KAZ", "USA"],
            "categories": [],
        }
        for i in (1, 2)
    ]
    out = analyze.write_links(arts, "uz")
    assert out["links"] and out["model"] == "m"


def test_196_states_and_tiers():
    import pairs

    assert len(pairs.SOVEREIGN) == 196 == len(set(pairs.SOVEREIGN))
    assert all(c in analyze.COUNTRY_INFO for c in pairs.SOVEREIGN)
    assert pairs.tier("USA") == 1 and pairs.tier("AUS") == 2 and pairs.tier("MDG") == 3
    assert set(pairs.SOVEREIGN) >= pairs.TIER_1 | pairs.TIER_2
    opts = pairs.country_options()
    assert len(opts) == 196 and opts[0]["tier"] == 1 and opts[-1]["tier"] == 3
    assert pairs.key("UZB", "AUS", "uz") == pairs.key("AUS", "UZB", "uz") == "AUS-UZB-uz"


PAIR_JSON = """{"score": 2, "label": "Uzoq, lekin do'stona", "essence": "Avstraliya O'zbekistonda ta'lim va konchilik imkoniyatini ko'radi.",
 "summary": "Ta'lim aloqasi o'smoqda [1], eski da'vo [9].", "asymmetry": "a",
 "good": [{"area": "Ta'lim", "text": "Talabalar", "evidence": [1, 77]}, {"area": "Konchilik", "text": "Investitsiya", "evidence": []}],
 "bad": [{"area": "Masofa", "text": "Uzoq", "evidence": ["x"]}],
 "areas": [{"area": "Diplomatiya", "state": "positive", "note": "n"}, {"area": "Xavfsizlik", "state": "weird", "note": "n"}],
 "third_parties": [{"iso3": "CHN", "role": "r"}, {"iso3": "UZB", "role": "self"}, {"iso3": "ZZZ", "role": "bad"}],
 "scenarios": [{"title": "Asosiy", "probability": "high", "horizon": "1-2 yil", "trigger": "t", "outcome": "o"},
               {"title": "Yaxshi", "probability": "medium", "outcome": "o"}, {"title": "Yomon", "probability": "nope", "outcome": "o"}],
 "watch": ["w1"], "data_note": "kam xabar"}"""


FACTS = {
    "countries": {
        "AUS": {"NY.GDP.MKTP.CD": {"value": 1.8e12, "year": "2025"}},
        "UZB": {"NY.GDP.MKTP.CD": {"value": 1.47e11, "year": "2025"}},
    },
    "trade": {"year": "2024", "a_to_b": 162_700.0, "b_to_a": 1_200_000.0},
}


async def fake_facts(a, b):
    return FACTS


def test_pair_page_flow(client, monkeypatch):
    import pairs

    c, app_mod = client
    r = c.get("/pair")
    assert r.status_code == 200 and "Qudratli davlatlar" in r.text and "Madagascar" in r.text
    assert c.get("/pair?a=UZB&b=AUS").status_code == 200

    async def fake_news(a, b):
        return [
            collectors.item("Australia and Uzbekistan sign education deal", "https://x/au", "", "ABC", NOW.isoformat())
        ]

    monkeypatch.setattr(pairs, "pair_news", fake_news)
    monkeypatch.setattr(app_mod.facts, "pair_facts", fake_facts)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seen = {}

    def fake_ai(system, prompt, json_mode=False):
        seen["prompt"] = prompt
        return PAIR_JSON, "gemini-test"

    monkeypatch.setitem(analyze.PROVIDERS, "gemini", fake_ai)
    r = c.post("/pair", data={"a": "UZB", "b": "AUS", "lang": "uz"})
    assert r.status_code == 200
    assert "middle power" in seen["prompt"] and "developing country" in seen["prompt"]
    assert "education deal" in seen["prompt"]
    t = r.text
    assert "Uzoq, lekin do" in t and "+2" in t and "konchilik imkoniyatini" in t
    assert 'href="#s1"' in t and "#s77" not in t and "umumiy" in t
    assert "o'smoqda" not in t or "[1]" not in t  # summary citations are links, not raw brackets
    assert "[9]" not in t and "#s9" not in t
    assert "Xitoy:" in t and "ZZZ" not in t  # unknown third party dropped, Uzbek name used
    assert c.get("/pair?a=AUS&b=UZB").text.count("konchilik imkoniyatini") == 1  # same cache both ways


def test_pair_rejects_same_or_unknown_country(client):
    c, _ = client
    assert "Ikki xil" in c.post("/pair", data={"a": "UZB", "b": "UZB"}).text
    assert "Ikki xil" in c.post("/pair", data={"a": "UZB", "b": "HKG"}).text  # not a sovereign state


def test_quota_on_one_gemini_model_moves_to_next(monkeypatch):
    import httpx

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=GEMINI_MODELS)
        if "gemini-3.5-flash:" in request.url.path:
            return httpx.Response(429, json={"error": {"message": "Quota exceeded"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    real = httpx.Client
    monkeypatch.setattr(analyze.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    analyze._gemini_cache.clear()
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert analyze._gemini("s", "p") == ("ok", "gemini-3.5-flash-lite")


def test_links_must_be_web_addresses():
    assert collectors.item("t", "javascript:alert(1)") is None
    assert collectors.item("t", "data:text/html,x") is None
    assert collectors.item("t", "https://ok.example/a")["url"] == "https://ok.example/a"
    feed = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
    <item><title>Bad</title><link>javascript:alert(1)</link></item>
    <item><title>Good</title><link>https://ok.example/b</link></item></channel></rss>"""
    assert [i["title"] for i in collectors.parse_feed(feed)] == ["Good"]


def test_cross_site_posts_are_refused(client):
    c, _ = client
    from fastapi.testclient import TestClient

    raw = TestClient(c.app)
    assert raw.post("/sources/1/delete").status_code == 403  # no Origin at all
    assert raw.post("/sources/1/delete", headers={"origin": "https://evil.example"}).status_code == 403
    assert raw.post("/sources/1/delete", headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert raw.post("/refresh", headers={"origin": "null"}).status_code == 403
    ok = raw.post("/sources/1/toggle", headers={"sec-fetch-site": "same-origin"}, follow_redirects=False)
    assert ok.status_code == 303
    r = c.get("/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "same-origin"


def test_refresh_redirect_stays_on_site(client):
    c, _ = client
    r = c.post("/refresh", headers={"referer": "https://evil.example/x"}, follow_redirects=False)
    assert r.headers["location"] == "/"
    r = c.post("/refresh", headers={"referer": "http://testserver/map?id=2"}, follow_redirects=False)
    assert r.headers["location"] == "/map?id=2"


def test_ai_daily_limit_and_single_job(client, monkeypatch):
    import asyncio

    c, app_mod = client
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(analyze.PROVIDERS, "gemini", lambda *a: ("Brif [1].", "m"))
    monkeypatch.setattr(app_mod, "AI_DAILY_LIMIT", 1)
    assert "Brif" in c.post("/brief", data={"lang": "uz", "hours": "24"}).text
    r = c.post("/brief", data={"lang": "uz", "hours": "24"})
    assert "limiti tugadi" in r.text and "AI: 1/1" in r.text

    async def busy():
        async with app_mod._ai_lock:
            app_mod.state["ai_job"] = "xarita"
            with pytest.raises(analyze.AIError, match="boshqa AI ish"):
                await app_mod.run_ai("x", lambda: None)

    asyncio.run(busy())


def test_new_default_sources_reach_old_databases_but_deleted_stay_deleted(tmp_path, monkeypatch):
    use_test_db(monkeypatch, tmp_path, "seed")
    sys.modules.pop("db", None)
    import db

    db.init(
        [
            {"kind": "rss", "value": "https://a/rss", "label": "A"},
            {"kind": "rss", "value": "https://b/rss", "label": "B"},
        ]
    )
    with db.tx() as c:
        c.execute("DELETE FROM sources WHERE value = ?", ("https://b/rss",))
    db.init(
        [
            {"kind": "rss", "value": "https://a/rss", "label": "A"},
            {"kind": "rss", "value": "https://b/rss", "label": "B"},
            {"kind": "rss", "value": "https://c/rss", "label": "C", "enabled": False},
        ]
    )
    got = {r["value"]: r["enabled"] for r in db.rows("SELECT value, enabled FROM sources")}
    assert got == {"https://a/rss": 1, "https://c/rss": 0}
    db.cache_put("k", {"x": 1})
    assert db.cache_get("k", 1) == {"x": 1} and db.cache_get("nope", 1) is None


def test_pair_history_previous_check_and_triangle(client, monkeypatch):
    import pairs

    c, app_mod = client

    async def no_news(a, b):
        return []

    monkeypatch.setattr(pairs, "pair_news", no_news)
    monkeypatch.setattr(app_mod.facts, "pair_facts", fake_facts)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    prompts = []
    import json as _json

    second = _json.loads(PAIR_JSON)
    second.update(
        score=4,
        previous_check={"unfolding": "Asosiy", "verdict": "Asosiy ssenariy amalga oshmoqda.", "change": "c"},
        triangle={
            "summary": "Xitoy omili",
            "a_c": {"score": 3, "note": "n"},
            "b_c": {"score": "x", "note": "n"},
            "who_benefits": "w",
            "leverage": "l",
        },
    )
    replies = iter([PAIR_JSON, _json.dumps(second), _json.dumps(second)])

    def fake_ai(system, prompt, json_mode=False):
        prompts.append((system, prompt))
        return next(replies), "m"

    monkeypatch.setitem(analyze.PROVIDERS, "gemini", fake_ai)
    c.post("/pair", data={"a": "UZB", "b": "AUS", "lang": "uz"})
    assert "Hard figures" in prompts[0][1] and "1.8 trln $" in prompts[0][1] and "previous_check" not in prompts[0][0]
    t = c.post("/pair", data={"a": "AUS", "b": "UZB", "lang": "uz"}).text  # same pair, reversed
    assert (
        "Earlier analysis of" in prompts[1][1]
        and "Asosiy (high)" in prompts[1][1]
        and "previous_check" in prompts[1][0]
    )
    assert "Oldingi bashorat tekshiruvi" in t and "Asosiy ssenariy amalga oshmoqda" in t
    assert "Munosabat tarixi" in t and "<polyline" in t
    assert "Raqamlarda" in t and "162.7 ming $" in t and "1.2 mln $" in t
    t = c.post("/pair", data={"a": "UZB", "b": "AUS", "c": "CHN", "lang": "uz"}).text
    assert "Country C (third party)" in prompts[2][1] and "triangle" in prompts[2][0]
    assert "Uchburchak" in t and "Xitoy omili" in t
    assert (
        "Ikki xil" in c.post("/pair", data={"a": "UZB", "b": "AUS", "c": "UZB"}).text
        or "farq" in c.post("/pair", data={"a": "UZB", "b": "AUS", "c": "UZB"}).text
    )


def test_facts_helpers():
    import facts

    assert (
        facts.human(1.8e12, "usd") == "1.8 trln $"
        and facts.human(14.36, "pct") == "14.4%"
        and facts.human(None, "usd") == "—"
    )
    rows = facts.rows_for(FACTS, "UZB", "AUS")
    assert rows[0]["a"] == "147.0 mlrd $" and rows[0]["b"] == "1.8 trln $"
    block = facts.prompt_block(FACTS, "UZB", "AUS", {"UZB": "O'zbekiston", "AUS": "Avstraliya"})
    assert "O'zbekiston→Avstraliya 162.7 ming $" in block


# ------------------------------------------------------------------ Telegram bot and scheduled jobs


@pytest.fixture
def bot(client, monkeypatch):
    c, app_mod = client
    telegram_bot = app_mod.telegram_bot
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr(app_mod, "CRON_TOKEN", "cron-secret")
    sent = []

    async def fake_api(method, **params):
        sent.append((method, params))
        return {"ok": True}

    monkeypatch.setattr(telegram_bot, "api", fake_api)
    return c, app_mod, telegram_bot, sent


def tg(c, bot_mod, text, chat=42):
    upd = {"message": {"chat": {"id": chat, "first_name": "Ali"}, "text": text}}
    return c.post("/telegram/webhook", json=upd, headers={"x-telegram-bot-api-secret-token": bot_mod.webhook_secret()})


def texts(sent):
    return [p["text"] for m, p in sent if m == "sendMessage"]


def test_webhook_needs_telegram_secret(bot):
    c, _, bot_mod, _sent = bot
    from fastapi.testclient import TestClient

    raw = TestClient(c.app)
    assert raw.post("/telegram/webhook", json={}).status_code == 403
    assert (
        raw.post("/telegram/webhook", json={}, headers={"x-telegram-bot-api-secret-token": "wrong"}).status_code == 403
    )
    assert tg(raw, bot_mod, "/start").status_code == 200  # no site password or Origin needed
    assert (
        raw.get("/cron/refresh").status_code == 404
        and raw.get("/cron/refresh", headers={"x-cron-token": "x"}).status_code == 404
    )


def test_bot_commands(bot):
    c, _app, bot_mod, sent = bot
    import db

    tg(c, bot_mod, "/start")
    assert db.one("SELECT active FROM tg_subscribers WHERE chat_id = '42'")["active"] == 1
    assert "Xush kelibsiz" in texts(sent)[-1]
    tg(c, bot_mod, "/kuzat O'zbekiston janubiy koreya")
    assert "Kuzatuvga olindi" in texts(sent)[-1] and "Janubiy Koreya" in texts(sent)[-1]
    tg(c, bot_mod, "/kuzat aqsh rossiya")
    tg(c, bot_mod, "/kuzatuvlar")
    assert "AQSh ↔ Rossiya" in texts(sent)[-1] and "O'zbekiston ↔ Janubiy Koreya" in texts(sent)[-1]
    tg(c, bot_mod, "/kuzat Atlantida Mars")
    assert "Ikki davlatni yozing" in texts(sent)[-1]
    tg(c, bot_mod, "/bekor USA RUS")
    assert db.one("SELECT COUNT(*) n FROM tg_follows")["n"] == 1
    tg(c, bot_mod, "/bugun")
    assert "hali tayyor emas" in texts(sent)[-1]
    tg(c, bot_mod, "/stop")
    assert db.one("SELECT active FROM tg_subscribers WHERE chat_id = '42'")["active"] == 0


def test_daily_digest_job(bot, monkeypatch):
    c, _app, bot_mod, sent = bot
    tg(c, bot_mod, "/start", chat=1)
    tg(c, bot_mod, "/start", chat=2)
    sent.clear()
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setitem(
        analyze.PROVIDERS,
        "gemini",
        lambda *a: (
            '{"items": [{"n": 1, "emoji": "⚡", "title": "Gaz kelishuvi <b>", "essence": "Xitoy bozori kengaymoqda."}, {"n": 99}]}',
            "m",
        ),
    )
    r = c.post("/cron/digest", headers={"x-cron-token": "cron-secret"}).json()
    assert r["sent"] == 2 and r["model"] == "m"
    body = texts(sent)[0]
    assert "⚡ <b>Gaz kelishuvi &lt;b&gt;</b>" in body and "Xitoy bozori" in body and 'href="https://' in body
    assert c.post("/cron/digest", headers={"x-cron-token": "cron-secret"}).json() == {
        "skipped": "bugungi digest allaqachon yuborilgan"
    }
    tg(c, bot_mod, "/bugun", chat=1)
    assert "Gaz kelishuvi" in texts(sent)[-1]


def test_digest_without_ai_sends_headlines(bot, monkeypatch):
    c, _app, bot_mod, sent = bot
    tg(c, bot_mod, "/start")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = c.post("/cron/digest?force=1", headers={"x-cron-token": "cron-secret"}).json()
    assert r["sent"] == 1 and "AI o'chiq" in texts(sent)[-1]


def test_weekly_job_alerts_when_relationship_moves(bot, monkeypatch):
    import json as _json

    import pairs

    c, app_mod, bot_mod, sent = bot

    async def no_news(a, b):
        return []

    monkeypatch.setattr(pairs, "pair_news", no_news)
    monkeypatch.setattr(app_mod.facts, "pair_facts", fake_facts)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    worse = _json.loads(PAIR_JSON) | {
        "score": -1,
        "label": "Sovuqlashuv",
        "previous_check": {"unfolding": "Yomon", "verdict": "v", "change": "Vizalar to'xtatildi."},
    }
    replies = iter([PAIR_JSON, _json.dumps(worse | {"score": 1}), _json.dumps(worse)])
    monkeypatch.setitem(analyze.PROVIDERS, "gemini", lambda *a: (next(replies), "m"))
    tg(c, bot_mod, "/kuzat UZB AUS")
    sent.clear()
    cron = {"x-cron-token": "cron-secret"}
    r = c.post("/cron/weekly", headers=cron).json()
    assert r["analysed"] == ["AUS-UZB-uz"] and "Birinchi tahlil tayyor" in texts(sent)[-1]
    sent.clear()
    assert c.post("/cron/weekly", headers=cron).json()["alerts"] == 0  # +2 → +1: small move, no alert
    r = c.post("/cron/weekly", headers=cron).json()  # +1 → -1: alert
    assert r["alerts"] == 1
    alert = texts(sent)[-1]
    assert "munosabat o'zgardi" in alert and "+1" in alert and "<b>-1</b>" in alert and "Vizalar to'xtatildi" in alert
    import db

    assert [h["score"] for h in db.rows("SELECT score FROM pair_history ORDER BY id")] == [2, 1, -1]


def test_resolve_country_words():
    import telegram_bot

    assert telegram_bot.resolve("uzb") == "UZB" and telegram_bot.resolve("O‘zbekiston") == "UZB"
    assert telegram_bot.resolve("AQSh") == "USA" and telegram_bot.resolve("United States") == "USA"
    assert telegram_bot._two_countries("Janubiy Koreya Yaponiya") == ("KOR", "JPN")
    assert telegram_bot.resolve("Atlantida") is None


def test_meaning_vectors_join_languages_but_not_different_stories():
    import embed

    base = {"published": NOW.isoformat(), "categories": []}
    v = lambda *xs: embed.pack(list(xs) + [0.0] * (embed.DIM - len(xs)))  # noqa: E731
    arts = [
        base
        | {
            "title": "World Bank approves $800 million for Uzbekistan",
            "publisher": "Reuters",
            "countries": ["UZB"],
            "lang": "en",
            "emb": v(1, 0.05),
        },
        base
        | {
            "title": "Всемирный банк одобрил 800 млн долларов Узбекистану",
            "publisher": "Gazeta",
            "countries": ["UZB"],
            "lang": "ru",
            "emb": v(1, 0.0),
        },
        base
        | {
            "title": "Uzbekistan raises central bank rate to 14%",
            "publisher": "Spot",
            "countries": ["UZB"],
            "lang": "en",
            "emb": v(1, 0.1),
        },  # close in meaning, but the numbers disagree
        base
        | {
            "title": "Россия и Украина обменялись ударами дронов",
            "publisher": "BBC",
            "countries": ["RUS", "UKR"],
            "lang": "ru",
            "emb": v(0, 1),
        },
    ]
    groups = analyze.cluster(arts)
    sizes = sorted(len(g) for g in groups)
    assert sizes == [1, 1, 2]
    pair = next(g for g in groups if len(g) == 2)
    assert {a["publisher"] for a in pair} == {"Reuters", "Gazeta"} and pair[0]["lang"] == "en"
    assert analyze.cluster([dict(a, emb=None) for a in arts])  # no vectors: token grouping only


def test_refresh_stores_vectors(client, monkeypatch):
    import embed

    c, app_mod = client
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(embed, "embed", lambda texts: [[0.1] * embed.DIM for _ in texts])
    import db

    with db.tx() as conn:
        conn.execute("UPDATE articles SET emb = NULL")
    assert app_mod._embed_new() > 0
    since = (datetime.now(UTC) - timedelta(hours=36)).isoformat()
    assert db.one("SELECT COUNT(*) n FROM articles WHERE emb IS NULL AND published >= ?", (since,))["n"] == 0
    assert "emb" not in c.get("/api/articles?hours=168").json()[0]


def test_pair_page_shows_figures_before_any_analysis(client, monkeypatch):
    c, app_mod = client
    monkeypatch.setattr(app_mod.facts, "pair_facts", fake_facts)
    t = c.get("/pair?a=UZB&b=AUS").text
    assert "Hali AI tahlili yo" in t and "Raqamlarda" in t and "147.0 mlrd $" in t and "162.7 ming $" in t


# ------------------------------------------------------------------ debts

DEBT_DATA = {
    "debt": {
        "DT.DOD.DECT.CD": [[2010, 8.0e9], [2018, 18.3e9], [2025, 88.0e9]],
        "DT.DOD.DECT.GN.ZS": [[2010, 14.2], [2025, 59.7]],
    },
    "outcomes": {
        "SE.XPD.TOTL.GD.ZS": [[2013, 5.74], [2023, 5.47]],
        "SI.POV.LMIC": [[2021, 9.1], [2025, 4.0]],
        "GOV_WGI_CC.EST": [[2010, -1.36], [2025, -0.69]],
        "BX.KLT.DINV.WD.GD.ZS": [[2010, 3.01], [2025, 3.02]],
    },
    "creditors": {
        "year": "2025",
        "total": 30e9,
        "rows": [
            {"code": "BND", "name": "Bondholders", "value": 11.8e9, "year": "2025", "iso": "", "kind": "bonds"},
            {
                "code": "915",
                "name": "Asian Dev. Bank",
                "value": 8.4e9,
                "year": "2025",
                "iso": "",
                "kind": "institution",
            },
            {"code": "730", "name": "China", "value": 4.0e9, "year": "2025", "iso": "CHN", "kind": "country"},
        ],
    },
    "projects": [
        {
            "id": "P1",
            "name": "Social Protection System",
            "date": "2024-05-24",
            "amount": 100e6,
            "status": "Active",
            "sector": "Social Protection",
            "url": "https://projects.worldbank.org/x",
        }
    ],
    "fetched": "2026-11-26T00:00:00+00:00",
}

DEBT_JSON = """{"summary": "Qarz tez o'sdi.", "sustainability": {"level": "moderate", "note": "59.7% YaMD"},
 "creditors": [{"name": "Asian Dev. Bank", "logic": "Infratuzilma loyihalari"}],
 "uses": [{"what": "Ijtimoiy himoya tizimi", "lender": "Jahon banki", "evidence": [1]}],
 "effects": [{"area": "education", "state": "worsened", "finding": "Ta'lim xarajati 5.7% dan 5.5% ga tushdi.", "evidence": []},
             {"area": "corruption", "state": "mixed", "finding": "Tender ishi xabar qilindi.", "evidence": [2]},
             {"area": "bogus", "state": "improved", "finding": "x", "evidence": []}],
 "red_flags": [{"text": "Yo'l qurilishi tenderida firibgarlik gumoni xabar qilindi", "evidence": [2]},
               {"text": "Manbasiz ayblov", "evidence": []}],
 "watch": ["Yevroobligatsiyalar"], "data_note": "yetarli"}"""


def test_debt_view_and_prompt():
    import debt

    v = debt.view(json.loads(json.dumps(DEBT_DATA)))
    assert v["debt_rows"][0]["then"] == "8.0 mlrd $" and v["debt_rows"][0]["now"] == "88.0 mlrd $"
    labels = [r["label"] for r in v["creditors"]["rows"]]
    assert labels == ["Obligatsiya egalari (yevroobligatsiyalar)", "Osiyo taraqqiyot banki (ADB)", "Xitoy"]
    out = {r["label"][:10]: r["better"] for r in v["outcome_rows"]}
    assert out["Ta'lim xar"] is False and out["Qashshoqli"] is True and out["Korrupsiya"] is True
    assert out["Xorijiy to"] is None  # 3.01 → 3.02 is no real change
    block = debt.prompt_block("Uzbekistan", DEBT_DATA)
    assert "Bondholders 11.8 mlrd $" in block and "2010: 8.0 mlrd $" in block and "Social Protection System" in block


def test_debt_report_cleaning():
    import debt

    items = [
        {"title": "WB loan", "url": "https://x/1", "publisher": "Reuters", "published": "2026-11-01T00:00:00+00:00"},
        {
            "title": "Tender probe",
            "url": "https://x/2",
            "publisher": "Kun.uz",
            "published": "2026-11-02T00:00:00+00:00",
        },
    ]
    r = debt.clean_report(json.loads(DEBT_JSON), items)
    assert [e["area"] for e in r["effects"]] == [k for k, _ in debt.AREAS]  # all seven, in order, bogus dropped
    assert r["effects"][1]["state"] == "mixed" and r["effects"][2]["state"] == "unclear"
    assert [f["text"][:5] for f in r["red_flags"]] == ["Yo'l "]  # an accusation with no report is dropped
    assert [s["n"] for s in r["sources"]] == [1, 2]


def test_debt_page_and_report(client, monkeypatch):
    c, app_mod = client
    debt = app_mod.debt

    async def fake_country(iso):
        return json.loads(json.dumps(DEBT_DATA))

    async def fake_news(iso):
        return [
            {
                "id": -1,
                "title": "World Bank approves social protection loan",
                "url": "https://x/1",
                "publisher": "Reuters",
                "published": NOW.isoformat(),
                "summary": "",
            },
            {
                "id": -2,
                "title": "Road tender probe opened",
                "url": "https://x/2",
                "publisher": "Kun.uz",
                "published": NOW.isoformat(),
                "summary": "",
            },
        ]

    monkeypatch.setattr(debt, "country_debt", fake_country)
    monkeypatch.setattr(debt, "debt_news", fake_news)
    t = c.get("/debt").text
    assert "Davlatni tanlang" in t and "Qarzlar" in t
    t = c.get("/debt?c=UZB").text
    assert "Qarz dinamikasi" in t and "Osiyo taraqqiyot banki" in t and 'href="/pair?a=UZB&b=CHN"' in t
    assert "Social Protection System" in t and "▼ yomonlashgan" in t and "▲ yaxshilangan" in t
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    seen = {}

    def fake_ai(system, prompt, json_mode=False):
        seen["p"] = prompt
        return DEBT_JSON, "gemini-test"

    monkeypatch.setitem(analyze.PROVIDERS, "gemini", fake_ai)
    t = c.post("/debt", data={"c": "UZB", "lang": "uz"}).text
    assert "Hard figures" in seen["p"] and "Road tender probe" in seen["p"]
    assert "Qarzdan keyin sohalar bo" in t and "Tender ishi xabar qilindi" in t and "firibgarlik gumoni" in t
    assert "Manbasiz ayblov" not in t and "sud qarori bo" in t and "qarz xavfi: o" in t
    assert "Davlat xaridlari" in t and "Tibbiyot" in t


def test_pair_page_shows_mutual_debt(client, monkeypatch):
    c, app_mod = client
    debt = app_mod.debt

    async def fake_bilateral(debtor, creditor):
        return [[2014, 1.09e9], [2025, 3.97e9]] if (debtor, creditor) == ("UZB", "CHN") else []

    monkeypatch.setattr(debt, "bilateral", fake_bilateral)
    t = c.get("/pair?a=UZB&b=CHN").text
    assert "O&#39;zbekistonning Xitoyga qarzi" in t and "4.0 mlrd $" in t and "/debt?c=UZB" in t


def test_bot_debt_command(bot):
    c, _app, bot_mod, sent = bot
    import db

    tg(c, bot_mod, "/qarz Pokiston")
    assert "AI tahlili hali yo" in texts(sent)[-1] and "/debt?c=PAK" in texts(sent)[-1]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO debt_reports (key, iso, lang, created, data) VALUES (?,?,?,?,?)",
            ("UZB-uz", "UZB", "uz", "2026-11-26", DEBT_JSON.replace('"bogus"', '"health"')),
        )
    tg(c, bot_mod, "/qarz uzb")
    t = texts(sent)[-1]
    assert "Qarz tez o" in t and "▼ <b>Ta'lim</b>" in t and "/debt?c=UZB" in t
    tg(c, bot_mod, "/qarz")
    assert "Davlatni yozing" in texts(sent)[-1]


# ------------------------------------------------------------------ sanctions, risk index, framing

OFAC_SDN = """36,"AEROCARIBBEAN AIRLINES",-0- ,"CUBA",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0-
9001,"IVANOV, Pyotr Sergeyevich","individual","RUSSIA-EO14024",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"DOB 01 Jan 1970; nationality Russia; Gender Male."
9002,"TASHKENT DUAL USE TRADING LLC",-0- ,"RUSSIA-EO14024] [UKRAINE-EO13662",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0-
9003,"SEA STAR",vessel,"IRAN",-0- ,"ABCD",-0- ,-0- ,-0- ,"Panama",-0- ,-0-
"""
OFAC_ADD = """36,25,-0- ,"Havana","Cuba",-0-
9002,77,"Amir Temur 1","Tashkent","Uzbekistan",-0-
"""
OFAC_ALT = """9002,5,"aka","TDUT LLC",-0-
"""
UN_XML = """<?xml version="1.0" encoding="UTF-8"?><CONSOLIDATED_LIST><INDIVIDUALS><INDIVIDUAL>
<DATAID>1</DATAID><FIRST_NAME>KIM</FIRST_NAME><SECOND_NAME>CHOL</SECOND_NAME><UN_LIST_TYPE>DPRK</UN_LIST_TYPE>
<REFERENCE_NUMBER>KPi.001</REFERENCE_NUMBER><LISTED_ON>2016-03-02</LISTED_ON>
<NATIONALITY><VALUE>Democratic People's Republic of Korea</VALUE></NATIONALITY>
<INDIVIDUAL_ALIAS><QUALITY>Good</QUALITY><ALIAS_NAME>KIM CHOL-SU</ALIAS_NAME></INDIVIDUAL_ALIAS>
<INDIVIDUAL_ADDRESS><COUNTRY/></INDIVIDUAL_ADDRESS></INDIVIDUAL></INDIVIDUALS>
<ENTITIES><ENTITY><DATAID>2</DATAID><FIRST_NAME>KOREA MINING CORP</FIRST_NAME><UN_LIST_TYPE>DPRK</UN_LIST_TYPE>
<REFERENCE_NUMBER>KPe.001</REFERENCE_NUMBER><LISTED_ON>2009-04-24</LISTED_ON>
<ENTITY_ADDRESS><COUNTRY>Democratic People's Republic of Korea</COUNTRY></ENTITY_ADDRESS></ENTITY></ENTITIES>
</CONSOLIDATED_LIST>"""
EU_CSV = (
    "﻿Entity_LogicalId;Entity_DesignationDate;Entity_SubjectType_ClassificationCode;Entity_Regulation_Programme;"
    "NameAlias_WholeName;Address_CountryIso2Code;Citizenship_CountryIso2Code\n"
    "500;2024-06-24;P;RUS;Рустам МУМИНОВ;;UZ\n"
    "500;2024-06-24;P;RUS;Rustam MUMINOV;RU;\n"
    "501;2022-03-01;E;BLR;Belaruskali;BY;\n"
)
UK_CSV = (
    "Report Date: 26-Nov-2026\n"
    "Last Updated,Unique ID,Name 6,Name 1,Name 2,Name 3,Name 4,Name 5,Name type,Regime Name,Designation Type,"
    "Address Country,Nationality(/ies),Date Designated\n"
    "01/01/2026,RUS0001,MUMINOV,Rustam,,,,,Primary Name,The Russia (Sanctions) (EU Exit) Regulations 2019,"
    "Individual,Uzbekistan,(1) Russia (2) Uzbekistan,24/02/2025\n"
    "01/01/2026,RUS0001,MUMINOV,R.,,,,,Alias,The Russia (Sanctions) (EU Exit) Regulations 2019,Individual,,,24/02/2025\n"
    "01/01/2026,RUS0002,FARGONA KIMYO ZAVODI LLC,,,,,,Primary Name,The Russia (Sanctions) (EU Exit) Regulations 2019,"
    "Entity,Uzbekistan,,24/02/2025\n"
)


def load_sample_lists(app_mod, tmp_path):
    S = app_mod.sanctions
    files = {}
    for name, body in {
        "sdn": OFAC_SDN,
        "add": OFAC_ADD,
        "alt": OFAC_ALT,
        "un": UN_XML,
        "eu": EU_CSV,
        "uk": UK_CSV,
    }.items():
        files[name] = tmp_path / name
        files[name].write_text(body, encoding="utf-8")
    S.store("OFAC", S.parse_ofac(files["sdn"], files["add"], files["alt"]))
    S.store("UN", S.parse_un(files["un"]))
    S.store("EU", S.parse_eu(files["eu"]))
    S.store("UK", S.parse_uk(files["uk"]))
    S.compute_stats()
    return S


def test_sanctions_parsers_and_countries(client, tmp_path):
    _, app_mod = client
    S = load_sample_lists(app_mod, tmp_path)
    assert S.country_iso("Russian Federation") == "RUS"
    assert S.country_iso("Crimea region of Ukraine") == "UKR"
    assert S.country_iso("South Sudan") == "SSD" and S.country_iso("Sudan") == "SDN"
    assert S.country_iso("-0-") is None
    rows = {r["uid"]: r for r in app_mod.db.rows("SELECT * FROM sanctions")}
    assert rows["9001"]["kind"] == "person" and rows["9001"]["countries"].split() == ["RUS"]  # from the remarks
    assert rows["9002"]["countries"].split() == ["UZB"] and "TDUT LLC" in rows["9002"]["aliases"]
    assert rows["9002"]["programs"] == "RUSSIA-EO14024; UKRAINE-EO13662"
    assert rows["9003"]["kind"] == "vessel" and "PAN" in rows["9003"]["countries"]  # flag state
    assert rows["KPi.001"]["listed"] == "2016-03-02" and rows["KPi.001"]["countries"].split() == ["PRK"]
    assert rows["500"]["countries"].split() == ["RUS", "UZB"] and rows["500"]["name"] == "Рустам МУМИНОВ"
    assert rows["RUS0001"]["programs"] == "Russia" and rows["RUS0001"]["listed"] == "2025-02-24"
    st = S.stats()
    assert st["UZB"]["total"] == 4 and st["UZB"]["lists"] == {"OFAC": 1, "EU": 1, "UK": 2}
    assert S.totals()["all"] == 10


def test_sanctions_search_and_pages(client, tmp_path):
    c, app_mod = client
    S = load_sample_lists(app_mod, tmp_path)
    hits = S.search("Муминов")  # Cyrillic finds the Latin spellings too
    assert {h["list"] for h in hits} == {"EU", "UK"}
    assert S.search("Rustam Muminov")[0]["score"] >= 0.95
    assert S.search("Muminov", country="UZB", lst="UK")[0]["uid"] == "RUS0001"
    assert S.search("Pyotr Ivanovv")  # a typo still finds the stem
    assert S.search("x") == []
    page = c.get("/sanctions?q=muminov").text
    assert "MUMINOV" in page and "yuridik maslahat emas" in page
    country = c.get("/sanctions?c=UZB").text
    assert "FARGONA KIMYO ZAVODI LLC" in country and "TASHKENT DUAL USE TRADING LLC" in country
    home = c.get("/sanctions").text
    assert "eksportyorlari uchun" in home and "Ikkilamchi sanksiya" in home
    assert c.get("/sanctions?c=UZB&kind=person&list=UK").status_code == 200


def test_sanctions_refresh_keeps_old_list_on_failure(client, tmp_path, monkeypatch):
    _, app_mod = client
    S = load_sample_lists(app_mod, tmp_path)

    async def broken(client_, url, dest):
        raise app_mod.collectors.CollectError("HTTP 503")

    monkeypatch.setattr(S, "_download", broken)
    import asyncio

    out = asyncio.run(S.refresh(["UN"]))
    assert "HTTP 503" in out["UN"]
    assert S.totals()["lists"]["UN"] == 2  # still there
    assert "HTTP 503" in S.status()["UN"]["error"]


WB_SAMPLE = {
    "pv": {"UZB": [-0.3, "2024"], "NOR": [1.2, "2024"], "SSD": [-2.4, "2024"]},
    "rl": {"UZB": [-1.0, "2024"], "NOR": [1.9, "2024"], "SSD": [-1.8, "2024"]},
    "cc": {"UZB": [-0.9, "2024"], "NOR": [2.1, "2024"]},
    "ext_debt": {"UZB": [60.0, "2024"], "NOR": [0, "2024"]},
    "inflation": {"UZB": [10.0, "2024"], "NOR": [3.0, "2024"], "SSD": [90.0, "2024"]},
    "growth": {"UZB": [6.0, "2024"], "NOR": [1.0, "2024"], "SSD": [-6.0, "2024"]},
}


def test_risk_scores_are_traceable(client):
    _, app_mod = client
    risk = app_mod.risk
    assert risk.lin(70, 20, 120) == 50 and risk.lin(500, 20, 120) == 100 and risk.lin(-5, 20, 120) == 0
    uz = risk.score_country("UZB", WB_SAMPLE, {"UZB": {"total": 99}}, {"UZB": [20, 5]})
    comps = {c["key"]: c for c in uz["components"]}
    assert comps["stability"]["value"] == risk.lin(0.3, -2, 2)  # −0.3 → 57.5
    assert comps["governance"]["value"] == round((risk.lin(1.0, -2, 2) + risk.lin(0.9, -2, 2)) / 2, 1)
    assert comps["debt"]["value"] == 40.0  # 60% of GNI on the 20…120 line
    assert comps["sanctions"]["value"] == 50.0  # 99 entries → 25·log10(100)
    assert comps["news"]["value"] == risk.lin(0.25, 0.05, 0.6)
    expected = sum(c["weight"] * c["value"] for c in uz["components"]) / 100
    assert uz["score"] == round(expected, 1) and uz["coverage"] == 6
    # too little data: no score rather than a misleading one
    assert risk.score_country("TWN", WB_SAMPLE, None, {})["score"] is None
    rows = risk.index(WB_SAMPLE, {"UZB": {"total": 99}}, {})
    scored = [r["iso"] for r in rows if r["score"] is not None]
    assert scored[0] == "SSD" and scored[-1] == "NOR" and len(rows) == 196


def test_risk_page(client, monkeypatch):
    c, app_mod = client

    async def wb(force=False):
        return WB_SAMPLE

    monkeypatch.setattr(app_mod.risk, "world_bank", wb)
    page = c.get("/risk").text
    assert "Xavf indeksi" in page and "riskmap" in page and "Siyosiy barqarorlik" in page
    d = c.get("/risk?c=UZB").text
    assert "Qarz tahlili" in d and "20% → 0, 120% → 100" in d
    assert c.get("/risk?tier=1").status_code == 200


def test_framing_camps(client):
    _, app_mod = client
    F = app_mod.framing
    assert F.camp_of("Reuters") == "west" and F.camp_of("ТАСС") == "ru" and F.camp_of("RT") == "ru"
    assert F.camp_of("Kun.uz | Расмий канал") == "uz" and F.camp_of("Radio Ozodlik") == "us_funded"
    assert F.camp_of("Meduza") == "ru_indep" and F.camp_of("Xinhua") == "cn" and F.camp_of("Times of India") == "in"
    assert F.camp_of("Ukrainska Pravda") == "west" and F.camp_of("Smalltown Gazette") is None
    assert F.camp_of("", "https://www.aljazeera.com/news/x") == "gulf"


def framing_items():
    now = datetime.now(UTC).isoformat()
    return [
        {"id": 1, "title": "Talks in Geneva stall as Moscow rejects ceasefire terms", "summary": "Kremlin aggression",
         "publisher": "Reuters", "url": "https://r.example/1", "published": now},
        {"id": 2, "title": "Переговоры в Женеве: Запад саботирует мир", "summary": "",
         "publisher": "ТАСС", "url": "https://t.example/2", "published": now},
        {"id": 3, "title": "Geneva talks end without deal", "summary": "", "publisher": "Kun.uz",
         "url": "https://k.example/3", "published": now},
    ]  # fmt: skip


def test_framing_clean_keeps_only_evidence(client):
    _, app_mod = client
    F = app_mod.framing
    items = F.pick(framing_items())
    raw = {
        "story": "Talks stalled.",
        "facts": [{"text": "Talks were held in Geneva", "cites": [1, 2]}, {"text": "one camp only", "cites": [1]}],
        "camps": [
            {"camp": "west", "angle": "Russia blocks peace", "words": ["aggression", "invented word"], "cites": [1, 2],
             "tone": "critical", "toward": "Russia"},
            {"camp": "ru", "angle": "West sabotages", "words": ["саботирует"], "tone": "angry"},
            {"camp": "cn", "angle": "not in the items"},
        ],
        "disputes": [{"text": "who blocked", "cites": [1, 2]}],
    }  # fmt: skip
    out = F.clean(raw, items)
    camps = {c["camp"]: c for c in out["camps"]}
    assert set(camps) == {"west", "ru"}  # no invented camp
    assert camps["west"]["words"] == ["aggression"]  # only words that really appear
    assert camps["west"]["cites"] == [n for n, a in enumerate(items, 1) if a["camp"] == "west"]
    assert camps["ru"]["tone"] == "neutral"  # unknown tone falls back
    assert [f["text"] for f in out["facts"]] == ["Talks were held in Geneva"]  # a shared fact needs two camps
    assert len(out["disputes"]) == 1


def test_framing_page_and_compare(client, monkeypatch):
    c, app_mod = client
    with app_mod.db.tx() as conn:
        for a in framing_items():
            conn.execute(
                "INSERT INTO articles (url, title, summary, publisher, published, fetched, kind, title_key) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    a["url"],
                    a["title"],
                    a["summary"],
                    a["publisher"],
                    a["published"],
                    a["published"],
                    "rss",
                    f"k{a['id']}",
                ),
            )
    ids = [r["id"] for r in app_mod.db.rows("SELECT id FROM articles WHERE url LIKE 'https://_.example/%'")]
    reply = json.dumps(
        {
            "story": "Geneva talks stalled.",
            "camps": [
                {"camp": "west", "angle": "Moscow blamed", "cites": [1]},
                {"camp": "ru", "angle": "West blamed", "cites": [2]},
            ],
        }
    )
    monkeypatch.setattr(app_mod.analyze, "ai_available", lambda: True)
    monkeypatch.setattr(app_mod.analyze, "complete", lambda *a, **k: (reply, "test-model"))
    r = c.post("/framing", data={"ids": ",".join(map(str, ids)), "lang": "uz"}, follow_redirects=False)
    assert r.status_code == 303 and "/framing?id=" in r.headers["location"]
    page = c.get(r.headers["location"]).text
    assert "Moscow blamed" in page and "Rossiya OAV" in page and "G&#39;arb OAV" in page

    # a topic nobody but one camp covers is refused before any AI call
    async def only_west(topic):
        return []

    monkeypatch.setattr(app_mod.framing, "search_topic", only_west)
    r = c.post("/framing", data={"topic": "zzzz nothing", "lang": "uz"}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_bot_risk_and_sanctions(bot, tmp_path, monkeypatch):
    c, app_mod, telegram_bot, sent = bot
    load_sample_lists(app_mod, tmp_path)

    async def wb(force=False):
        return WB_SAMPLE

    monkeypatch.setattr(app_mod.risk, "world_bank", wb)
    tg(c, telegram_bot, "/xavf O'zbekiston")
    tg(c, telegram_bot, "/sanksiya Muminov")
    out = texts(sent)
    assert "xavf indeksi" in out[-2] and "/risk?c=UZB" in out[-2]
    assert "MUMINOV" in out[-1] and "Rasmiy manbada tekshiring" in out[-1]


def test_cron_sanctions(bot, monkeypatch):
    c, app_mod, _, _ = bot

    async def fake(only=None):
        return {"OFAC": 3}

    monkeypatch.setattr(app_mod.sanctions, "refresh", fake)
    r = c.post("/cron/sanctions", headers={"x-cron-token": "cron-secret"})
    assert r.json() == {"OFAC": 3}


# ------------------------------------------------------------------ watch list, scheduler, forecasts, trade, pages


def test_watch_list_alerts_on_new_sanctions_match_and_risk_move(client, tmp_path, monkeypatch):
    c, app_mod = client
    S = load_sample_lists(app_mod, tmp_path)
    W = app_mod.watch
    assert "Qo'shildi" in unquote_plus(
        c.post("/watch/add", data={"kind": "company", "value": "Muminov"}, follow_redirects=False).headers["location"]
    )
    c.post("/watch/add", data={"kind": "country", "value": "UZB"})
    assert (
        "error="
        in c.post("/watch/add", data={"kind": "country", "value": "XXX"}, follow_redirects=False).headers["location"]
    )
    item = next(i for i in W.items(0) if i["kind"] == "company")
    assert len(item["state"]["known"]) == 2  # EU + UK already list him: known from the start, no alert
    assert W.check_sanctions() == []
    # a new listing of the same name appears in a later download
    rec = S._record("OFAC", "777", "person", "MUMINOV, Rustam", (), {"UZB"}, ["RUSSIA-EO14024"])
    with app_mod.db.tx() as conn:
        conn.execute(
            "INSERT INTO sanctions (list, uid, kind, name, aliases, countries, programs, listed, search) VALUES (?,?,?,?,?,?,?,?,?)",
            tuple(rec[k] for k in S.COLUMNS[1:]) if False else tuple(rec[k] for k in S.COLUMNS),
        )
    alerts = W.check_sanctions()
    assert len(alerts) == 1 and "OFAC" in alerts[0][1] and alerts[0][0] == 0
    assert W.check_sanctions() == []  # told once
    rows = [{"iso": "UZB", "score": 40.0, "band": "o'rta"}]
    assert W.check_risk(rows) == []  # first reading only sets the baseline
    assert W.check_risk([{"iso": "UZB", "score": 47.5, "band": "o'rta"}])[0][1].count("▲") == 1
    page = c.get("/watch").text
    assert "Muminov" in page and "MUMINOV" in page and "Kuzatuv" in page


def test_bot_links_account_and_shows_watch_list(bot, tmp_path, monkeypatch):
    c, app_mod, telegram_bot, sent = bot
    load_sample_lists(app_mod, tmp_path)
    u = app_mod.accounts.create("akhad", "a-long-password", "admin")
    code = app_mod.accounts.link_code(u["id"])
    tg(c, telegram_bot, "/bogla WRONG1")
    tg(c, telegram_bot, f"/bogla {code.lower()}")
    assert "noto'g'ri" in texts(sent)[-2] and "akhad" in texts(sent)[-1]
    assert app_mod.accounts.by_chat(42)["username"] == "akhad"
    app_mod.watch.add(u["id"], "company", "Muminov")
    tg(c, telegram_bot, "/mening")
    assert "Muminov" in texts(sent)[-1] and "moslik" in texts(sent)[-1]
    tg(c, telegram_bot, "/mening", chat=7)
    assert "bog'lanmagan" in texts(sent)[-1]
    # the account page's button opens the bot with /start bogla_CODE: one tap links it
    other = app_mod.accounts.create("ali", "another-long-pw")
    tg(c, telegram_bot, f"/start bogla_{app_mod.accounts.link_code(other['id'])}", chat=99)
    assert app_mod.accounts.by_chat(99)["username"] == "ali" and "Bog'landi" in texts(sent)[-1]


def test_scheduler_decides_what_is_due():
    import scheduler

    now = datetime(2026, 11, 26, 1, 30, tzinfo=UTC)  # 06:30 in Tashkent
    due = scheduler.due(now, {})
    assert "refresh" in due and "digest" not in due  # the digest waits for 07:00 Tashkent
    assert "digest" in scheduler.due(now + timedelta(hours=1), {})
    last = {"refresh": now - timedelta(minutes=10), "sanctions": now - timedelta(days=2)}
    later = scheduler.due(now, last)
    assert "refresh" not in later and "sanctions" not in later and "automap" in later


def test_due_jobs_run_once_and_are_remembered(client, monkeypatch):
    c, app_mod = client
    calls = []

    def fake(name):
        async def run():
            calls.append(name)
            return {"ok": name}

        return run

    monkeypatch.setattr(app_mod, "JOBS", {k: fake(k) for k in app_mod.JOBS})
    import asyncio

    first = asyncio.run(app_mod.run_due_jobs())
    assert "refresh" in first and first["sanctions"] == {"skipped": "kerak emas"}  # lists off in tests
    second = asyncio.run(app_mod.run_due_jobs())
    assert "refresh" not in second  # remembered in the database
    assert calls.count("refresh") == 1
    assert c.get("/healthz").json() == {"ok": True, "busy": False}


def test_auto_map_is_made_and_the_time_machine_shows_changes(client, monkeypatch):
    c, app_mod = client
    import asyncio

    monkeypatch.setattr(app_mod.analyze, "ai_available", lambda: True)
    monkeypatch.setattr(app_mod, "_articles", lambda h, *a: [{"id": i} for i in range(40)])
    first = {"summary": "s", "model": "m", "links": [{"from": "RUS", "to": "UKR", "type": "conflict", "title": "War"}]}
    second = {"summary": "s", "model": "m", "links": [{"from": "CHN", "to": "UZB", "type": "trade", "title": "Gas"}]}
    results = iter([first, second])
    monkeypatch.setattr(app_mod.analyze, "write_links", lambda arts, lang, focus: next(results))
    assert asyncio.run(app_mod.job_automap())["links"] == 1
    assert asyncio.run(app_mod.job_automap())["links"] == 1
    monkeypatch.undo()
    maps = c.get("/api/maps").json()
    assert len(maps) == 2 and all(m["auto"] == 1 for m in maps)
    page = c.get("/map").text
    assert "Vaqt mashinasi" in page and "1</b> ta yangi aloqa" in page and "yo'qoldi" in page
    older = c.get(f"/map?id={maps[0]['id']}").text
    assert "eng yangisi" in older


def test_forecast_ledger_checks_and_scores(client, monkeypatch):
    c, app_mod = client
    P = app_mod.predictions
    assert P.months_from("6 oy") == 6 and P.months_from("1-2 yil") == 18 and P.months_from("2 years") == 24
    assert P.months_from("", 9) == 9 and P.months_from("tez orada") == P.DEFAULT_MONTHS
    made = (datetime.now(UTC) - timedelta(days=400)).replace(microsecond=0).isoformat()
    data = {"scenarios": [
        {"title": "Gas deal signed", "probability": "high", "horizon": "1 yil", "trigger": "t", "outcome": "o"},
        {"title": "Border closed", "chance": 20, "months": 6, "trigger": "t", "outcome": "o"},
    ]}  # fmt: skip
    assert P.record("CHN-UZB-uz", "CHN", "UZB", data, made) == 2
    assert P.record("CHN-UZB-uz", "CHN", "UZB", data, made) == 0  # no duplicates
    due = P.due(10)
    assert len(due) == 2 and {d["chance"] for d in due} == {60, 20}
    news = [{"id": 1, "title": "China and Uzbekistan sign gas deal", "summary": "", "url": "https://x/1",
             "publisher": "Reuters", "published": collectors.now_iso()}]  # fmt: skip
    replies = iter([
        json.dumps({"outcome": "happened", "explanation": "Signed.", "evidence": [1]}),
        json.dumps({"outcome": "not_happened", "explanation": "Open.", "evidence": []}),  # no evidence → unclear
    ])  # fmt: skip
    monkeypatch.setattr(app_mod.analyze, "complete", lambda *a, **k: (next(replies), "m"))
    gas = next(d for d in due if d["title"] == "Gas deal signed")
    border = next(d for d in due if d["title"] == "Border closed")
    v1 = P.judge(gas, news)
    v2 = P.judge(border, news)
    assert v1["outcome"] == "happened" and v1["evidence"][0]["url"] == "https://x/1"
    assert v2["outcome"] == "unclear"
    P.save(gas["id"], v1)
    P.save(border["id"], v2)
    rows = {r["title"]: r for r in P.all_rows()}
    assert rows["Gas deal signed"]["status"] == "happened"
    assert rows["Border closed"]["status"] == "open" and rows["Border closed"]["due"] > collectors.now_iso()
    st = P.stats()
    assert st["scored"] == 1 and st["brier"] == round((gas["chance"] / 100 - 1) ** 2, 3) and st["hits"] == 100
    page = c.get("/predictions").text
    assert "Gas deal signed" in page and "amalga oshdi" in page and "Brier" in page


def test_pair_analysis_feeds_the_ledger(client, monkeypatch):
    c, app_mod = client
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    async def no_news(a, b):
        return []

    monkeypatch.setattr(app_mod.pairs, "pair_news", no_news)
    reply = {"score": 2, "label": "Partners", "essence": "Trade.", "summary": "S.", "asymmetry": "A.",
             "good": [{"area": "Trade", "text": "Gas", "evidence": []}], "bad": [], "areas": [], "third_parties": [],
             "scenarios": [{"title": "More gas", "probability": "high", "chance": 55, "months": 12, "horizon": "1 yil",
                            "trigger": "t", "outcome": "o"}], "watch": [], "data_note": "", "numbers_note": ""}  # fmt: skip
    monkeypatch.setattr(app_mod.analyze, "complete", lambda *a, **k: (json.dumps(reply), "m"))
    c.post("/pair", data={"a": "UZB", "b": "CHN", "lang": "uz"})
    rows = app_mod.predictions.all_rows()
    assert [(r["title"], r["chance"]) for r in rows] == [("More gas", 55)]


COMTRADE = {"data": [
    {"partnerCode": 0, "primaryValue": 1000.0}, {"partnerCode": 643, "primaryValue": 300.0},
    {"partnerCode": 156, "primaryValue": 500.0}, {"partnerCode": 899, "primaryValue": 150.0},
    {"partnerCode": 398, "primaryValue": 50.0},
]}  # fmt: skip


def test_trade_flows_parse_and_api(client, monkeypatch):
    c, app_mod = client
    T = app_mod.trade
    parsed = T.parse(COMTRADE["data"])
    assert parsed["world"] == 1000.0 and parsed["partners"][0] == ["CHN", 500] and parsed["other"] == 150
    calls = []

    async def fake_flow(client_, reporter, period, flow):
        calls.append((reporter, period, flow))
        return COMTRADE["data"] if period == datetime.now(UTC).year - 2 else []

    monkeypatch.setattr(T, "_flow", fake_flow)
    real_sleep = T.asyncio.sleep
    monkeypatch.setattr(T.asyncio, "sleep", lambda s: real_sleep(0))
    r = c.get("/api/trade/UZB").json()
    assert r["year"] == datetime.now(UTC).year - 2 and r["exports"]["partners"][1] == ["RUS", 300]
    n = len(calls)
    assert c.get("/api/trade/UZB").json()["year"] == r["year"] and len(calls) == n  # cached
    assert c.get("/api/trade/XXX").status_code == 404


def test_country_page(client, monkeypatch, tmp_path):
    c, app_mod = client
    load_sample_lists(app_mod, tmp_path)

    async def wb(force=False):
        return WB_SAMPLE

    async def no_debt(iso):
        return None

    async def facts_for(iso):
        return {"NY.GDP.MKTP.CD": {"value": 1.1e11, "year": "2025"}}

    async def flows(iso, force=False):
        return {
            "iso": iso,
            "year": 2025,
            "exports": app_mod.trade.parse(COMTRADE["data"]),
            "imports": app_mod.trade.parse([]),
        }

    monkeypatch.setattr(app_mod.risk, "world_bank", wb)
    monkeypatch.setattr(app_mod.debt, "country_debt", no_debt)
    monkeypatch.setattr(app_mod, "_country_facts", facts_for)
    monkeypatch.setattr(app_mod.trade, "flows", flows)
    page = c.get("/country/UZB").text
    assert "O'zbekiston" in page and "Asosiy raqamlar" in page and "Xitoy" in page and "Sanksiyalar" in page
    assert "Kuzatish" in page and "cglobe" in page
    assert "Davlatlar" in c.get("/country").text
    assert c.get("/country/XXX").status_code == 200  # the picker, not an error


def test_embedding_quota_backoff_and_model_discovery(client, monkeypatch):
    _, app_mod = client
    E = app_mod.embed
    now = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
    until = E.back_off(
        E.EmbedError(429, "RESOURCE_EXHAUSTED: Quota exceeded for metric EmbedContentRequestsPerDay"), now
    )
    assert until == datetime(2026, 11, 27, 8, 0, tzinfo=UTC)  # midnight Pacific
    assert E.remaining(now) == 0 and E.remaining(until + timedelta(minutes=1)) == E.DAILY
    assert E.back_off(E.EmbedError(429, "per minute"), now) == now + timedelta(minutes=2)
    assert E.back_off(E.EmbedError(429, "x quota=PerMinute retry=25s"), now) == now + timedelta(seconds=30)
    assert E.back_off(E.EmbedError(429, "You exceeded your current quota"), now) == now + timedelta(hours=1)
    assert E.back_off(E.EmbedError(500, "x"), now) is None
    E.spend(5, until + timedelta(minutes=1))
    assert E.remaining(until + timedelta(minutes=1)) == E.DAILY - 5

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._b, self.text = code, body, json.dumps(body)

        def json(self):
            return self._b

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, json=None, headers=None):
            if "gemini-embedding-001" in url:
                return Resp(404, {"error": {"status": "NOT_FOUND", "message": "gone"}})
            return Resp(200, {"embeddings": [{"values": [0.1] * E.DIM} for _ in json["requests"]]})

        def get(self, url, params=None, headers=None):
            return Resp(200, {"models": [{"name": "models/text-x", "supportedGenerationMethods": ["generateContent"]},
                                         {"name": "models/gemini-embedding-002", "supportedGenerationMethods": ["embedContent"]}]})  # fmt: skip

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(E.httpx, "Client", FakeClient)
    assert len(E.embed(["a", "b"])) == 2 and E.current_model() == "models/gemini-embedding-002"


def test_gzip_and_static_caching(client):
    c, _ = client
    r = c.get("/", headers={"accept-encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"
    assert c.get("/static/style.css?v=1").headers["cache-control"].startswith("public, max-age=")
    assert c.get("/static/style.css").headers["cache-control"] == "no-cache"


def test_backup_round_trip(client, tmp_path, monkeypatch):
    c, app_mod = client
    app_mod.watch.add(0, "country", "UZB")
    dump = c.get("/admin/export")
    assert dump.status_code == 200 and "attachment" in dump.headers["content-disposition"]
    data = dump.json()
    assert data["tables"]["watch_items"][0]["value"] == "UZB"
    with app_mod.db.tx() as conn:
        conn.execute("DELETE FROM watch_items")
    r = c.post(
        "/admin/import",
        files={"file": ("b.json", json.dumps(data).encode(), "application/json")},
        follow_redirects=False,
    )
    assert "Tiklandi" in unquote_plus(r.headers["location"])
    assert app_mod.watch.items(0)[0]["value"] == "UZB"


def test_new_pages_render(client):
    c, _ = client
    for path in ["/watch", "/predictions", "/predictions?show=open", "/account", "/admin", "/login", "/map?trade=UZB"]:
        r = c.get(path)
        assert r.status_code == 200, path
    assert "bottomnav" in c.get("/").text and "data-pdf" not in c.get("/").text


def test_logs_never_show_tokens(caplog):
    import logging

    import app

    f = app._Redact()
    rec = logging.LogRecord("httpx", logging.INFO, "", 0, 'HTTP Request: %s %s "%s"',
                            ("POST", "https://api.telegram.org/bot123456:AAH-secret_x/getMe", "HTTP/1.1 200"), None)  # fmt: skip
    f.filter(rec)
    assert "AAH-secret" not in rec.getMessage() and "/bot***/getMe" in rec.getMessage()
    rec2 = logging.LogRecord("x", logging.INFO, "", 0, "GET https://x.test/a?key=abc123&q=1", (), None)
    f.filter(rec2)
    assert rec2.getMessage() == "GET https://x.test/a?key=***&q=1"
