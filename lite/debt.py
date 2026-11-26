"""A country's debts and what happened at home after it borrowed.

Figures, all from the World Bank's free APIs (no key), cached for a month:
- external debt over time (total, public, % of GNI, yearly debt service);
- who the public debt is owed to, by creditor country and institution (International Debt
  Statistics, which covers low- and middle-income borrowers; rich countries are not in it);
- the World Bank's own loans to the country (projects API: what each was for, how much, status);
- indicators of what changed at home: spending on education and health, investment, foreign
  investment, poverty, life expectancy, and control of corruption (Worldwide Governance Indicators).

News about loans, procurement, corruption cases and projects comes from Google News. The AI then
reads figures and news together and reports, area by area, what changed after the borrowing, keeping
reported facts, allegations and its own reading apart.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote_plus

import httpx

import analyze
import collectors
import db
import facts
import pairs

log = logging.getLogger("worldscope.debt")
HERE = Path(__file__).parent
CACHE_DAYS = 30
WB = "https://api.worldbank.org/v2"
_REF = json.loads((HERE / "ids_creditors.json").read_text())
CREDITOR_CODES: dict[str, str] = _REF["creditor_codes"]  # ISO3 → World Bank IDS counterpart code
ISO2: dict[str, str] = _REF["iso2"]  # ISO3 → ISO2, which the projects API wants

# (code, World Bank source, label, unit) — the debt itself
DEBT_SERIES = [
    ("DT.DOD.DECT.CD", 2, "Tashqi qarz, jami", "usd"),
    ("DT.DOD.DPPG.CD", 2, "Davlat va davlat kafolatlagan qarz", "usd"),
    ("DT.DOD.DECT.GN.ZS", 2, "Tashqi qarz / YaMD", "pct"),
    ("DT.TDS.DECT.CD", 2, "Yillik qarz to'lovi", "usd"),
    ("DT.DOD.DIMF.CD", 2, "XVF kreditlari", "usd"),
]

# Uzbek names for the lenders that are not countries (matched on the World Bank's English name)
INSTITUTIONS = [
    ("Bondholders", "Obligatsiya egalari (yevroobligatsiyalar)"),
    ("Asian Dev. Bank", "Osiyo taraqqiyot banki (ADB)"),
    ("Asian Infrastructure", "Osiyo infratuzilma investitsiya banki (AIIB)"),
    ("World Bank-IDA", "Jahon banki — IDA"),
    ("World Bank-IBRD", "Jahon banki — IBRD"),
    ("Islamic Dev. Bank", "Islom taraqqiyot banki (IsDB)"),
    ("European Bank for Reconstruction", "Yevropa tiklanish va taraqqiyot banki (EBRD)"),
    ("European Investment Bank", "Yevropa investitsiya banki (EIB)"),
    ("OPEC Fund", "OPEK xalqaro taraqqiyot fondi"),
    ("African Dev. Bank", "Afrika taraqqiyot banki (AfDB)"),
    ("Inter-American Dev. Bank", "Amerikalararo taraqqiyot banki (IDB)"),
    ("International Fund for Agricultural", "Xalqaro qishloq xo'jaligini rivojlantirish jamg'armasi (IFAD)"),
    ("Eurasian Development Bank", "Yevroosiyo taraqqiyot banki (EADB)"),
    ("New Development Bank", "Yangi taraqqiyot banki (BRIKS)"),
    ("Arab Fund", "Arab iqtisodiy va ijtimoiy taraqqiyot jamg'armasi"),
    ("Kuwait Fund", "Kuvayt jamg'armasi"),
    ("Saudi Fund", "Saudiya taraqqiyot jamg'armasi"),
]


def lender_name(row: dict) -> str:
    if row.get("iso"):
        return analyze.display_name(row["iso"])
    for prefix, uz in INSTITUTIONS:
        if row["name"].startswith(prefix):
            return uz
    return row["name"]


# what changed at home
OUTCOME_SERIES = [
    ("NE.GDI.FTOT.ZS", 2, "investment", "Asosiy kapitalga investitsiya / YaIM", "pct", "Qurilish va investitsiya"),
    (
        "BX.KLT.DINV.WD.GD.ZS",
        2,
        "investment",
        "Xorijiy to'g'ridan-to'g'ri investitsiya / YaIM",
        "pct",
        "Qurilish va investitsiya",
    ),
    ("SE.XPD.TOTL.GD.ZS", 2, "education", "Ta'lim xarajati / YaIM", "pct", "Ta'lim"),
    ("SH.XPD.CHEX.GD.ZS", 2, "health", "Sog'liqni saqlash xarajati / YaIM", "pct", "Tibbiyot"),
    ("SP.DYN.LE00.IN", 2, "health", "Umr davomiyligi (yil)", "num", "Tibbiyot"),
    ("SI.POV.LMIC", 2, "social", "Qashshoqlik ($3.65/kun), aholi ulushi", "pct", "Ijtimoiy yordam"),
    ("per_allsp.cov_pop_tot", 2, "social", "Ijtimoiy himoya qamrovi", "pct", "Ijtimoiy yordam"),
    ("GOV_WGI_CC.EST", 3, "corruption", "Korrupsiya nazorati (−2.5…+2.5)", "num", "Korrupsiya va jinoyat"),
    ("GOV_WGI_GE.EST", 3, "procurement", "Hukumat samaradorligi (−2.5…+2.5)", "num", "Davlat xaridlari"),
]

# The seven areas the user asked to check, in this order.
AREAS = [
    ("procurement", "Davlat xaridlari"),
    ("corruption", "Korrupsiya va jinoyat"),
    ("construction", "Qurilish"),
    ("investment", "Investitsiya"),
    ("social", "Ijtimoiy yordam"),
    ("education", "Ta'lim"),
    ("health", "Tibbiyot"),
]


def _num(v) -> str:
    return "—" if v is None else f"{v:.2f}".rstrip("0").rstrip(".")


def fmt(value, unit: str) -> str:
    return _num(value) if unit == "num" else facts.human(value, unit)


# ------------------------------------------------------------------ fetching


async def _series(client: httpx.AsyncClient, iso: str, code: str, source: int, start: int = 2008) -> list[list]:
    """[[year, value], ...] oldest first; empty if the series has nothing for this country."""
    try:
        r = await client.get(
            f"{WB}/country/{iso}/indicator/{code}",
            params={"format": "json", "date": f"{start}:{datetime.now(UTC).year}", "per_page": 100, "source": source},
        )
        rows = r.json()[1] if r.status_code == 200 and len(r.json()) > 1 else []
    except (httpx.HTTPError, ValueError, IndexError, TypeError):
        return []
    return sorted([[int(d["date"]), d["value"]] for d in rows or [] if d.get("value") is not None])


def _counterpart_rows(payload: dict) -> list[dict]:
    out = []
    for d in (payload.get("source") or {}).get("data") or []:
        if not d.get("value"):
            continue
        var = {x["concept"].lower(): x for x in d.get("variable", [])}
        cp = var.get("counterpart-area") or next((v for k, v in var.items() if k.startswith("counterpart")), None)
        tm = var.get("time", {})
        if cp:
            out.append(
                {
                    "code": cp["id"],
                    "name": " ".join(cp["value"].split()),
                    "value": d["value"],
                    "year": str(tm.get("value", "")).replace("YR", ""),
                }
            )
    return out


async def _creditors(client: httpx.AsyncClient, iso: str) -> dict:
    """Public debt by creditor for the latest year the debtor has reported."""
    year = datetime.now(UTC).year
    for y in (year - 1, year - 2, year - 3):
        try:
            r = await client.get(
                f"{WB}/sources/6/country/{iso}/series/DT.DOD.DPPG.CD/counterpart-area/all/time/YR{y}",
                params={"format": "json", "per_page": 500},
            )
            rows = _counterpart_rows(r.json()) if r.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            rows = []
        rows = [x for x in rows if x["code"] != "WLD"]
        if rows:
            by_code = {v: k for k, v in CREDITOR_CODES.items()}
            for x in rows:
                x["iso"] = by_code.get(x["code"], "")
                x["kind"] = "country" if x["iso"] else "bonds" if x["code"] == "BND" else "institution"
            rows.sort(key=lambda x: -x["value"])
            total = sum(x["value"] for x in rows)
            return {"year": str(y), "total": total, "rows": rows[:25]}
    return {"year": "", "total": 0, "rows": []}


async def _wb_projects(client: httpx.AsyncClient, iso: str) -> list[dict]:
    """The World Bank's latest loans and grants to the country."""
    iso2 = ISO2.get(iso)
    if not iso2:
        return []
    try:
        r = await client.get(
            "https://search.worldbank.org/api/v2/projects",
            params={
                "format": "json",
                "countrycode_exact": iso2,
                "rows": 15,
                "sort": "boardapprovaldate",
                "order": "desc",
                "fl": "id,project_name,boardapprovaldate,totalcommamt,status,sector1,url,lendinginstr",
            },
        )
        projects = (r.json().get("projects") or {}).values() if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    out = []
    for p in projects:
        amt = str(p.get("totalcommamt") or "0").replace(",", "")
        sector = p.get("sector1")
        out.append(
            {
                "id": p.get("id", ""),
                "name": " ".join(str(p.get("project_name") or "").split())[:160],
                "date": str(p.get("boardapprovaldate") or "")[:10],
                "amount": float(amt) if amt.replace(".", "").isdigit() else None,
                "status": p.get("status", ""),
                "sector": (sector.get("Name") if isinstance(sector, dict) else sector) or "",
                "url": f"https://projects.worldbank.org/en/projects-operations/project-detail/{p.get('id', '')}",
            }
        )
    return [p for p in out if p["name"]]


