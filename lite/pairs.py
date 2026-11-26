"""Two-country relationship analysis: the 196 states, their power tiers, news for any pair, and the AI.

The tiers are a deliberate simplification for orientation, not a ranking of worth:
  1. great powers: global military, economic and diplomatic reach;
  2. middle powers: strong regional weight or a developed economy;
  3. developing countries (the "third world" of older usage).
Edit TIER_1 / TIER_2 below to change them; everything else is tier 3.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote_plus

import httpx

import analyze
import collectors

# 193 UN members, plus the Holy See and Palestine (UN observers) and Taiwan.
SOVEREIGN = """AFG ALB DZA AND AGO ATG ARG ARM AUS AUT AZE BHS BHR BGD BRB BLR BEL BLZ BEN BTN BOL BIH BWA BRA BRN
BGR BFA BDI CPV KHM CMR CAN CAF TCD CHL CHN COL COM COG COD CRI CIV HRV CUB CYP CZE PRK DNK DJI DMA DOM ECU EGY SLV
GNQ ERI EST SWZ ETH FJI FIN FRA GAB GMB GEO DEU GHA GRC GRD GTM GIN GNB GUY HTI HND HUN ISL IND IDN IRN IRQ IRL ISR
ITA JAM JPN JOR KAZ KEN KIR KWT KGZ LAO LVA LBN LSO LBR LBY LIE LTU LUX MDG MWI MYS MDV MLI MLT MHL MRT MUS MEX FSM
MDA MCO MNG MNE MAR MOZ MMR NAM NRU NPL NLD NZL NIC NER NGA MKD NOR OMN PAK PLW PAN PNG PRY PER PHL POL PRT QAT KOR
ROU RUS RWA KNA LCA VCT WSM SMR STP SAU SEN SRB SYC SLE SGP SVK SVN SLB SOM ZAF SSD ESP LKA SDN SUR SWE CHE SYR TJK
TZA THA TLS TGO TON TTO TUN TUR TKM TUV UGA UKR ARE GBR USA URY UZB VUT VEN VNM YEM ZMB ZWE VAT PSE TWN""".split()

TIER_1 = set("USA CHN RUS GBR FRA DEU JPN IND".split())
TIER_2 = set(
    """CAN ITA KOR AUS BRA MEX IDN TUR SAU ZAF ARG ESP NLD CHE SWE NOR POL ISR IRN PAK EGY ARE QAT NGA VNM
