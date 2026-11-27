"""Plain words: one short Uzbek sentence per big story that a child could follow, and a glossary.

The AI writes, for the most-covered stories of the day in one call (scheduled job "explain"), an
Uzbek headline, a plain sentence and what it may mean for people in Uzbekistan. Only AI-written
lines are shown: a guess built from tags was too often wrong, and a wrong line costs more trust than
a missing one. The glossary explains the site's hard words where they appear on any page.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime

import analyze
import db

PER_RUN = 24
TOPIC = {  # category → (emoji, plain words); also the topics offered when a visitor says what interests them
    "conflict": ("⚔️", "urush yoki to'qnashuv"),
    "defense": ("🛡️", "qurol va armiya"),
    "diplomacy": ("🤝", "davlatlar o'rtasidagi muzokara"),
    "economy": ("💰", "pul va iqtisod"),
    "trade": ("🚢", "savdo: mol sotish va sotib olish"),
    "energy": ("⚡", "gaz, neft va elektr"),
    "technology": ("💻", "texnologiya"),
    "cybersecurity": ("🔐", "internet xavfsizligi"),
    "environment": ("🌱", "tabiat va ob-havo"),
    "public-health": ("🏥", "sog'liq"),
    "society": ("👥", "odamlar hayoti"),
}

GLOSSARY = {
    "sanksiya": "Jazo: bir davlat boshqa davlat, kompaniya yoki odam bilan savdo qilishni taqiqlaydi.",
    "ikkilamchi sanksiya": "Sanksiyadagi kompaniya bilan ishlagan boshqa kompaniyani ham jazolash.",
    "OFAC": "AQSH Moliya vazirligining sanksiya bo'limi; qora ro'yxat yuritadi.",
    "BMT": "Birlashgan Millatlar Tashkiloti — deyarli hamma davlatlar a'zo bo'lgan tashkilot.",
    "YeI": "Yevropa Ittifoqi — 27 ta Yevropa davlatining birlashmasi.",
    "YaIM": "Bir yilda mamlakatda ishlab chiqarilgan hamma narsaning narxi.",
    "YaMD": "YaIMga o'xshash: mamlakat fuqarolari bir yilda topgan jami daromad.",
    "inflyatsiya": "Narxlar o'sishi: bir yil oldin 100 so'm turgan narsa endi qanchaga turadi.",
    "joriy hisob": "Mamlakatga kirgan va chiqqan pulning farqi; minus — chiqqani ko'p.",
    "eksport": "Boshqa davlatlarga mol sotish.",
    "import": "Boshqa davlatlardan mol sotib olish.",
    "xavf indeksi": "0 dan 100 gacha ball: qancha katta bo'lsa, o'sha davlat bilan ishlash shuncha xavfli.",
    "ssenariy": "Kelajakda nima bo'lishi mumkinligi haqidagi taxmin.",
    "lager": "Bir xil qarashdagi gazeta va telekanallar guruhi.",
    "OAV": "Ommaviy axborot vositalari: gazeta, sayt, televizor, radio.",
    "Brier": "Bashoratlar qanchalik to'g'ri chiqqanini o'lchaydigan son: 0 — juda yaxshi.",
    "kalibr": "AI aytgan ehtimol bilan haqiqatda bo'lgani qanchalik mos.",
    "ko'p manbali": "Bir voqeani bir nechta turli nashr yozgan — demak muhim va ishonchliroq.",
    "GDELT": "Butun dunyo yangiliklarini yig'adigan ochiq xizmat.",
    "RSS": "Saytlar yangiliklarini avtomatik tarqatish usuli.",
    "diplomatiya": "Davlatlar urushmasdan, gaplashib kelishishi.",
    "mojaro": "Urush yoki kuchli janjal.",
    "kreditor": "Qarz bergan davlat yoki bank.",
    "XVF": "Xalqaro Valyuta Fondi — davlatlarga qarz beradigan xalqaro bank.",
    "Jahon banki": "Davlatlarga qarz va maslahat beradigan xalqaro bank.",
    "Comtrade": "BMTning savdo bazasi: kim kimga qancha mol sotganini yozadi.",
    "AI": "Sun'iy intellekt — matnlarni o'qib, xulosa yozadigan kompyuter dasturi.",
    "tahlil": "Voqeani bo'laklarga ajratib, sababini tushuntirish.",
}


def key(group: list[dict]) -> str:
    return analyze.title_key(group[0]["title"]) or group[0]["url"]


def stored(keys: list[str]) -> dict[str, dict]:
    if not keys:
        return {}
    marks = ",".join("?" * len(keys))
    rows = db.rows(f"SELECT key, data FROM cache WHERE key IN ({marks})", [f"explain:{k}" for k in keys])
    out = {}
    for r in rows:
        with contextlib.suppress(ValueError):
            out[r["key"][8:]] = json.loads(r["data"])
    return out


def for_groups(groups: list[list[dict]]) -> dict[str, dict]:
    """AI lines for the groups that have one; the others are simply absent."""
    return stored([key(g) for g in groups])


SYSTEM = """You explain world news to someone who knows nothing about politics, in Uzbek (Latin script).
For each numbered news story write:
- "title": the headline translated into natural Uzbek (Latin script), at most 14 words, same facts,
  country names in Uzbek. If the headline is already Uzbek, tidy it.
