"""Media framing: how different media camps tell the same story.

Publishers are sorted into camps (Russian media, Western agencies, Chinese state media, Uzbek media
and so on) by name. A story is either a group of stored headlines that several camps covered, or a
topic the user types, searched in Google News in English and Russian. The AI compares the camps'
headlines and lead paragraphs: what each puts first, what it leaves out, which loaded words it uses,
and which facts all of them agree on. Only headlines and snippets are read, never full articles,
and the page says so.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote_plus, urlparse

import httpx

import analyze
import collectors

# (key, label, publisher names and domains); checked in this order, the first match wins.
CAMPS: list[tuple[str, str, list[str]]] = [
    ("us_funded", "AQSH davlat radiolari (RFE/RL, VOA)", [
        "radio free europe", "rfe rl", "rferl", "radio liberty", "радио свобода", "радио азаттык", "svoboda",
        "ozodlik", "озодлик", "azattyq", "azattyk", "азаттык", "current time", "настоящее время",
        "voice of america", "voa", "голос америки", "amerika ovozi"]),
    ("ru_indep", "Rossiya mustaqil OAV (xorijda)", [
        "meduza", "медуза", "novaya gazeta", "новая газета", "the insider", "tv rain", "dozhd", "дождь",
        "mediazona", "медиазона", "the bell", "verstka", "вёрстка", "агентство", "istories", "важные истории"]),
    ("ru", "Rossiya OAV", [
        "rt", "rt com", "russia today", "tass", "тасс", "ria", "ria novosti", "риа", "риа новости", "sputnik",
        "спутник", "rossiyskaya gazeta", "российская газета", "rg ru", "izvestia", "известия", "interfax",
        "интерфакс", "kommersant", "коммерсант", "коммерсантъ", "lenta ru", "лента ру", "gazeta ru",
        "газета ru", "vesti", "вести", "rbc", "рбк", "первый канал", "ntv", "нтв", "kp ru", "комсомольская правда",
        "аиф", "argumenty i fakty", "regnum", "регнум", "ura ru", "mk ru", "московский комсомолец", "vedomosti",
        "ведомости", "ren tv", "рен тв", "life ru", "царьград", "tsargrad", "eadaily", "pravda ru"]),
    ("uz", "O'zbekiston OAV", [
        "kun uz", "kunuz", "gazeta uz", "daryo", "uza", "uza uz", "spot uz", "podrobno", "uzdaily", "uzreport",
        "xabar uz", "dunyo info", "nuz uz", "upl uz", "uzbekistan today", "review uz", "qalampir", "bugun uz",
        "president uz", "uz24", "zamin uz", "ўзбекистон", "o zbekiston 24"]),
    ("ca", "Markaziy Osiyo va Kavkaz OAV", [
        "kazinform", "tengrinews", "tengri", "24 kg", "akipress", "kaktus", "asia plus", "asiaplus", "trend",
        "apa az", "azertac", "orda kz", "vlast kz", "kursiv", "inbusiness kz", "khovar", "turkmenportal",
        "kapital kz", "zakon kz", "sputnik kazakhstan", "report az", "armenpress", "interpressnews"]),
    ("cn", "Xitoy davlat OAV", [
        "xinhua", "синьхуа", "cgtn", "global times", "china daily", "people s daily", "cctv", "china org cn",
        "ecns", "chinanews", "china news service", "жэньминь"]),
    ("tr", "Turkiya OAV", ["anadolu", "aa com tr", "trt", "daily sabah", "hurriyet", "hürriyet", "yeni safak",
                           "yeni şafak", "sabah", "milliyet", "sozcu", "sözcü"]),
    ("gulf", "Arab va Fors ko'rfazi OAV", ["al jazeera", "aljazeera", "al arabiya", "alarabiya", "gulf news",
                                         "the national news", "thenationalnews", "arab news", "khaleej",
                                         "asharq", "al monitor", "middle east eye", "gulf times"]),
    ("ir", "Eron davlat OAV", ["press tv", "presstv", "irna", "tasnim", "tehran times", "mehr news", "fars news",
                               "parstoday", "iran daily"]),
    ("in", "Hindiston OAV", ["times of india", "hindustan times", "ndtv", "the hindu", "india today",
                             "economic times", "indian express", "wion", "firstpost", "news18", "livemint"]),
    ("west", "G'arb OAV", [
        "reuters", "associated press", "ap news", "apnews", "ap", "bbc", "cnn", "new york times", "nytimes",
        "washington post", "the guardian", "guardian", "bloomberg", "financial times", "ft com", "ft",
        "wall street journal", "wsj", "dw", "deutsche welle", "france 24", "afp", "le monde", "politico",
        "the economist", "euronews", "nbc news", "cbs news", "abc news", "fox news", "the telegraph",
        "the times", "the independent", "axios", "newsweek", "npr", "sky news", "der spiegel", "spiegel",
        "eurasianet", "the diplomat", "rfi", "le figaro", "the hill", "usa today", "cnbc", "yahoo news",
        "foreign policy", "time", "los angeles times", "abc net au", "cbc", "globe and mail", "kyiv independent",
        "ukrainska pravda", "kyiv post", "ukrinform"]),
]  # fmt: skip
CAMP_LABELS = {k: label for k, label, _ in CAMPS}
_PATTERNS = [
    (k, re.compile(r"(?<![a-z0-9а-яё])(?:" + "|".join(re.escape(t) for t in terms) + r")(?![a-z0-9а-яё])"))
    for k, _, terms in CAMPS
]
PER_CAMP = 5
MAX_ITEMS = 36
MIN_CAMPS = 2


def _plain(text: str) -> str:
    t = (text or "").lower().replace("’", " ").replace("'", " ")
    return re.sub(r"[^\w]+", " ", t).strip()


def camp_of(publisher: str, url: str = "") -> str | None:
    """'Reuters' → west, 'Kun.uz | Rasmiy kanal' → uz, a Russian agency in Cyrillic → ru; None when unknown."""
    host = urlparse(url).hostname or ""
    if host.endswith("news.google.com"):
        host = ""
    for text in (publisher, host.removeprefix("www.")):
        t = _plain(text)
        if not t:
            continue
        for key, pat in _PATTERNS:
            if pat.search(t):
                return key
    return None


def tag(items: list[dict]) -> list[dict]:
    out = []
    for a in items:
        if k := camp_of(a.get("publisher", ""), a.get("url", "")):
            out.append({**a, "camp": k})
    return out


def pick(items: list[dict]) -> list[dict]:
    """Newest few per camp, one per headline; at most MAX_ITEMS overall."""
    by: dict[str, list[dict]] = {}
    seen = set()
    for a in sorted(tag(items), key=lambda x: x["published"], reverse=True):
        key = analyze.title_key(a["title"]) or a["url"]
        if key in seen or len(by.setdefault(a["camp"], [])) >= PER_CAMP:
            continue
        seen.add(key)
        by[a["camp"]].append(a)
    flat = [a for k in CAMP_LABELS for a in by.get(k, [])]
    return flat[:MAX_ITEMS]


def camps_in(items: list[dict]) -> list[str]:
    return [k for k in CAMP_LABELS if any(a.get("camp") == k for a in items)]


def stories(articles: list[dict], limit: int = 12) -> list[dict]:
    """Stored stories that at least two camps covered, most camps first."""
    out = []
    for g in analyze.cluster(articles):
        tagged = tag(g)
        ks = camps_in(tagged)
        if len(ks) >= MIN_CAMPS:
            out.append({"title": g[0]["title"], "ids": [a["id"] for a in g][:MAX_ITEMS], "camps": ks, "size": len(g)})
    out.sort(key=lambda s: (len(s["camps"]), s["size"]), reverse=True)
    return out[:limit]


async def search_topic(topic: str) -> list[dict]:
    """The topic in Google News, English and Russian editions, last two weeks."""
    found: dict[str, dict] = {}
    async with httpx.AsyncClient(headers={"User-Agent": collectors.UA}, timeout=collectors.TIMEOUT) as client:

        async def one(lang: str) -> list[dict]:
            hl, gl, ceid = collectors.GNEWS_EDITIONS[lang]
            q = quote_plus(f"{topic} when:14d")
            url = f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"
            try:
                r = await client.get(url, follow_redirects=True)
                return collectors.parse_gnews(r.content) if r.status_code < 400 else []
            except (httpx.HTTPError, collectors.CollectError):
                return []

        for items in await asyncio.gather(one("en"), one("ru")):
            for it in items:
                found.setdefault(it["url"], it)
    return list(found.values())


# ------------------------------------------------------------------ the AI

FRAMING_SYSTEM = """You are the media analyst of WorldScope. You receive numbered news items about one
story; each is labelled with the media camp it comes from (for example Russian media, Western media,
Chinese state media, Uzbek media). You only have headlines and short lead snippets, not full articles,
so judge only what each camp chose to put in its headline and lead.

