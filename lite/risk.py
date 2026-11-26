"""Country risk index: one transparent 0-100 score per sovereign state (higher = riskier).

Every input is a published figure: World Bank indicators (debt, inflation, growth, current account),
the Worldwide Governance Indicators, the number of entries on official sanctions lists tied to the
country, and the share of this site's recent news about the country that is about conflict. Each
figure is turned into 0-100 by a fixed straight-line rule, shown on the page next to the figure, and
the components are averaged with fixed weights over whatever the country has data for.

It is a screening tool for comparing countries, not a credit rating or a forecast.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx

import collectors
import db
import pairs
import sanctions

WB = "https://api.worldbank.org/v2/country/all/indicator/{code}?format=json&mrnev=1&per_page=400&source={source}"
CACHE_KEY = "risk:wb"
CACHE_DAYS = 7
MAX_AGE_YEARS = 8

# key: (indicator code, World Bank source, label, unit)
INDICATORS = {
    "ext_debt": ("DT.DOD.DECT.GN.ZS", 2, "Tashqi qarz, YaMDning %", "%"),
    "gov_debt": ("GC.DOD.TOTL.GD.ZS", 2, "Hukumat qarzi, YaIMning %", "%"),
    "inflation": ("FP.CPI.TOTL.ZG", 2, "Inflyatsiya", "%"),
    "growth": ("NY.GDP.MKTP.KD.ZG", 2, "Iqtisodiy o'sish", "%"),
    "current": ("BN.CAB.XOKA.GD.ZS", 2, "Joriy hisob balansi, YaIMning %", "%"),
    "pv": ("GOV_WGI_PV.EST", 3, "Siyosiy barqarorlik va zo'ravonlik yo'qligi", "−2.5…2.5"),
    "rl": ("GOV_WGI_RL.EST", 3, "Qonun ustuvorligi", "−2.5…2.5"),
    "cc": ("GOV_WGI_CC.EST", 3, "Korrupsiyani nazorat qilish", "−2.5…2.5"),
    "ge": ("GOV_WGI_GE.EST", 3, "Hukumat samaradorligi", "−2.5…2.5"),
    "rq": ("GOV_WGI_RQ.EST", 3, "Tartibga solish sifati", "−2.5…2.5"),
}


def lin(x: float, safe: float, risky: float) -> float:
    """Straight line: `safe` → 0, `risky` → 100, clipped to 0..100."""
    return round(max(0.0, min(100.0, (x - safe) / (risky - safe) * 100)), 1)


# How each figure becomes 0..100, with the rule in words for the page.
RULES = {
    "ext_debt": (lambda x: lin(x, 20, 120), "20% → 0, 120% → 100"),
    "gov_debt": (lambda x: lin(x, 30, 130), "30% → 0, 130% → 100"),
    "inflation": (lambda x: lin(abs(x - 3), 0, 37), "3% → 0, 40% (yoki −34%) → 100"),
    "growth": (lambda x: lin(-x, -6, 6), "+6% → 0, −6% → 100"),
    "current": (lambda x: lin(-x, -2, 15), "+2% profitsit → 0, −15% defitsit → 100"),
    "pv": (lambda x: lin(-x, -2, 2), "+2 → 0, −2 → 100"),
    "rl": (lambda x: lin(-x, -2, 2), "+2 → 0, −2 → 100"),
    "cc": (lambda x: lin(-x, -2, 2), "+2 → 0, −2 → 100"),
    "ge": (lambda x: lin(-x, -2, 2), "+2 → 0, −2 → 100"),
    "rq": (lambda x: lin(-x, -2, 2), "+2 → 0, −2 → 100"),
}

# (key, label, weight, inputs)
COMPONENTS = [
    ("stability", "Siyosiy barqarorlik", 25, ["pv"]),
    ("governance", "Boshqaruv sifati", 20, ["rl", "cc", "ge", "rq"]),
    ("debt", "Qarz yuki", 15, ["ext_debt", "gov_debt"]),
    ("economy", "Iqtisodiy holat", 15, ["inflation", "growth", "current"]),
    ("sanctions", "Sanksiyalar", 15, ["sanctions"]),
    ("news", "Yangiliklarda mojaro", 10, ["conflict_share"]),
]
MIN_COMPONENTS = 3
NEWS_MIN_ARTICLES = 8
NEWS_HOURS = 168
BANDS = [(20, "past", "#16a34a"), (40, "o'rtachadan past", "#84cc16"), (60, "o'rta", "#eab308"),
         (80, "yuqori", "#f97316"), (101, "juda yuqori", "#dc2626")]  # fmt: skip


def band(score: float | None) -> tuple[str, str]:
    if score is None:
        return "ma'lumot yetarli emas", "#94a3b8"
    for top, label, color in BANDS:
        if score < top:
            return label, color
    return BANDS[-1][1], BANDS[-1][2]


# ------------------------------------------------------------------ inputs


async def _indicator(client: httpx.AsyncClient, code: str, source: int) -> dict[str, list]:
    r = await client.get(WB.format(code=code, source=source))
    r.raise_for_status()
    data = r.json()
    out = {}
    for row in data[1] if isinstance(data, list) and len(data) > 1 and data[1] else []:
        iso, v = row.get("countryiso3code"), row.get("value")
        year = str(row.get("date", ""))[:4]
        if year.isdigit() and int(year) < datetime.now(UTC).year - MAX_AGE_YEARS:
            continue  # a figure this old says little about the country today
        if iso and v is not None and pairs.valid(iso):
            out[iso] = [round(float(v), 2), year]
    return out


async def world_bank(force: bool = False) -> dict[str, dict]:
    """{indicator key: {ISO3: [value, year]}} for every country, one request per indicator, cached."""
    if not force and (hit := await asyncio.to_thread(db.cache_get, CACHE_KEY, CACHE_DAYS)):
        return hit
    async with httpx.AsyncClient(headers={"User-Agent": collectors.UA}, timeout=25) as client:
        got = await asyncio.gather(
            *(_indicator(client, code, src) for code, src, *_ in INDICATORS.values()), return_exceptions=True
        )
    out = {k: v for k, v in zip(INDICATORS, got, strict=True) if isinstance(v, dict) and v}
    if len(out) >= len(INDICATORS) - 2:  # do not cache a mostly failed fetch
        await asyncio.to_thread(db.cache_put, CACHE_KEY, out)
    elif not out:
        raise collectors.CollectError("Jahon banki javob bermadi")
    return out


def news_stats(hours: int = NEWS_HOURS) -> dict[str, list[int]]:
    """{ISO3: [articles naming the country, of which tagged conflict]} over the last week."""
    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    out: dict[str, list[int]] = {}
    for r in db.rows("SELECT countries, categories FROM articles WHERE published >= ?", (since,)):
        conflict = '"conflict"' in r["categories"]
        for iso in db.loads(r["countries"]):
            s = out.setdefault(iso, [0, 0])
            s[0] += 1
            s[1] += conflict
    return out


# ------------------------------------------------------------------ the score


def score_country(iso: str, wb: dict, sanc: dict | None, news: dict) -> dict:
    parts: dict[str, dict] = {}
    for key, (code, _src, label, unit) in INDICATORS.items():
        hit = wb.get(key, {}).get(iso)
        if hit:
            parts[key] = {"label": label, "raw": hit[0], "year": hit[1], "unit": unit,
                          "risk": RULES[key][0](hit[0]), "rule": RULES[key][1], "code": code}  # fmt: skip
    if sanc is not None:  # None: the lists are not loaded, so the component is left out for everyone
        n = sanc.get(iso, {}).get("total", 0)
        parts["sanctions"] = {
            "label": "Sanksiya ro'yxatlarida shu davlatga bog'langan yozuvlar (OFAC, BMT, YeI, Buyuk Britaniya)",
            "raw": n, "year": "", "unit": "ta", "risk": sanctions.risk_points(n),
            "rule": "10 → 26, 100 → 50, 1000 → 75, 10 000 → 100 (logarifmik)", "code": "sanctions",
        }  # fmt: skip
    seen, conflict = news.get(iso, [0, 0])
    if seen >= NEWS_MIN_ARTICLES:
        share = conflict / seen
        parts["conflict_share"] = {"label": f"So'nggi 7 kun yangiliklarida mojaro ulushi ({conflict}/{seen})",
                                   "raw": round(share * 100), "year": "", "unit": "%",
                                   "risk": lin(share, 0.05, 0.6), "rule": "5% → 0, 60% → 100",
                                   "code": "news"}  # fmt: skip
    comps, total, weights = [], 0.0, 0
    for key, label, weight, inputs in COMPONENTS:
        have = [parts[i] for i in inputs if i in parts]
        value = round(sum(p["risk"] for p in have) / len(have), 1) if have else None
        comps.append({"key": key, "label": label, "weight": weight, "value": value, "parts": have})
        if value is not None:
            total += weight * value
            weights += weight
    present = sum(c["value"] is not None for c in comps)
    has_core = any(c["value"] is not None for c in comps if c["key"] in ("stability", "governance"))
    score = round(total / weights, 1) if weights and present >= MIN_COMPONENTS and has_core else None
    label, color = band(score)
    return {"iso": iso, "score": score, "band": label, "color": color, "components": comps,
            "coverage": present, "of": len(COMPONENTS)}  # fmt: skip


def index(wb: dict, sanc: dict | None, news: dict) -> list[dict]:
    """Every sovereign state, riskiest first; states without enough data at the end."""
    rows = [score_country(iso, wb, sanc, news) for iso in sorted(pairs.SOVEREIGN)]
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0), r["iso"]))
    rank = 0
    for r in rows:
        if r["score"] is not None:
            rank += 1
            r["rank"] = rank
    return rows


async def build(force: bool = False) -> tuple[list[dict], dict]:
    wb = await world_bank(force)
    sanc = None
    if await asyncio.to_thread(sanctions.loaded):
        sanc = await asyncio.to_thread(sanctions.stats)
    news = await asyncio.to_thread(news_stats)
    meta = {"sanctions": sanc is not None, "years": _years(wb)}
    return index(wb, sanc, news), meta


def _years(wb: dict) -> str:
    ys = sorted({v[1] for series in wb.values() for v in series.values() if v[1]})
    return f"{ys[0]}–{ys[-1]}" if ys else ""


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)
