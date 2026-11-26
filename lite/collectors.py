"""Collectors: each returns a list of plain dicts (title, url, summary, publisher, published).

Only headline, a short snippet and the link are kept, never full article text.
Sites that refuse direct access are reached the same way a reader finds them: through
Google News and GDELT, which list the headline and link without opening the site.
"""

from __future__ import annotations

import asyncio
import html
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

UA = "WorldScopeLite/1.0 (+https://github.com/Axadxon01/worldscope)"
TIMEOUT = httpx.Timeout(20.0, connect=10.0)
SNIPPET_MAX = 400

# Google News editions. Google has no Uzbek edition; Uzbek-language queries still work
# in the Russian or English edition.
GNEWS_EDITIONS = {
    "en": ("en-US", "US", "US:en"),
    "ru": ("ru", "RU", "RU:ru"),
    "tr": ("tr", "TR", "TR:tr"),
    "de": ("de", "DE", "DE:de"),
    "fr": ("fr", "FR", "FR:fr"),
    "ar": ("ar", "AE", "AE:ar"),
    "zh": ("zh-CN", "CN", "CN:zh-Hans"),
}


class CollectError(Exception):
    pass


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def to_iso(value) -> str | None:
    """Turn the many date shapes feeds use into ISO 8601 UTC."""
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif hasattr(value, "tm_year"):  # time.struct_time from feedparser
            dt = datetime(*value[:6], tzinfo=UTC)
        elif re.fullmatch(r"\d{8}T\d{6}Z", str(value)):  # GDELT
            dt = datetime.strptime(str(value), "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        else:
            s = str(value).strip()
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).replace(microsecond=0).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_|ref$|ocid$)")


def clean_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ""))


def text_of(fragment: str | None, limit: int = SNIPPET_MAX) -> str:
    if not fragment:
        return ""
    txt = BeautifulSoup(fragment, "html.parser").get_text(" ", strip=True)
    txt = re.sub(r"\s+", " ", html.unescape(txt)).strip()
    return txt if len(txt) <= limit else txt[: limit - 1].rsplit(" ", 1)[0] + "…"


def item(title, url, summary="", publisher="", published=None, need_date=False) -> dict | None:
    title = text_of(title, 300)
    when = to_iso(published)
    if not title or not url or (need_date and not when):
        return None
    url = clean_url(str(url))
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None  # only web links: a "javascript:" link from a hostile feed would run in our page
    return {
        "title": title,
        "url": url,
        "summary": text_of(summary),
        "publisher": (publisher or "").strip()[:120],
        "published": when or now_iso(),
    }


async def _get(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    try:
        r = await client.get(url, follow_redirects=True, **kw)
    except httpx.HTTPError as e:
        raise CollectError(f"ulanib bo'lmadi: {type(e).__name__}") from e
    if r.status_code >= 400:
        raise CollectError(f"HTTP {r.status_code}")
    return r


# ---------------------------------------------------------------- parsers (pure)


def parse_feed(content: bytes, default_publisher: str = "") -> list[dict]:
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise CollectError("RSS/Atom sifatida o'qib bo'lmadi")
    publisher = default_publisher or feed.feed.get("title", "")

    def date_of(e):
        return e.get("published_parsed") or e.get("updated_parsed") or e.get("published") or e.get("updated")

    # An undated item in a dated feed is usually an old one; it would otherwise show as new.
    dated = sum(1 for e in feed.entries if to_iso(date_of(e)))
    need_date = dated >= len(feed.entries) / 2
    out = []
    for e in feed.entries:
        it = item(
            e.get("title"),
            e.get("link"),
            e.get("summary") or e.get("description"),
            publisher,
            date_of(e),
            need_date,
        )
        if it:
            out.append(it)
    return out


def parse_gnews(content: bytes) -> list[dict]:
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise CollectError("Google News javobini o'qib bo'lmadi")
    out = []
    for e in feed.entries:
        publisher = (e.get("source") or {}).get("title", "")
        title = e.get("title", "")
        # Google appends " - Publisher" to every headline.
        if publisher and title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3]
        summary = text_of(e.get("summary"))
        if summary.startswith(text_of(title, 300)):
            summary = ""  # Google's summary is usually just the headline again
        it = item(title, e.get("link"), summary, publisher, e.get("published_parsed"))
        if it:
            out.append(it)
    return out