Compare the camps. Return ONLY JSON:
{
  "story": "one neutral sentence: what happened",
  "facts": [{"text": "a fact that items from at least two camps state", "cites": [n, ...]}],
  "camps": [
    {"camp": "camp key exactly as given",
     "angle": "one sentence: the main frame this camp gives the story",
     "emphasis": ["what this camp puts first", "..."],
     "omits": ["something other camps report that this camp's items do not mention", "..."],
     "words": ["loaded or evaluative words copied exactly from this camp's headlines/snippets"],
     "tone": "critical | neutral | supportive",
     "toward": "who the tone is about (a country, leader or side)",
     "cites": [n, ...]}
  ],
  "disputes": [{"text": "a point where camps state contradicting facts", "cites": [n, ...]}],
  "contrast": "2-4 sentences: the biggest differences between the camps",
  "caution": "one sentence on the limits of this comparison"
}

Rules:
- Write every text field in {lang}. Keep "words" in the original language of the headline.
- One entry in "camps" per camp present in the items, using its key. Do not invent camps.
- Cite item numbers for everything; cite only items of that camp in its entry.
- "omits" lists only things other camps' items actually report. Never guess what is in a full article.
- Do not say which camp is right, except where items state facts that contradict each other: put those
  in "disputes" and do not resolve them.
