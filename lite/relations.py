"""One country's ties with the rest of the world, for the globe.

Three sources, best first, one link per partner:
  1. the AI maps of the last weeks (typed, with an explanation),
  2. news that names both countries (the topic of those stories gives the kind),
  3. the biggest trade partners, when UN Comtrade data is already cached.
No AI call here, so it works for every country, any time.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta

import analyze
import db

MAP_DAYS = 30
LIMIT = 16
# shared news says two countries are in the same stories, not what is between them: the kind stays
# "news" and the stories' main topic is told in words, never guessed into "war" or "trade"
NEWS = "news"


def _usd(v: float) -> str:
    if v >= 1e9:
        return f"{v / 1e9:.1f} mlrd $"
    if v >= 1e6:
        return f"{round(v / 1e6)} mln $"
    return f"{round(v / 1e3)} ming $"


def from_maps(iso: str, days: int = MAP_DAYS) -> list[dict]:
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    rows = db.rows(
        "SELECT data, created FROM maps WHERE created >= ? ORDER BY created DESC, id DESC LIMIT 60", (since,)
    )
    out, seen = [], set()
    for r in rows:
        for x in json.loads(r["data"]).get("links", []):
            if iso not in (x.get("from"), x.get("to")) or x.get("from") == x.get("to"):
                continue
            other = x["to"] if x["from"] == iso else x["from"]
            if other in seen or other not in analyze.COUNTRY_INFO:
                continue
            seen.add(other)
            out.append({**x, "src": "ai", "date": r["created"][:10]})
    return out


def from_news(iso: str, articles: list[dict]) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for a in articles:
        cs = a["countries"] if isinstance(a["countries"], list) else json.loads(a["countries"] or "[]")
        if iso not in cs:
            continue
        for c in cs[:5]:
            if c != iso and c in analyze.COUNTRY_INFO:
                by.setdefault(c, []).append(a)
    ranked = sorted(by.items(), key=lambda kv: (len({a["publisher"] for a in kv[1]}), len(kv[1])), reverse=True)
    out = []
    for other, arts in ranked:
        if len(arts) < 2:
            break
        cats = Counter(
            c
            for a in arts
            for c in (a["categories"] if isinstance(a["categories"], list) else json.loads(a["categories"] or "[]"))
            if c in analyze.CATEGORIES
        )
        topic = analyze.CATEGORIES[cats.most_common(1)[0][0]][0].lower() if cats else ""
        latest = max(arts, key=lambda a: a["published"])
        n = len(arts)
        out.append(
            {
                "from": iso, "to": other, "type": NEWS, "src": "news", "count": n, "topic": topic,
                "confidence": "medium" if n >= 4 else "low",
                "title": latest["title"],
                "bubble": f"{n} ta xabarda birga yozildi" + (f", ko'proq «{topic}» mavzusida" if topic else "")
                + f". Oxirgisi: {latest['title']}",
                "sources": [{"title": latest["title"], "url": latest["url"], "publisher": latest["publisher"]}],
                "date": latest["published"][:10],
            }
        )  # fmt: skip
    return out


def from_trade(iso: str, flows: dict | None, top: int = 6) -> list[dict]:
    if not flows or not flows.get("year"):
        return []
    out = []
    for side, verb in (("exports", "sotadi"), ("imports", "sotib oladi")):
        for p, v in ((flows.get(side) or {}).get("partners") or [])[:top]:
            if p == iso or p not in analyze.COUNTRY_INFO:
                continue
            a, b = (iso, p) if side == "exports" else (p, iso)
            out.append(
                {
                    "from": a, "to": b, "type": "trade", "src": "trade", "confidence": "high", "value": v,
                    "title": f"Savdo hamkori ({flows['year']})",
                    "bubble": f"{analyze.display_name(iso)} {analyze.display_name(p)}ga "
                    if side == "exports" else f"{analyze.display_name(iso)} {analyze.display_name(p)}dan ",
                    "date": str(flows["year"]),
                }
            )  # fmt: skip
            out[-1]["bubble"] += f"yiliga {_usd(v)}lik tovar {verb}."
    out.sort(key=lambda x: -x["value"])
    return out


def build(iso: str, articles: list[dict], flows: dict | None = None, limit: int = LIMIT) -> list[dict]:
    """Up to `limit` ties, one per partner: AI maps first, then news, then trade."""
    links, partners = [], set()
    for group in (from_maps(iso), from_news(iso, articles), from_trade(iso, flows)):
        for x in group:
            other = x["to"] if x["from"] == iso else x["from"]
            if other in partners:
                continue
            partners.add(other)
            links.append(x)
    return links[:limit]


def summary(iso: str, links: list[dict]) -> str:
    """'Rossiya hozir 9 ta davlat bilan bog'liq: 2 tasi bilan urush, 4 tasi bilan savdo…'"""
    name = analyze.display_name(iso)
    if not links:
        return f"{name} haqida so'nggi kunlarda boshqa davlatlar bilan birga yozilgan xabar topilmadi."
    words = {
        "conflict": "urush yoki to'qnashuv", "trade": "savdo", "diplomacy": "muzokara", "cooperation": "hamkorlik",
        "aid": "yordam", "pressure": "bosim", "rivalry": "raqobat", "dependence": "qaramlik",
    }  # fmt: skip
    kinds = Counter(x["type"] for x in links if x["type"] != NEWS)
    parts = [f"{n} tasi bilan {words.get(k, k)}" for k, n in kinds.most_common()]
    if n_news := sum(x["type"] == NEWS for x in links):
        parts.append(f"{n_news} tasi bilan yangiliklarda birga yoziladi")
    return f"{name} hozir {len(links)} ta davlat bilan bog'liq: " + ", ".join(parts) + "."