def parse_gdelt(payload: dict) -> list[dict]:
    out = []
    for a in payload.get("articles") or []:
        it = item(a.get("title"), a.get("url"), "", a.get("domain", ""), a.get("seendate"))
        if it:
            it["lang"] = (a.get("language") or "")[:20]
            out.append(it)
    return out


def parse_telegram(page: str, channel: str) -> list[dict]:
    soup = BeautifulSoup(page, "html.parser")
    title_el = soup.select_one(".tgme_channel_info_header_title")
    publisher = f"Telegram: {title_el.get_text(strip=True) if title_el else '@' + channel}"
    out = []
    for msg in soup.select(".tgme_widget_message[data-post]"):
        text_el = msg.select_one(".tgme_widget_message_text")
        if not text_el:
            continue  # photo or video without a caption
        for br in text_el.find_all("br"):
            br.replace_with("\n")
        text = text_el.get_text().strip()
        if not text:
            continue
        first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        title = first if len(first) <= 160 else first[:159].rsplit(" ", 1)[0] + "…"
        rest = text[len(first) :].strip() if len(first) <= 160 else text
        when = msg.select_one(".tgme_widget_message_date time")
        it = item(
            title,
            f"https://t.me/{msg['data-post']}",
            rest,
            publisher,
            when.get("datetime") if when else None,
        )
        if it:
            out.append(it)
    return out


# ---------------------------------------------------------------- fetchers


async def fetch_rss(client, value: str, label: str, lang: str) -> list[dict]:
    r = await _get(client, value)
    if urlsplit(value).netloc.endswith("news.google.com"):
        return parse_gnews(r.content)  # keep each item's real publisher, not the feed's name
    return parse_feed(r.content, label)


async def fetch_gnews(client, value: str, label: str, lang: str) -> list[dict]:
    hl, gl, ceid = GNEWS_EDITIONS.get(lang, GNEWS_EDITIONS["en"])
    q = value if "when:" in value else f"{value} when:2d"
    url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl={hl}&gl={gl}&ceid={ceid}"
    r = await _get(client, url)
    return parse_gnews(r.content)


async def fetch_telegram(client, value: str, label: str, lang: str) -> list[dict]:
    channel = value.strip().lstrip("@").removeprefix("https://t.me/").removeprefix("s/").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_]{4,64}", channel):
        raise CollectError("kanal nomi noto'g'ri")
    r = await _get(client, f"https://t.me/s/{channel}")
    items = parse_telegram(r.text, channel)
    if not items and "tgme_widget_message" not in r.text:
        raise CollectError("kanal ochiq emas yoki veb-ko'rinishi o'chirilgan")
    return items


_gdelt_lock = asyncio.Lock()


async def fetch_gdelt(client, value: str, label: str, lang: str) -> list[dict]:
    params = {
        "query": value,
        "mode": "artlist",
        "format": "json",
        "maxrecords": "75",
        "timespan": "2d",
        "sort": "datedesc",
    }
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    async with _gdelt_lock:  # GDELT asks for about one request per 5 seconds
        for attempt in range(3):
            try:
                r = await _get(client, url, params=params)
                break
            except CollectError as e:
                if attempt == 2 or not ("429" in str(e) or "ulanib" in str(e)):
                    raise
                await asyncio.sleep(8 * (attempt + 1))
        await asyncio.sleep(5)
    try:
        payload = r.json()
    except ValueError:
        raise CollectError("GDELT: " + text_of(r.text, 160)) from None
    return parse_gdelt(payload)


FETCHERS = {"rss": fetch_rss, "gnews": fetch_gnews, "telegram": fetch_telegram, "gdelt": fetch_gdelt}


async def collect(sources: list[dict]) -> list[tuple[dict, list[dict] | None, str | None]]:
    """Fetch every source; return (source, items or None, error or None) per source."""
    sem = asyncio.Semaphore(6)

    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=TIMEOUT) as client:

        async def one(src):
            async with sem:
                try:
                    items = await FETCHERS[src["kind"]](client, src["value"], src["label"], src["lang"])
                    return src, items, None
                except CollectError as e:
                    return src, None, str(e)
                except Exception as e:
                    return src, None, f"kutilmagan xato: {type(e).__name__}"

        return await asyncio.gather(*(one(s) for s in sources))