- "simple": ONE short sentence (at most 18 words) a 10-year-old understands: who did what. Use plain
  words, no jargon, no abbreviations.
- "why": ONE short sentence on how this may touch people living in Uzbekistan (prices, jobs, migrants
  abroad, trade, gas, safety, the region). Write "" unless there is a real, direct link: do not invent one.
- "emoji": one fitting emoji.
Only use what the headline and snippet say. Return ONLY JSON:
{"items": [{"n": 1, "title": "...", "simple": "...", "why": "...", "emoji": "..."}]}"""


def write(groups: list[list[dict]]) -> dict[str, dict]:
    """One AI call for the stories that have no sentence yet; stores and returns them."""
    todo = [(key(g), g) for g in groups if key(g) not in stored([key(g)])][:PER_RUN]
    if not todo:
        return {}
    lines = []
    for n, (_, g) in enumerate(todo, 1):
        a = g[0]
        snip = f" — {a['summary'][:200]}" if a.get("summary") else ""
        lines.append(f"[{n}] {a['title']}{snip}")
    prompt = "Stories:\n" + "\n".join(lines) + "\n\nReturn the JSON."
    text, _model = analyze.complete(SYSTEM, prompt, json_mode=True)
    raw = analyze._json_from(text)
    out = {}
    for it in raw.get("items") or []:
        try:
            n = int(it.get("n"))
        except (TypeError, ValueError):
            continue
        simple = str(it.get("simple", "")).strip()
        if not (1 <= n <= len(todo)) or not simple:
            continue
        k = todo[n - 1][0]
        val = {"emoji": str(it.get("emoji", "🌍")).strip()[:4] or "🌍", "simple": simple[:220],
               "title": str(it.get("title", "")).strip()[:160],
               "why": str(it.get("why", "")).strip()[:220], "ai": True}  # fmt: skip
        db.cache_put(f"explain:{k}", val)
        out[k] = val
    return out


def risk_sentence(r: dict | None, name: str) -> str:
    """'O'zbekiston bilan ishlash xavfi o'rtachadan past (100 dan 39).' plus the biggest and smallest part."""
    if not r or r.get("score") is None:
        return f"{name} uchun xavf indeksini hisoblashga ma'lumot yetarli emas."
    parts = [c for c in r["components"] if c["value"] is not None]
    worst = max(parts, key=lambda c: c["value"])
    best = min(parts, key=lambda c: c["value"])
    s = f"{name} bilan savdo va hamkorlik xavfi — {r['band']} (100 balldan {round(r['score'])})."
    if worst["value"] >= 40:
        s += f" Eng ko'p tashvish: {worst['label'].lower()} ({round(worst['value'])} ball)."
    if best is not worst and best["value"] < 40:
        s += f" Eng kam xavf: {best['label'].lower()} ({round(best['value'])} ball)."
    return s


def stale(year: str | int | None, years: int = 5) -> bool:
    try:
        return int(str(year)[:4]) < datetime.now(UTC).year - years
    except (TypeError, ValueError):
        return False
