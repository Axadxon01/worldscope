"""Analysis: tag categories and countries, group similar headlines, write the AI brief.

Everything except the brief runs without any API key.
"""

from __future__ import annotations

import csv
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

import httpx

HERE = Path(__file__).parent

# ------------------------------------------------------------------ categories
# Stems in English, Russian and Uzbek (Latin). Matched at word starts.
# Terms ending in "*" match as word prefixes (stems); the rest match whole words only.
CATEGORIES: dict[str, tuple[str, list[str]]] = {
    "conflict": (
        "Mojarolar",
        [
            "war",
            "wars",
            "attack*",
            "strike*",
            "missile*",
            "drone*",
            "killed",
            "shelling",
            "ceasefire*",
            "clash*",
            "militant*",
            "terror*",
            "hostage*",
            "invasion",
            "offensive",
            "airstrike*",
            "rebel*",
            "войн*",
            "атак*",
            "удар*",
            "ракет*",
            "дрон*",
            "беспилот*",
            "погиб*",
            "обстрел*",
            "перемири*",
            "теракт*",
            "боевик*",
            "наступлен*",
            "urush*",
            "hujum*",
            "zarba*",
            "raketa*",
            "halok*",
            "otashkesim*",
        ],
    ),
    "defense": (
        "Mudofaa",
        [
            "army",
            "armies",
            "military",
            "defence",
            "defense",
            "troops",
            "nato",
            "weapon*",
            "arms",
            "navy",
            "pentagon",
            "арми*",
            "военн*",
            "войск*",
            "нато",
            "оружи*",
            "минобороны",
            "armiya*",
            "harbiy",
            "qo'shin*",
            "qurol*",
            "mudofaa*",
        ],
    ),
    "diplomacy": (
        "Diplomatiya",
        [
            "talks",
            "summit*",
            "minister*",
            "visit*",
            "ambassador*",
            "treaty",
            "agreement*",
            "diplomat*",
            "negotiat*",
            "переговор*",
            "саммит*",
            "министр*",
            "визит*",
            "посол",
            "посла",
            "договор*",
            "соглашени*",
            "дипломат*",
            "muzokara*",
            "sammit*",
            "vazir*",
            "tashrif*",
            "elchi*",
            "shartnoma*",
            "kelishuv*",
        ],
    ),
    "economy": (
        "Iqtisodiyot",
        [
            "economy",
            "economic*",
            "inflation",
            "gdp",
            "central bank",
            "interest rate*",
            "market*",
            "stock*",
            "budget*",
            "debt*",
            "currency",
            "investment*",
            "imf",
            "world bank",
            "экономи*",
            "инфляц*",
            "ввп",
            "центробанк*",
            "ставк*",
            "рын*к*",
            "бюджет*",
            "долг*",
            "валют*",
            "инвестиц*",
            "iqtisod*",
            "inflyatsiya*",
            "markaziy bank*",
            "byudjet*",
            "qarz*",
            "valyuta*",
            "investitsiya*",
            "всемирн* банк*",
            "доллар*",
            "млрд",
            "кредит*",
            "финансир*",
            "dollar*",
            "mlrd",
            "kredit*",
            "billion",
            "loan*",
            "financ*",
        ],
    ),
    "trade": (
        "Savdo",
        [
            "trade",
            "trading",
            "tariff*",
            "export*",
            "import*",
            "sanction*",
            "supply chain*",
            "wto",
            "торгов*",
            "пошлин*",
            "экспорт*",
            "импорт*",
            "санкци*",
            "savdo*",
            "eksport*",
            "sanksiya*",
            "bojxona*",
        ],
    ),
    "energy": (
        "Energetika",
        [
            "oil",
            "gas",
            "opec",
            "pipeline*",
            "energy",
            "power plant*",
            "nuclear",
            "electricity",
            "lng",
            "нефт*",
            "газ",
            "газа",
            "газов*",
            "газопровод*",
            "опек",
            "трубопровод*",
            "энерг*",
            "аэс",
            "электроэнерг*",
            "neft*",
            "gaz",
            "energetika*",
            "elektr*",
            "atom",
        ],
    ),
    "technology": (
        "Texnologiya",
        [
            "ai",
            "artificial intelligence",
            "chip",
            "chips",
            "semiconductor*",
            "tech",
            "technolog*",
            "satellite*",
            "startup*",
            "искусствен* интеллект*",
            "чип*",
            "полупроводник*",
            "технолог*",
            "спутник*",
            "sun'iy intellekt*",
            "texnologiya*",
            "startap*",
        ],
    ),
    "cybersecurity": (
        "Kiberxavfsizlik",
        [
            "cyber*",
            "hack*",
            "ransomware",
            "malware",
            "data breach*",
            "vulnerabilit*",
            "кибер*",
            "хакер*",
            "утечк*",
            "уязвим*",
            "kiber*",
            "xaker*",
        ],
    ),
    "environment": (
        "Ekologiya",
        [
            "climate",
            "flood*",
            "earthquake*",
            "wildfire*",
            "drought*",
            "storm*",
            "emission*",
            "климат*",
            "наводнени*",
            "землетрясени*",
            "засух*",
            "шторм*",
            "iqlim*",
            "suv toshqini*",
            "zilzila*",
            "qurg'oqchilik*",
        ],
    ),
    "public-health": (
        "Sog'liqni saqlash",
        [
            "health",
            "virus",
            "outbreak*",
            "vaccin*",
            "epidemic*",
            "pandemic*",
            "hospital*",
            "здравоохранени*",
            "вирус*",
            "вакцин*",
            "эпидеми*",
            "пандеми*",
            "sog'liq*",
            "vaksina*",
            "epidemiya*",
            "kasalxona*",
        ],
    ),
    "society": (
        "Jamiyat",
        [
            "protest*",
            "election*",
            "vote",
            "votes",
            "voting",
            "court*",
            "human rights",
            "migrant*",
            "refugee*",
            "education",
            "протест*",
            "выбор*",
            "суд",
            "суда",
            "суде",
            "права человека",
            "мигрант*",
            "беженц*",
            "образовани*",
            "namoyish*",
            "saylov*",
            "sud",
            "sudi",
            "huquq*",
            "qochqin*",
            "ta'lim*",
        ],
    ),
}

