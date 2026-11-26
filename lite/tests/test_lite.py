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
