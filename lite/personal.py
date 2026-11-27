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