# Names that the English country list does not catch: other languages and demonyms.
EXTRA_NAMES: dict[str, list[str]] = {
    "UZB": [
        "uzbek*",
        "tashkent",
        "mirziyoyev",
        "узбек*",
        "ташкент*",
        "мирзиёев*",
        "o'zbek*",
        "toshkent*",
        "oliy majlis",
        "олий мажлис*",
        "fergana",
        "фергана",
        "ферган*",
        "farg'ona*",
        "samarkand",
        "самарканд*",
        "samarqand*",
        "bukhara",
        "бухар*",
        "buxoro*",
        "andijan",
        "андижан*",
        "andijon*",
        "namangan*",
        "наманган*",
        "navoi",
        "навои*",
        "navoiy*",
        "karakalpak*",
        "каракалпак*",
        "qoraqalpog'*",
        "khorezm",
        "хорезм*",
        "xorazm*",
        "kashkadarya",
        "кашкадар*",
        "qashqadaryo*",
        "surkhandarya",
        "сурхандар*",
        "surxondaryo*",
        "jizzakh",
        "джизак*",
        "jizzax*",
        "termez",
        "термез*",
        "termiz*",
        "nukus",
        "нукус*",
        "sirdaryo*",
        "сырдар*",
        "gulistan",
    ],
    "KAZ": ["kazakh*", "astana", "tokayev", "казах*", "астан*", "токаев*", "qozog'*"],
    "KGZ": ["kyrgyz*", "bishkek", "japarov", "кыргыз*", "киргиз*", "бишкек*", "жапаров*", "qirg'iz*"],
    "TJK": ["tajik*", "dushanbe", "rahmon", "таджик*", "душанбе", "рахмон*", "tojik*"],
    "TKM": ["turkmen*", "ashgabat", "туркмен*", "ашхабад*", "turkman*"],
    "AFG": ["afghan*", "kabul", "taliban", "афган*", "кабул*", "талиб*", "afg'on*"],
    "RUS": ["russia*", "kremlin", "moscow", "putin", "росси*", "кремл*", "москв*", "путин*", "rossiya*", "moskva*"],
    "UKR": ["ukrain*", "kyiv", "kiev", "zelensky*", "украин*", "киев*", "зеленск*", "ukraina*"],
    "USA": ["u.s.", "washington", "white house", "trump", "сша", "вашингтон*", "трамп*", "aqsh"],
    "CHN": ["china", "chinese", "beijing", "xi jinping", "китай*", "китая", "пекин*", "си цзиньпин*", "xitoy*"],
    "IRN": ["iran*", "tehran", "иран*", "тегеран*", "eron*"],
    "ISR": ["israel*", "netanyahu", "израил*", "нетаньяху", "isroil*"],
    "PSE": ["palestin*", "gaza", "west bank", "hamas", "палестин*", "сектор* газа", "хамас*", "falastin*", "g'azo*"],
    "TUR": ["turkey", "türkiye", "turkish", "ankara", "erdogan", "erdoğan", "турци*", "анкар*", "эрдоган*", "turkiya*"],
    "GBR": ["britain", "british", "london", "великобритани*", "лондон*", "britaniya*"],
    "DEU": ["german*", "berlin", "германи*", "берлин*", "germaniya*"],
    "FRA": ["france", "french", "paris", "macron", "франци*", "париж*", "макрон*", "fransiya*"],
    "IND": ["india", "indian", "delhi", "new delhi", "modi", "индия", "индии", "индию", "дели", "моди", "hindiston*"],
    "PAK": ["pakistan*", "islamabad", "пакистан*", "исламабад*"],
    "JPN": ["japan*", "tokyo", "япони*", "токио", "yaponiya*"],
    "KOR": ["south korea*", "seoul", "южн* коре*", "сеул*", "janubiy koreya*"],
    "PRK": ["north korea*", "pyongyang", "кндр", "пхеньян*", "shimoliy koreya*"],
    "SAU": ["saudi*", "riyadh", "саудовск*", "эр-рияд*", "saudiya*"],
    "SYR": ["syria*", "damascus", "сири*", "дамаск*", "suriya*"],
    "AZE": ["azerbaijan*", "baku", "aliyev", "азербайджан*", "баку", "алиев*", "ozarbayjon*"],
    "ARM": ["armenia*", "yerevan", "армени*", "ереван*", "armaniston*"],
    "GEO": ["tbilisi", "грузия", "грузии", "грузинск*", "тбилиси", "gruziya*"],
    "BLR": ["belarus*", "minsk", "lukashenko", "беларус*", "белорус*", "минск*", "лукашенко"],
    "POL": ["poland", "polish", "warsaw", "польш*", "варшав*", "polsha*"],
}
_SKIP_ENGLISH = {"Georgia"}  # also a US state