- Describe framing in neutral, factual words. No insults, no labels such as "propaganda" or "fake".
"""


def numbered(items: list[dict]) -> list[str]:
    lines = []
    for n, a in enumerate(items, 1):
        snippet = f" — {a['summary'][:260]}" if a.get("summary") else ""
        lines.append(f"[{n}] camp={a['camp']} | {a['publisher']} | {a['published'][:10]} | {a['title']}{snippet}")
    return lines


def _cites(v, items: list[dict], camp: str | None = None) -> list[int]:
    out = []
    for n in v if isinstance(v, list) else []:
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(items) and n not in out and (camp is None or items[n - 1]["camp"] == camp):
            out.append(n)
    return out


def _strs(v, limit: int = 6, size: int = 220) -> list[str]:
    return [str(x).strip()[:size] for x in (v if isinstance(v, list) else []) if str(x).strip()][:limit]


def clean(raw: dict, items: list[dict]) -> dict:
    present = camps_in(items)
    camps, done = [], set()
    for c in raw.get("camps") or []:
        if not isinstance(c, dict):
            continue
        k = str(c.get("camp", "")).strip()
        if k not in present or k in done:
            continue
        done.add(k)
        text = " ".join((a["title"] + " " + a.get("summary", "")).lower() for a in items if a["camp"] == k)
        words = [w for w in _strs(c.get("words"), 5, 60) if w.lower() in text]  # only words really used
        tone = str(c.get("tone", "neutral")).lower()
        camps.append(
            {
                "camp": k,
                "label": CAMP_LABELS[k],
                "angle": str(c.get("angle", "")).strip()[:400],
                "emphasis": _strs(c.get("emphasis"), 4),
                "omits": _strs(c.get("omits"), 4),
                "words": words,
                "tone": tone if tone in ("critical", "neutral", "supportive") else "neutral",
                "toward": str(c.get("toward", "")).strip()[:80],
                "cites": _cites(c.get("cites"), items, k) or [n for n, a in enumerate(items, 1) if a["camp"] == k][:3],
            }
        )
    camps.sort(key=lambda c: present.index(c["camp"]))

    def cited_list(key: str, min_camps: int = 1) -> list[dict]:
        out = []
        for f in raw.get(key) or []:
            if not isinstance(f, dict) or not str(f.get("text", "")).strip():
                continue
            cites = _cites(f.get("cites"), items)
            if cites and len({items[n - 1]["camp"] for n in cites}) >= min_camps:
                out.append({"text": str(f["text"]).strip()[:400], "cites": cites})
        return out[:8]

    return {
        "story": str(raw.get("story", "")).strip()[:400],
        "facts": cited_list("facts", 2),  # a shared fact must really come from two camps
        "camps": camps,
        "disputes": cited_list("disputes", 2),
        "contrast": str(raw.get("contrast", "")).strip()[:1200],
        "caution": str(raw.get("caution", "")).strip()[:400],
    }


def compare(items: list[dict], lang: str, topic: str = "") -> dict:
    if len(camps_in(items)) < MIN_CAMPS:
        raise analyze.AIError("Bu voqeani kamida ikki xil OAV lageri yoritgani topilmadi")
    system = FRAMING_SYSTEM.replace("{lang}", analyze.LANG_NAMES.get(lang, "English"))
    legend = "\n".join(f"{k} = {CAMP_LABELS[k]}" for k in camps_in(items))
    prompt = f"Camps:\n{legend}\n\n{'Topic: ' + topic + chr(10) if topic else ''}Items:\n" + "\n".join(numbered(items))
    for attempt in range(2):
        text, model = analyze.complete(system, prompt + "\n\nReturn the JSON.", json_mode=True)
        try:
            raw = analyze._json_from(text)
            break
        except analyze.AIError:
            if attempt == 1:
                raise
    out = clean(raw, items)
    if not out["camps"]:
        raise analyze.AIError("AI lagerlarni taqqoslamadi, qayta urinib ko'ring")
    out["model"] = model
    out["items"] = [
        {"n": n, "camp": a["camp"], "publisher": a["publisher"], "title": a["title"], "url": a["url"],
         "published": a["published"]}
        for n, a in enumerate(items, 1)
    ]  # fmt: skip
    return out