MYS THA SGP UKR BEL AUT DNK FIN IRL NZL PRT GRC CZE KAZ CHL COL PHL BGD DZA TWN LUX ISL EST LVA LTU SVK SVN HRV CYP
MLT MCO LIE AND SMR VAT BRN KWT BHR OMN HUN ROU BGR URY""".split()
)

TIERS = {1: "Qudratli davlatlar", 2: "O'rta davlatlar", 3: "Rivojlanayotgan davlatlar (3-dunyo)"}
TIER_SHORT = {1: "qudratli", 2: "o'rta", 3: "rivojlanayotgan"}
TIER_EN = {1: "great power", 2: "middle power", 3: "developing country"}

# Names Google News readers would use, where the map's names are too formal or too short.
SEARCH_NAMES = {
    "USA": "United States",
    "RUS": "Russia",
    "KOR": "South Korea",
    "PRK": "North Korea",
    "LAO": "Laos",
    "COG": "Republic of the Congo",
    "COD": "DR Congo",
    "SWZ": "Eswatini",
    "CPV": "Cape Verde",
    "GMB": "Gambia",
    "CIV": "Ivory Coast",
    "FSM": "Micronesia",
    "VAT": "Vatican",
    "CZE": "Czech Republic",
    "BIH": "Bosnia",
    "GBR": "Britain",
    "ARE": "UAE",
    "TLS": "East Timor",
}


def tier(iso: str) -> int:
    return 1 if iso in TIER_1 else 2 if iso in TIER_2 else 3


def search_name(iso: str) -> str:
    return SEARCH_NAMES.get(iso) or analyze.COUNTRY_INFO.get(iso, {}).get("name", iso)


def country_options() -> list[dict]:
    """The 196 states for the pickers, grouped by tier and sorted by their Uzbek name."""
    out = [
        {"iso": c, "name": analyze.display_name(c), "en": analyze.COUNTRY_INFO[c]["name"], "tier": tier(c)}
        for c in SOVEREIGN
        if c in analyze.COUNTRY_INFO
    ]
    return sorted(out, key=lambda x: (x["tier"], x["name"]))


def valid(iso: str) -> bool:
    return iso in SOVEREIGN and iso in analyze.COUNTRY_INFO


def key(a: str, b: str, lang: str, c: str = "") -> str:
    """Cache key for a pair (order-free), or for a pair seen through a third country."""
    x, y = sorted((a, b))
    return f"{x}-{y}-{lang}" + (f"~{c}" if c else "")


# ------------------------------------------------------------------ news for the pair


async def pair_news(a: str, b: str) -> list[dict]:
    """Recent headlines that name both countries, from Google News, newest first.

    Close pairs (USA-Russia) get plenty in a month; rare ones (Japan-Madagascar) need a year, and a
    looser query if the exact one finds little.
    """
    na, nb = search_name(a), search_name(b)
    queries = [f'"{na}" "{nb}" when:30d', f'"{na}" "{nb}" when:1y', f"{na} {nb} relations"]
    found: dict[str, dict] = {}
    async with httpx.AsyncClient(headers={"User-Agent": collectors.UA}, timeout=collectors.TIMEOUT) as client:
        for q in queries:
            url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-US&gl=US&ceid=US:en"
            try:
                r = await client.get(url, follow_redirects=True)
                items = collectors.parse_gnews(r.content) if r.status_code < 400 else []
            except (httpx.HTTPError, collectors.CollectError):
                items = []
            for it in items:
                found.setdefault(it["url"], it)
            if len(found) >= 25:
                break
            await asyncio.sleep(0.5)
    return sorted(found.values(), key=lambda x: x["published"], reverse=True)[:40]


def merge_news(db_articles: list[dict], fetched: list[dict], limit: int = 45) -> list[dict]:
    """Stored articles about both countries first, then fresh search results; one per headline."""
    out, seen = [], set()
    for i, a in enumerate([*db_articles, *fetched]):
        k = analyze.title_key(a["title"]) or a["url"]
        if k in seen:
            continue
        seen.add(k)
        out.append({**a, "id": a.get("id") or -(i + 1)})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ the AI

PAIR_SYSTEM = """You are the international-relations analyst of WorldScope. A student chose two countries
and wants to understand their relationship: what is good in it, what is bad, where it is heading and why.

You get: the two countries with their power tier (great power / middle power / developing country),
and a numbered list of recent news items that mention both (it may be short or empty for distant pairs).

Use two kinds of knowledge and keep them apart:
- the numbered news items: cite them as evidence numbers;
- well-established background (history, treaties, trade structure, alliances, diaspora, geography,
  votes at the UN). Use it freely, but give such points an empty evidence list, and never present an
  event as recent unless a news item reports it. Do not invent figures, dates, quotes or agreements.
If the countries have little direct relationship, say so plainly and explain the indirect links
(through third countries, blocs, markets) instead of inflating it.