def _term(t: str) -> str:
    """Regex for one term: '*' at the end means prefix, otherwise a whole word."""
    t = t.strip().lower()
    prefix = t.endswith("*")
    t = t.rstrip("*")
    body = re.escape(t).replace(r"\*", r"\w*")
    return r"(?<![\w'])" + body + ("" if prefix else r"(?![\w'])")


def _load_countries() -> tuple[dict[str, dict], list[tuple[re.Pattern, str]]]:
    info, patterns = {}, []
    with open(HERE / "countries.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            info[r["iso3"]] = {"name": r["name"], "lat": float(r["lat"] or 0), "lon": float(r["lon"] or 0)}
            for n in {r["name"], r["name_long"]}:
                if n and n not in _SKIP_ENGLISH and len(n) > 3:
                    patterns.append((re.compile(_term(n)), r["iso3"]))
    for iso, names in EXTRA_NAMES.items():
        for n in names:
            patterns.append((re.compile(_term(n)), iso))
    return info, patterns


COUNTRY_INFO, _COUNTRY_PATTERNS = _load_countries()
_CAT_PATTERNS = [(slug, re.compile("|".join(_term(k) for k in kws))) for slug, (_, kws) in CATEGORIES.items()]


_APOS = str.maketrans({"’": "'", "‘": "'", "ʻ": "'", "ʼ": "'", "`": "'", "´": "'"})


def tag(title: str, summary: str) -> tuple[list[str], list[str]]:
    text = f" {title} {summary} ".lower().translate(_APOS)
    cats = [slug for slug, rx in _CAT_PATTERNS if rx.search(text)]
    countries = []
    for rx, iso in _COUNTRY_PATTERNS:
        if iso not in countries and iso in COUNTRY_INFO and rx.search(text):
            countries.append(iso)
    if "USA" not in countries and re.search(r"(?<![\w.])(US|U\.S\.|USA)(?![\w])", f"{title} {summary}"):
        countries.append("USA")
    if any(c in countries for c in ("UZB", "KAZ", "KGZ", "TJK", "TKM")):
        cats.append("central-asia")
    return cats, countries[:6]


# ------------------------------------------------------------------ grouping

_STOP = frozenset(
    """
the a an and or of to in on for with from by at as is are was were be been has have had
that this these those it its after over into about amid says said will would could new
more than not but who what when where how why his her their they them he she we you
и в во на с со по к ко из за от до о об что как это для при над под не но же ли бы
его ее их они он она мы вы был была были будет также после заявил заявила
va bilan uchun bu u ular ham esa yoki dan ga da ning ni
""".split()
)
_WORD = re.compile(r"[\w']+", re.UNICODE)


def title_tokens(title: str) -> set[str]:
    toks = set()
    for w in _WORD.findall(title.lower()):
        if len(w) < 3 or w in _STOP or w.isdigit():
            continue
        toks.add(w[:6])  # crude stem so "sanctions"/"sanctioned" and "санкции"/"санкций" meet
    return toks


def title_key(title: str) -> str:
    return " ".join(sorted(title_tokens(title)))


def cluster(articles: list[dict]) -> list[list[dict]]:
    """Greedy grouping of headlines that share most of their key words."""
    groups: list[tuple[set[str], list[dict]]] = []
    for a in sorted(articles, key=lambda x: x["published"], reverse=True):
        toks = title_tokens(a["title"])
        if not toks:
            groups.append((toks, [a]))
            continue
        best, best_score = None, 0.0
        for g in groups:
            shared = len(toks & g[0])
            if shared < 2:
                continue
            score = shared / min(len(toks), len(g[0]))
            if score > best_score:
                best, best_score = g, score
        joins = best is not None and ((best_score >= 0.5 and len(toks & best[0]) >= 3) or best_score >= 0.75)
        if joins:
            best[1].append(a)
            best[0].update(toks)
        else:
            groups.append((set(toks), [a]))
    out = [g[1] for g in groups]
    out.sort(key=lambda g: (len({a["publisher"] for a in g}), len(g), g[0]["published"]), reverse=True)
    return out


def overview(articles: list[dict]) -> dict:
    """The no-AI analysis: what is moving, where, and how widely it is reported."""
    cat_count, country_count, kind_count = Counter(), Counter(), Counter()
    for a in articles:
        cat_count.update(a["categories"])
        country_count.update(a["countries"])
        kind_count[a["kind"]] += 1
    groups = cluster(articles)
    return {
        "total": len(articles),
        "kinds": kind_count.most_common(),
        "categories": [(s, CATEGORIES.get(s, ("Markaziy Osiyo",))[0], n) for s, n in cat_count.most_common(10)],
        "countries": [(c, COUNTRY_INFO[c]["name"], n) for c, n in country_count.most_common(15) if c in COUNTRY_INFO],
        "stories": [g for g in groups if len({a["publisher"] for a in g}) >= 2][:15],
        "groups": groups,
    }


# ------------------------------------------------------------------ AI brief

LANG_NAMES = {"uz": "Uzbek (Latin script)", "ru": "Russian", "en": "English"}
MODEL = os.environ.get("WS_MODEL", "claude-haiku-5-5")

SYSTEM = """You are the analyst of WorldScope, a news intelligence tool. You receive a numbered
list of recent news items (headline, publisher, time, snippet). Write a concise intelligence
brief strictly from these items.

Rules:
- Cite every factual sentence with the item numbers in square brackets, e.g. [3] or [3][7].
  Use only numbers that exist in the list.
- Keep reported facts, claims made by a party, and your own analysis visibly apart. Mark
  analysis with "Tahlil:" / "Анализ:" / "Analysis:" in the brief's language.
- When a story rests on a single source, or on a party to the events (a government,
  an army, a Telegram channel), say so.
- Do not invent numbers, names or events that are not in the items.
- Structure (markdown): a one-paragraph summary; "## Asosiy voqealar" (or the equivalent
  heading in the brief's language) with 4-8 short bullet points ordered by importance; a
  section on causes and likely consequences; a short "what to watch" list.
- Write in {lang}. Keep it under 600 words."""


def build_items(groups: list[list[dict]], limit: int = 70) -> list[dict]:
    """Pick items for the model: one or two per story, widest-reported stories first."""
    picked: list[dict] = []
    for g in groups:
        seen_pub = set()
        for a in g:
            if a["publisher"] in seen_pub:
                continue
            seen_pub.add(a["publisher"])
            picked.append(a)
            if len(seen_pub) >= 2:
                break
        if len(picked) >= limit:
            break
    return picked[:limit]


class AIError(Exception):
    """A provider refused or failed; the message is shown to the user as is."""


def providers() -> list[str]:
    """Configured providers in the order they are tried. WS_PROVIDER puts one first."""
    have = [p for p, var in (("anthropic", "ANTHROPIC_API_KEY"), ("gemini", "GEMINI_API_KEY")) if os.environ.get(var)]
    first = os.environ.get("WS_PROVIDER", "").lower()
    if first in have:
        have.remove(first)
        have.insert(0, first)
    return have


def ai_available() -> bool:
    return bool(providers())


def model_label() -> str:
    names = {"anthropic": MODEL, "gemini": os.environ.get("WS_GEMINI_MODEL") or _gemini_cache.get("model") or "Gemini"}
    return " → ".join(names[p] for p in providers()) or "—"


def _anthropic(system: str, prompt: str) -> tuple[str, str]:
    import anthropic

    try:
        resp = anthropic.Anthropic().messages.create(
            model=MODEL, max_tokens=2000, system=system, messages=[{"role": "user", "content": prompt}]
        )
    except anthropic.APIStatusError as e:
        detail = str(getattr(e, "body", "") or e)
        if "credit balance" in detail:
            raise AIError("Claude: Anthropic hisobida kredit yetarli emas") from e
        if e.status_code == 401:
            raise AIError("Claude: kalit noto'g'ri yoki bekor qilingan") from e
        raise AIError(f"Claude: HTTP {e.status_code}") from e
    except anthropic.APIError as e:
        raise AIError(f"Claude: {type(e).__name__}") from e
    return "".join(b.text for b in resp.content if b.type == "text").strip(), str(resp.model)


GEMINI_API = "https://generativelanguage.googleapis.com/v1beta"
_gemini_cache: dict[str, str] = {}


def _gemini_version(name: str) -> tuple:
    m = re.search(r"gemini-(\d+)(?:\.(\d+))?", name)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def pick_gemini_model(models: list[dict]) -> str | None:
    ranked = rank_gemini_models(models)
    return ranked[0] if ranked else None


def rank_gemini_models(models: list[dict]) -> list[str]:
    """Text models, best first: newest stable Flash, then Flash-Lite, then the rest."""
    skip = ("image", "tts", "audio", "live", "embed", "vision", "thinking-exp", "learnlm", "gemma", "robotics")
    cands = []
    for m in models:
        name = m.get("name", "").removeprefix("models/")
        if "generateContent" not in m.get("supportedGenerationMethods", []) or not name.startswith("gemini-"):
            continue
        if any(k in name for k in skip):
            continue
        unstable = any(k in name for k in ("preview", "exp", "latest"))
        rank = (
            not unstable,
            "flash" in name and "lite" not in name,
            "flash" in name,
            _gemini_version(name),
            -len(name),
        )
        cands.append((rank, name))
    return [name for _, name in sorted(cands, reverse=True)]


def _gemini_models(client: httpx.Client, key: str) -> list[str]:
    if os.environ.get("WS_GEMINI_MODEL"):
        return [os.environ["WS_GEMINI_MODEL"]]
    if "models" not in _gemini_cache:
        r = client.get(f"{GEMINI_API}/models", params={"pageSize": 1000}, headers={"x-goog-api-key": key})
        if r.status_code >= 400:
            raise AIError(_gemini_error(r))
        ranked = rank_gemini_models(r.json().get("models", []))
        if not ranked:
            raise AIError("Gemini: matn yozadigan model topilmadi")
        _gemini_cache["models"] = ranked
        _gemini_cache["model"] = ranked[0]
    return _gemini_cache["models"]


def _gemini_error(r: httpx.Response) -> str:
    try:
        msg = r.json()["error"].get("message", "")
    except (ValueError, KeyError, TypeError, AttributeError):
        msg = ""
    if r.status_code == 429:
        return "Gemini: bepul limit tugadi, birozdan keyin urinib ko'ring"
    if r.status_code in (500, 503):
        return "Gemini: modellar hozir band, bir-ikki daqiqadan keyin qayta urinib ko'ring"
    if "API key not valid" in msg or r.status_code in (401, 403):
        return "Gemini: kalit noto'g'ri yoki bu API uchun ruxsat yo'q"
    return f"Gemini: HTTP {r.status_code} {msg[:120]}".strip()


def _gemini(system: str, prompt: str) -> tuple[str, str]:
    key = os.environ["GEMINI_API_KEY"]
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 8192, "temperature": 0.3},
    }
    last = "Gemini: javob yo'q"
    try:
        with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            # Busy (503/500) models get one short retry, then the next model in the ranking is tried.
            for model in _fallback_order(_gemini_models(client, key)):
                for attempt in range(2):
                    r = client.post(
                        f"{GEMINI_API}/models/{model}:generateContent", json=body, headers={"x-goog-api-key": key}
                    )
                    if r.status_code in (500, 503) and attempt == 0:
                        time.sleep(3)
                        continue
                    break
                if r.status_code < 400:
                    return _gemini_text(r.json()), model
                last = _gemini_error(r)
                if r.status_code not in (404, 500, 503):
                    break  # bad key or quota: another model will not help
    except httpx.HTTPError as e:
        raise AIError(f"Gemini: ulanib bo'lmadi ({type(e).__name__})") from e
    raise AIError(last)


