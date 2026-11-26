"""Offline tests: parsers on realistic samples, tagging, grouping, and every page."""

import importlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze
import collectors

NOW = datetime.now(UTC)


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
    monkeypatch.setenv("WS_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    for m in ("db", "app"):
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

    from fastapi.testclient import TestClient

    with TestClient(app_mod.app) as c:
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


def test_password_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("WS_DB_PATH", str(tmp_path / "p.db"))
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    sys.modules.pop("app", None)
    app_mod = importlib.import_module("app")
    monkeypatch.setattr(app_mod, "refresh", lambda: __import__("asyncio").sleep(0))
    from fastapi.testclient import TestClient

    with TestClient(app_mod.app) as c:
        assert c.get("/").status_code == 401
        assert c.get("/healthz").status_code == 200
        assert c.get("/", auth=("any", "s3cret")).status_code == 200


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