Return JSON only:
{{
 "score": integer from -5 (hostile) to 5 (allied),
 "label": "2-4 words, e.g. 'Strategic partners', 'Cold rivals', 'Distant but friendly'",
 "essence": "one sentence, max 30 words: the core logic of the relationship (what each side wants from
            the other, or what divides them)",
 "summary": "3-5 sentences",
 "asymmetry": "1-2 sentences on how the power gap (or parity) between them shapes the relationship",
 "good": [{{"area": "...", "text": "1-2 sentences", "evidence": [n]}}],
 "bad":  [{{"area": "...", "text": "1-2 sentences", "evidence": [n]}}],
 "areas": [{{"area": "...", "state": "positive|mixed|negative|none", "note": "one sentence"}}],
 "third_parties": [{{"iso3": "XXX", "role": "one sentence on how this country shapes the pair"}}],
 "scenarios": [{{"title": "...", "probability": "high|medium|low", "horizon": "e.g. 1-2 yil",
                "trigger": "what would set it off", "outcome": "2-3 sentences: what it would lead to,
                for each side and for the region"}}],
 "watch": ["short signal to watch", "..."],
 "data_note": "one sentence on how much fresh news there was and how that limits the analysis",
 "numbers_note": "1-2 sentences: what the hard figures (if given) say about this relationship"{extra_fields}
}}

Lists: good 2-6, bad 1-6 (say honestly if there is little friction), areas exactly these five in this
order: Diplomatiya, Iqtisodiyot va savdo, Xavfsizlik, Energetika va resurslar, Madaniyat va odamlar
(translate the area names into the output language), third_parties 0-3, scenarios exactly 3 (the most
likely course, a better one, a worse one, with probabilities that make sense together), watch 2-4.
Forecasts are reasoned scenarios, not certainties: base each on interests and trends you named.
Hard figures (World Bank, UN Comtrade) may be given: use them for size, dependence and trade weight,
say "Jahon banki" / "UN Comtrade" when you rely on one, and never invent other figures.
{extra_rules}
Write every text field in {lang}."""

PREVIOUS_FIELD = """,
 "previous_check": {"unfolding": "title of the earlier scenario closest to what is happening now",
                    "verdict": "2-3 sentences: which earlier forecasts held, which did not, and why",
                    "change": "one sentence on what changed since the earlier analysis"}"""
PREVIOUS_RULE = """An earlier analysis of this pair is given with its date, score and scenarios. Judge it honestly
against the news: fill previous_check, and move the score only if the news justifies it."""

TRIANGLE_FIELD = """,
 "triangle": {"summary": "2-3 sentences on how country C shapes the A-B relationship",
              "a_c": {"score": -5..5, "note": "one sentence on A and C"},
              "b_c": {"score": -5..5, "note": "one sentence on B and C"},
              "who_benefits": "one sentence: which of the three gains most from the current pattern and why",
              "leverage": "one sentence: who holds leverage over whom"}"""
TRIANGLE_RULE = """A third country C is given: analyse the triangle too. Keep good/bad/areas/scenarios about A and B,
but explain in them where C is the reason."""


def _clean_pair(raw: dict, items: list[dict], a: str, b: str) -> dict:
    def ev(lst):
        out = []
        for n in lst or []:
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(items) and n not in out:
                out.append(n)
        return out

    def points(lst):
        res = []
        for p in (lst or [])[:6]:
            if isinstance(p, dict) and p.get("text"):
                res.append(
                    {
                        "area": str(p.get("area", ""))[:60],
                        "text": str(p["text"])[:500],
                        "evidence": ev(p.get("evidence")),
                    }
                )
        return res

    score = _score(raw.get("score", 0))
    states = {"positive", "mixed", "negative", "none"}
    probs = {"high", "medium", "low"}
    used = set()
    out = {
        "score": score,
        "label": str(raw.get("label", ""))[:60],
        "essence": str(raw.get("essence", ""))[:300],
        "summary": str(raw.get("summary", ""))[:1200],
        "asymmetry": str(raw.get("asymmetry", ""))[:500],
        "good": points(raw.get("good")),
        "bad": points(raw.get("bad")),
        "areas": [
            {
                "area": str(x.get("area", ""))[:60],
                "state": x.get("state") if x.get("state") in states else "none",
                "note": str(x.get("note", ""))[:300],
            }
            for x in (raw.get("areas") or [])[:6]
            if isinstance(x, dict)
        ],
        "third_parties": [
            {"iso3": str(x.get("iso3", "")).upper()[:3], "role": str(x.get("role", ""))[:300]}
            for x in (raw.get("third_parties") or [])[:4]
            if isinstance(x, dict)
            and str(x.get("iso3", "")).upper()[:3] in analyze.COUNTRY_INFO
            and str(x.get("iso3", "")).upper()[:3] not in (a, b)
        ],
        "scenarios": [
            {
                "title": str(x.get("title", ""))[:120],
                "probability": x.get("probability") if x.get("probability") in probs else "medium",
                "horizon": str(x.get("horizon", ""))[:40],
                "trigger": str(x.get("trigger", ""))[:300],
                "outcome": str(x.get("outcome", ""))[:700],
            }
            for x in (raw.get("scenarios") or [])[:4]
            if isinstance(x, dict) and x.get("title")
        ],
        "watch": [str(w)[:200] for w in (raw.get("watch") or [])[:5] if w],
        "data_note": str(raw.get("data_note", ""))[:300],
    }
    for p in out["good"] + out["bad"]:
        used.update(p["evidence"])
    out["sources"] = [
        {
            "n": n,
            "title": items[n - 1]["title"],
            "url": items[n - 1]["url"],
            "publisher": items[n - 1]["publisher"],
            "published": items[n - 1]["published"][:10],
        }
        for n in sorted(used)
    ]
    if not out["essence"] or not (out["good"] or out["bad"]):
        raise analyze.AIError("AI to'liq tahlil qaytarmadi, qayta urinib ko'ring")
    return out


def _score(v) -> int:
    try:
        return max(-5, min(5, int(v)))
    except (TypeError, ValueError):
        return 0


def write_pair(
    a: str, b: str, lang: str, items: list[dict], facts_text: str = "", previous: dict | None = None, c: str = ""
) -> dict:
    ta, tb = tier(a), tier(b)
    head = (
        f"Country A: {analyze.COUNTRY_INFO[a]['name']} ({a}), {TIER_EN[ta]}.\n"
        f"Country B: {analyze.COUNTRY_INFO[b]['name']} ({b}), {TIER_EN[tb]}.\n"
    )
    if c:
        head += f"Country C (third party): {analyze.COUNTRY_INFO[c]['name']} ({c}), {TIER_EN[tier(c)]}.\n"
    if facts_text:
        head += f"\nHard figures:\n{facts_text}\n"
    if previous:
        scen = "; ".join(f"{x['title']} ({x['probability']})" for x in previous.get("scenarios", []))
        head += (
            f"\nEarlier analysis of {previous.get('created', '')[:10]}: score {previous.get('score')}, "
            f"'{previous.get('label', '')}'. Its scenarios: {scen}.\n"
        )
    news = "\n".join(analyze.numbered(items)) if items else "(no recent news mentioning both)"
    system = PAIR_SYSTEM.format(
        lang=analyze.LANG_NAMES.get(lang, "English"),
        extra_fields=(PREVIOUS_FIELD if previous else "") + (TRIANGLE_FIELD if c else ""),
        extra_rules="\n".join(r for r, on in ((PREVIOUS_RULE, previous), (TRIANGLE_RULE, c)) if on),
    )
    prompt = f"{head}\nNews items:\n{news}\n\nReturn the JSON."
    for attempt in range(2):
        text, model = analyze.complete(system, prompt, json_mode=True)
        try:
            raw = analyze._json_from(text)
            break
        except analyze.AIError:
            if attempt == 1:
                raise
    result = _clean_pair(raw, items, a, b)
    result["numbers_note"] = str(raw.get("numbers_note", ""))[:500]
    pc = raw.get("previous_check")
    if previous and isinstance(pc, dict):
        result["previous_check"] = {
            "since": previous.get("created", "")[:10],
            "old_score": previous.get("score"),
            "unfolding": str(pc.get("unfolding", ""))[:160],
            "verdict": str(pc.get("verdict", ""))[:800],
            "change": str(pc.get("change", ""))[:300],
        }
    tri = raw.get("triangle")
    if c and isinstance(tri, dict):

        def side(k):
            d = tri.get(k) if isinstance(tri.get(k), dict) else {}
            return {"score": _score(d.get("score")), "note": str(d.get("note", ""))[:300]}

        result["triangle"] = {
            "c": c,
            "summary": str(tri.get("summary", ""))[:800],
            "a_c": side("a_c"),
            "b_c": side("b_c"),
            "who_benefits": str(tri.get("who_benefits", ""))[:300],
            "leverage": str(tri.get("leverage", ""))[:300],
        }
    result.update(model=model, news_count=len(items))
    return result