def _fallback_order(ranked: list[str]) -> list[str]:
    """Best model, then the best Flash-Lite (lighter, usually less loaded), then the runner-up."""
    order = ranked[:1]
    lite = next((m for m in ranked if "lite" in m), None)
    for m in [lite, *ranked[1:]]:
        if m and m not in order:
            order.append(m)
        if len(order) == 3:
            break
    return order


def _gemini_text(data: dict) -> str:
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
    if not text.strip():
        reason = cand.get("finishReason") or (data.get("promptFeedback") or {}).get("blockReason") or "bo'sh javob"
        raise AIError(f"Gemini javob bermadi: {reason}")
    return text.strip()


PROVIDERS = {"anthropic": _anthropic, "gemini": _gemini}


def complete(system: str, prompt: str) -> tuple[str, str]:
    """Try each configured provider in turn; report every failure if all fail."""
    errors = []
    for name in providers():
        try:
            return PROVIDERS[name](system, prompt)
        except AIError as e:
            errors.append(str(e))
    raise AIError("; ".join(errors) or "AI kaliti o'rnatilmagan")


def write_brief(articles: list[dict], lang: str, focus: str = "") -> dict:
    items = build_items(cluster(articles))
    if not items:
        raise ValueError("Tahlil uchun yangilik yo'q. Avval manbalarni yangilang.")
    lines = []
    for n, a in enumerate(items, 1):
        snippet = f" — {a['summary'][:240]}" if a["summary"] else ""
        lines.append(f"[{n}] {a['title']} ({a['publisher']}, {a['published'][:16].replace('T', ' ')} UTC){snippet}")
    task = "Write the brief."
    if focus:
        task = f"Focus the brief on: {focus}. Ignore items unrelated to it; say so if few are relevant."
    body, model = complete(
        SYSTEM.format(lang=LANG_NAMES.get(lang, "English")),
        "News items:\n" + "\n".join(lines) + "\n\n" + task,
    )
    used = sorted({int(m) for m in re.findall(r"\[(\d+)\]", body) if 1 <= int(m) <= len(items)})
    body = re.sub(r"\[(\d+)\]", lambda m: m.group(0) if 1 <= int(m.group(1)) <= len(items) else "", body)
    cites = [
        {
            "n": n,
            "article_id": items[n - 1]["id"],
            "title": items[n - 1]["title"],
            "url": items[n - 1]["url"],
            "publisher": items[n - 1]["publisher"],
        }
        for n in used
    ]
    return {"body": body, "cites": cites, "model": model}


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)