async def country_debt(iso: str) -> dict:
    """Every figure for one borrower, from cache when fresh."""
    cached = await asyncio.to_thread(db.cache_get, f"debt:{iso}", CACHE_DAYS)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(headers={"User-Agent": collectors.UA}, timeout=httpx.Timeout(30.0)) as client:
        debt = {code: await _series(client, iso, code, src) for code, src, _, _ in DEBT_SERIES}
        outcomes = {code: await _series(client, iso, code, src) for code, src, *_ in OUTCOME_SERIES}
        creditors = await _creditors(client, iso)
        projects = await _wb_projects(client, iso)
    data = {
        "debt": debt,
        "outcomes": outcomes,
        "creditors": creditors,
        "projects": projects,
        "fetched": collectors.now_iso(),
    }
    if any(debt.values()) or creditors["rows"]:
        await asyncio.to_thread(db.cache_put, f"debt:{iso}", data)
    return data


async def bilateral(debtor: str, creditor: str) -> list[list]:
    """Public debt the debtor owes the creditor, by year: [[year, usd], ...]."""
    code = CREDITOR_CODES.get(creditor)
    if not code:
        return []
    key = f"bilat:{debtor}-{creditor}"
    cached = await asyncio.to_thread(db.cache_get, key, CACHE_DAYS)
    if cached is not None:
        return cached["years"]
    try:
        async with httpx.AsyncClient(headers={"User-Agent": collectors.UA}, timeout=httpx.Timeout(30.0)) as client:
            r = await client.get(
                f"{WB}/sources/6/country/{debtor}/series/DT.DOD.DPPG.CD/counterpart-area/{code}/time/all",
                params={"format": "json", "per_page": 100},
            )
        rows = _counterpart_rows(r.json()) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    years = sorted([[int(x["year"]), x["value"]] for x in rows if x["year"].isdigit()])
    await asyncio.to_thread(db.cache_put, key, {"years": years})
    return years


