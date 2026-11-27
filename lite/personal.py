"""What makes the site the visitor's own: their interests, what is new since their last visit, a weekly quiz.

Interests and visit times live in cookies, so they work with or without an account; a signed-in
person's followed countries (the watch list) count as interests too.
"""

from __future__ import annotations

import random
import re
from datetime import UTC, datetime, timedelta

import analyze

PREFS_COOKIE, LAST_COOKIE, PREV_COOKIE = "ws_prefs", "ws_last", "ws_prev"
SESSION_GAP = timedelta(minutes=30)  # a pause longer than this starts a new visit
YEAR = 365 * 24 * 3600
# offered first when someone says what interests them: the region, then the big players
POPULAR = ["UZB", "KAZ", "KGZ", "TJK", "TKM", "AFG", "RUS", "CHN", "USA", "TUR", "IRN", "IND", "PAK",
           "UKR", "DEU", "GBR", "FRA", "JPN", "KOR", "SAU", "ARE", "ISR", "AZE"]  # fmt: skip


def parse_prefs(raw: str | None) -> tuple[list[str], list[str]]:
    """'UZB.RUS|energy.trade' → (["UZB", "RUS"], ["energy", "trade"]); anything unknown is dropped."""
    import explain  # the topics live with the plain words

    cs, _, ts = (raw or "").partition("|")
    countries = [c for c in dict.fromkeys(re.findall(r"[A-Z]{3}", cs.upper())) if c in analyze.COUNTRY_INFO][:12]
    topics = [t for t in dict.fromkeys(ts.split(".")) if t in explain.TOPIC][:8]
    return countries, topics


def dump_prefs(countries: list[str], topics: list[str]) -> str:
    c, t = parse_prefs(".".join(countries) + "|" + ".".join(topics))
    return ".".join(c) + "|" + ".".join(t)