# ------------------------------------------------------------------ shaping for the page and the model


def trend(points: list[list], since: int | None = None) -> dict | None:
    """First and last value (from `since` on, if given) and the change."""
    pts = [p for p in points if since is None or p[0] >= since]
    if not pts:
        return None
    first, last = pts[0], pts[-1]
    return {
        "from_year": first[0],
        "from": first[1],
        "to_year": last[0],
        "to": last[1],
        "change": last[1] - first[1] if len(pts) > 1 else None,
        "points": pts,
    }


def spark(points: list[list], w: int = 120, h: int = 28) -> str:
    """SVG polyline points for a tiny trend line."""
    if len(points) < 2:
        return ""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    sx = (w - 4) / ((x1 - x0) or 1)
    sy = (h - 4) / ((y1 - y0) or 1)
    return " ".join(f"{2 + (x - x0) * sx:.1f},{h - 2 - (y - y0) * sy:.1f}" for x, y in points)


def view(data: dict) -> dict:
    """What the page shows: debt lines, creditors, projects, outcome changes."""
    debt_rows = []
    for code, _, label, unit in DEBT_SERIES:
        t = trend(data["debt"].get(code, []), since=2010)
        if t:
            debt_rows.append(
                {
                    "label": label,
                    "now": fmt(t["to"], unit),
                    "year": t["to_year"],
                    "then": fmt(t["from"], unit),
                    "then_year": t["from_year"],
                    "spark": spark(t["points"]),
                }
            )
    first_loan = min((int(p["date"][:4]) for p in data["projects"] if p["date"][:4].isdigit()), default=None)
    outcome_rows = []
    for code, _, area, label, unit, area_label in OUTCOME_SERIES:
        t = trend(data["outcomes"].get(code, []), since=2010)
        if t:
            ch = t["change"]
            if ch is None or fmt(t["from"], unit) == fmt(t["to"], unit) or abs(ch) <= 0.02 * max(abs(t["from"]), 1e-9):
                better = None  # one data point, or a change too small to call
            else:
                better = (ch < 0) if code == "SI.POV.LMIC" else (ch > 0)
            outcome_rows.append(
                {
                    "area": area,
                    "area_label": area_label,
                    "label": label,
                    "then": fmt(t["from"], unit),
                    "then_year": t["from_year"],
                    "now": fmt(t["to"], unit),
                    "year": t["to_year"],
                    "better": better,
                    "spark": spark(t["points"]),
                }
            )
    cred = data["creditors"]
    for r in cred["rows"]:
        r["share"] = 100 * r["value"] / cred["total"] if cred["total"] else 0
        r["amount"] = facts.human(r["value"], "usd")
        r["label"] = lender_name(r)
    for p in data["projects"]:
        p["amount_h"] = facts.human(p["amount"], "usd") if p["amount"] else "—"
    return {
        "debt_rows": debt_rows,
        "outcome_rows": outcome_rows,
        "creditors": cred,
        "projects": data["projects"],
        "first_loan": first_loan,
    }


def prompt_block(name: str, data: dict) -> str:
    lines = [f"Borrower: {name}"]
    for code, _, label, unit in DEBT_SERIES:
        pts = [p for p in data["debt"].get(code, []) if p[0] >= 2010]
        if pts:
            sample = pts[:: max(1, len(pts) // 6)] + ([pts[-1]] if pts[-1] not in pts[:: max(1, len(pts) // 6)] else [])
            lines.append(f"- {label} [{code}]: " + ", ".join(f"{y}: {fmt(v, unit)}" for y, v in sample))
    cred = data["creditors"]
    if cred["rows"]:
        top = ", ".join(
            f"{(analyze.display_name(r['iso']) if r['iso'] else r['name'])} {facts.human(r['value'], 'usd')}"
            for r in cred["rows"][:12]
        )
        lines.append(f"- Public debt by creditor, {cred['year']} (World Bank IDS): {top}")
    else:
        lines.append(
            "- No creditor breakdown (the World Bank's debt statistics cover low- and middle-income borrowers)"
        )
    for p in data["projects"][:10]:
        amount = facts.human(p["amount"], "usd") if p["amount"] else "?"
        lines.append(f"- World Bank project {p['date']}: {p['name']} ({p['sector']}), {amount}, {p['status']}")
    for code, _, _area, label, unit, _ in OUTCOME_SERIES:
        pts = [p for p in data["outcomes"].get(code, []) if p[0] >= 2010]
        if pts:
            lines.append(
                f"- {label} [{code}]: {pts[0][0]}: {fmt(pts[0][1], unit)} → {pts[-1][0]}: {fmt(pts[-1][1], unit)}"
            )
    return "\n".join(lines)


# ------------------------------------------------------------------ news


async def debt_news(iso: str) -> list[dict]:
    """Recent reporting on the country's loans, debt, procurement, corruption cases and projects."""
    n = pairs.search_name(iso)
    queries = [
        f'"{n}" loan World Bank OR IMF OR "Asian Development Bank" OR AIIB when:1y',
        f'"{n}" external debt OR borrowing when:1y',
        f'"{n}" procurement OR tender corruption OR embezzlement when:1y',
        f'"{n}" loan hospital OR school OR road OR construction project when:1y',
        f'"{n}" social assistance OR pensions OR poverty program when:1y',
    ]
    found: dict[str, dict] = {}
    async with httpx.AsyncClient(headers={"User-Agent": collectors.UA}, timeout=collectors.TIMEOUT) as client:
        for q in queries:
            url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en"
            try:
                r = await client.get(url, follow_redirects=True)
                items = collectors.parse_gnews(r.content) if r.status_code < 400 else []
            except (httpx.HTTPError, collectors.CollectError):
                items = []
            for it in items[:12]:
                found.setdefault(it["url"], it)
            await asyncio.sleep(0.4)
    items = sorted(found.values(), key=lambda x: x["published"], reverse=True)[:50]
    return [{**it, "id": -(i + 1)} for i, it in enumerate(items)]


# ------------------------------------------------------------------ the AI

DEBT_SYSTEM = """You are the public-finance analyst of WorldScope. A student wants to understand a country's
borrowing: whom it owes, what the money was for, and what actually changed at home after it borrowed.

You get hard figures (World Bank: debt over time, creditors, World Bank projects, and indicators of
investment, education, health, poverty, social protection, control of corruption and government
effectiveness) and numbered recent news items.

Rules:
- Cite news by item number in "evidence". Figures need no number, but say which figure you use.
- Corruption, embezzlement and crime: report only what the cited news says, as reported or alleged unless
  it says there was a conviction. Never accuse anyone yourself; name a person only if the item names them.
- Borrowing and outcomes moving together does not prove one caused the other: say when a change has
  other likely causes, and when the data are too thin to tell.
- No invented loans, amounts, projects or cases. If there is no information for an area, say so.

Return JSON only:
{
 "summary": "3-5 sentences: the shape of the debt, who the main creditors are, and the overall picture",
 "sustainability": {"level": "low|moderate|high|critical", "note": "1-2 sentences, using debt/GNI and debt service"},
 "creditors": [{"name": "creditor as listed", "logic": "one sentence: what this lender gets from lending (influence,
               contracts, exports, policy reform) and any conditions or leverage"}],
 "uses": [{"what": "what borrowed money went to", "lender": "...", "evidence": [n]}],
 "effects": [{"area": "procurement|corruption|construction|investment|social|education|health",
              "state": "improved|mixed|worsened|unclear",
              "finding": "1-3 sentences on what changed after the borrowing, with the figure or report behind it",
              "evidence": [n]}],
 "red_flags": [{"text": "a reported risk or problem, worded as reported", "evidence": [n]}],
 "watch": ["signal to watch", "..."],
 "data_note": "one sentence on how complete the figures and news were"
}
"effects": exactly the seven areas, in this order: procurement, corruption, construction, investment,
social, education, health. creditors: the 3-6 largest. uses: 2-8. red_flags: 0-5. watch: 2-4.
Write every text field in {lang}."""


def _ev(lst, n_items: int) -> list[int]:
    out = []
    for n in lst or []:
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= n_items and n not in out:
            out.append(n)
    return out


def clean_report(raw: dict, items: list[dict]) -> dict:
    n = len(items)
    levels = {"low", "moderate", "high", "critical"}
    states = {"improved", "mixed", "worsened", "unclear"}
    by_area = {}
    for e in raw.get("effects") or []:
        if isinstance(e, dict) and e.get("area") in dict(AREAS) and e["area"] not in by_area:
            by_area[e["area"]] = {
                "area": e["area"],
                "state": e.get("state") if e.get("state") in states else "unclear",
                "finding": str(e.get("finding", ""))[:700],
                "evidence": _ev(e.get("evidence"), n),
            }
    effects = [by_area.get(k) or {"area": k, "state": "unclear", "finding": "", "evidence": []} for k, _ in AREAS]
    sus = raw.get("sustainability") if isinstance(raw.get("sustainability"), dict) else {}
    out = {
        "summary": str(raw.get("summary", ""))[:1500],
        "sustainability": {
            "level": sus.get("level") if sus.get("level") in levels else "moderate",
            "note": str(sus.get("note", ""))[:400],
        },
        "creditors": [
            {"name": str(c.get("name", ""))[:80], "logic": str(c.get("logic", ""))[:400]}
            for c in (raw.get("creditors") or [])[:6]
            if isinstance(c, dict) and c.get("name")
        ],
        "uses": [
            {
                "what": str(u.get("what", ""))[:300],
                "lender": str(u.get("lender", ""))[:80],
                "evidence": _ev(u.get("evidence"), n),
            }
            for u in (raw.get("uses") or [])[:8]
            if isinstance(u, dict) and u.get("what")
        ],
        "effects": effects,
        "red_flags": [
            {"text": str(f.get("text", ""))[:400], "evidence": _ev(f.get("evidence"), n)}
            for f in (raw.get("red_flags") or [])[:5]
            if isinstance(f, dict) and f.get("text")
        ],
        "watch": [str(w)[:200] for w in (raw.get("watch") or [])[:4] if w],
        "data_note": str(raw.get("data_note", ""))[:300],
    }
    # a red flag about crime or corruption must rest on a report
    out["red_flags"] = [f for f in out["red_flags"] if f["evidence"]]
    used = set()
    for group in (out["uses"], out["effects"], out["red_flags"]):
        for x in group:
            used.update(x["evidence"])
    out["sources"] = [
        {
            "n": i,
            "title": items[i - 1]["title"],
            "url": items[i - 1]["url"],
            "publisher": items[i - 1]["publisher"],
            "published": items[i - 1]["published"][:10],
        }
        for i in sorted(used)
    ]
    if not out["summary"]:
        raise analyze.AIError("AI to'liq tahlil qaytarmadi, qayta urinib ko'ring")
    return out


def write_report(iso: str, lang: str, data: dict, items: list[dict]) -> dict:
    name = analyze.COUNTRY_INFO[iso]["name"]
    system = DEBT_SYSTEM.replace("{lang}", analyze.LANG_NAMES.get(lang, "English"))
    news = "\n".join(analyze.numbered(items)) if items else "(no recent news found)"
    prompt = f"Hard figures:\n{prompt_block(name, data)}\n\nNews items:\n{news}\n\nReturn the JSON."
    for attempt in range(2):
        text, model = analyze.complete(system, prompt, json_mode=True)
        try:
            raw = analyze._json_from(text)
            break
        except analyze.AIError:
            if attempt == 1:
                raise
    report = clean_report(raw, items)
    report.update(model=model, news_count=len(items))
    return report