def _when(raw: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(raw or "")
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def visit(last_raw: str | None, prev_raw: str | None, now: datetime | None = None) -> tuple[datetime | None, str]:
    """(start of the previous visit or None, the new value of the 'previous' cookie).

    The 'last' cookie is the time of the latest page view; when more than SESSION_GAP has passed
    since it, a new visit begins and that time becomes 'previous'. Within one visit 'previous' stays,
    so reloading a page does not hide what was new.
    """
    now = now or datetime.now(UTC)
    last, prev = _when(last_raw), _when(prev_raw)
    if last and now - last > SESSION_GAP:
        prev = last
    return prev, prev.isoformat() if prev else ""


def newest(group: list[dict]) -> str:
    return max(a["published"] for a in group)


def is_new(group: list[dict], since: datetime | None) -> bool:
    return bool(since) and newest(group) > since.isoformat()


def matches(group: list[dict], countries: set[str], topics: set[str]) -> bool:
    return any(countries & set(a["countries"]) or topics & set(a["categories"]) for a in group)


def ago_words(t: datetime, now: datetime | None = None) -> str:
    d = (now or datetime.now(UTC)) - t
    if d < timedelta(hours=1):
        return f"{max(1, int(d.total_seconds() // 60))} daqiqa oldin"
    if d < timedelta(days=1):
        return f"{int(d.total_seconds() // 3600)} soat oldin"
    return f"{d.days} kun oldin"


# ------------------------------------------------------------------ the weekly quiz

VERBS = {
    "conflict": "kim bilan urushmoqda yoki to'qnashmoqda",
    "trade": "kim bilan savdo qilmoqda",
    "diplomacy": "kim bilan muzokara olib bormoqda",
    "cooperation": "kim bilan hamkorlik qilmoqda",
    "aid": "kimga yordam bermoqda",
    "pressure": "kimga bosim o'tkazmoqda",
    "rivalry": "kim bilan raqobatlashmoqda",
    "dependence": "kimga qaram bo'lib qolmoqda",
}


def week_key(now: datetime | None = None) -> str:
    y, w, _ = (now or datetime.now(UTC)).isocalendar()
    return f"{y}-W{w:02d}"


def quiz(links: list[dict], seed: str, n: int = 5) -> list[dict]:
    """Questions from the week's AI-map links: 'Bu hafta Rossiya kim bilan urushmoqda?'.

    A wrong option is never another true partner of the same country, so every question has
    exactly one right answer.
    """
    rnd = random.Random(seed)
    partners: dict[str, set[str]] = {}
    for x in links:
        partners.setdefault(x["from"], set()).add(x["to"])
        partners.setdefault(x["to"], set()).add(x["from"])
    pool = list(dict.fromkeys([c for x in links for c in (x["from"], x["to"])] + POPULAR))
    seen, out = set(), []
    for x in links:
        a, b, kind = x.get("from"), x.get("to"), x.get("type")
        if kind not in VERBS or a not in analyze.COUNTRY_INFO or b not in analyze.COUNTRY_INFO or (a, kind) in seen:
            continue
        wrong = [c for c in pool if c != a and c not in partners.get(a, set()) and c in analyze.COUNTRY_INFO]
        if len(wrong) < 3:
            continue
        seen.add((a, kind))
        options = [b, *rnd.sample(wrong, 3)]
        rnd.shuffle(options)
        out.append(
            {
                "q": f"Bu hafta {analyze.display_name(a)} {VERBS[kind]}?",
                "options": [{"iso": c, "name": analyze.display_name(c)} for c in options],
                "answer": b,
                "explain": x.get("bubble") or x.get("title") or "",
            }
        )
        if len(out) >= n:
            break
    return out


# ------------------------------------------------------------------ how many people come, and come back

_SEEN: set[tuple[str, str]] = set()
BOTS = ("bot", "crawl", "spider", "slurp", "preview", "headless", "python-", "curl", "wget")


def visitor_hash(vid: str) -> str:
    """The cookie id is never stored: only a salted hash of it."""
    import hashlib

    return hashlib.sha256(("worldscope-visit:" + vid).encode()).hexdigest()[:20]


def count_visit(vid: str, user_agent: str, now: datetime | None = None) -> None:
    import db

    if not vid or any(b in (user_agent or "").lower() for b in BOTS):
        return
    day = (now or datetime.now(UTC)).date().isoformat()
    h = visitor_hash(vid)
    if (day, h) in _SEEN:
        return
    if len(_SEEN) > 50_000:
        _SEEN.clear()
    _SEEN.add((day, h))
    first = db.one("SELECT MIN(first_day) AS d FROM visits WHERE visitor = ?", (h,))
    with db.tx() as c:
        c.execute(
            "INSERT INTO visits (day, visitor, first_day) VALUES (?,?,?) ON CONFLICT DO NOTHING",
            (day, h, (first or {}).get("d") or day),
        )


def visit_stats(days: int = 14, now: datetime | None = None) -> dict:
    """Per day: visitors, of them returning (seen on an earlier day); and the week's return rate."""
    import db

    today = (now or datetime.now(UTC)).date()
    since = (today - timedelta(days=days - 1)).isoformat()
    rows = db.rows(
        "SELECT day, COUNT(*) AS n, SUM(CASE WHEN first_day < day THEN 1 ELSE 0 END) AS back "
        "FROM visits WHERE day >= ? GROUP BY day ORDER BY day",
        (since,),
    )
    week = (today - timedelta(days=6)).isoformat()
    w = db.one(
        "SELECT COUNT(DISTINCT visitor) AS n, COUNT(DISTINCT CASE WHEN first_day < day THEN visitor END) AS back "
        "FROM visits WHERE day >= ?",
        (week,),
    )
    total = db.one("SELECT COUNT(DISTINCT visitor) AS n FROM visits")["n"]
    n, back = (w or {}).get("n") or 0, (w or {}).get("back") or 0
    return {"days": rows, "week": n, "week_back": back, "rate": round(100 * back / n) if n else None, "all": total}


# ------------------------------------------------------------------ the morning message, for one person


def picks(groups: list[list[dict]], countries: list[str], topics: list[str], n: int = 5) -> list[list[dict]]:
    """The person's stories first (most-covered first), then the day's biggest, n in all."""
    cs, ts = set(countries), set(topics)
    mine = [g for g in groups if (cs or ts) and matches(g, cs, ts)]
    rest = [g for g in groups if g not in mine and len({a["publisher"] for a in g}) >= 2]
    return (mine + rest)[:n]


def headline(group: list[dict], lines: dict, key) -> str:
    line = lines.get(key(group))
    return (line or {}).get("title") or group[0]["title"]


def digest_text(groups: list[list[dict]], countries: list[str], topics: list[str], lines: dict, key, base: str) -> str:
    """Telegram HTML: the person's stories with links to their WorldScope pages."""
    import html

    chosen = picks(groups, countries, topics)
    if not chosen or not (countries or topics):
        return ""
    names = ", ".join(analyze.display_name(c) for c in countries[:6])
    out = [f"📌 <b>Siz uchun bugun</b>{f' ({html.escape(names)})' if names else ''}", ""]
    for g in chosen:
        pubs = len({a["publisher"] for a in g})
        line = lines.get(key(g)) or {}
        out.append(
            f'• <a href="{base}/story/{g[0]["id"]}">{html.escape(headline(g, lines, key))}</a> <i>({pubs} manba)</i>'
        )
        if line.get("why"):
            out.append(f"  🇺🇿 {html.escape(line['why'])}")
    out.append(f"\nQiziqishlarni o'zgartirish: {base}/#prefs")
    return "\n".join(out)
