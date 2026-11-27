"""Official sanctions lists (US OFAC, UN Security Council, EU, UK) in one searchable table.

Each list is downloaded from its publisher, read one row at a time and stored as one record per
listed person, company, vessel or aircraft: name, other names, the countries in its addresses and
nationalities, the sanctions programmes and, where the list gives it, the date it was listed.
A list that fails to download keeps its previous records. Per-country counts are computed once per
refresh and cached; a name search runs on a normalised copy of every name.

A name match is a lead to check against the official list, never a legal finding.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import csv
import difflib
import json
import logging
import math
import re
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

import analyze
import collectors
import db
import debt
import pairs

log = logging.getLogger("worldscope.sanctions")

LISTS = {
    "OFAC": {
        "label": "AQSH (OFAC SDN)",
        "files": {
            "sdn": "https://www.treasury.gov/ofac/downloads/sdn.csv",
            "add": "https://www.treasury.gov/ofac/downloads/add.csv",
            "alt": "https://www.treasury.gov/ofac/downloads/alt.csv",
        },
        "official": "https://sanctionssearch.ofac.treas.gov/",
    },
    "UN": {
        "label": "BMT Xavfsizlik Kengashi",
        "files": {"xml": "https://scsanctions.un.org/resources/xml/en/consolidated.xml"},
        "official": "https://main.un.org/securitycouncil/en/content/un-sc-consolidated-list",
    },
    "EU": {
        "label": "Yevropa Ittifoqi",
        "files": {
            # The EU publishes the file behind a fixed public token: base64 of "token-2017".
            "csv": "https://webgate.ec.europa.eu/fsd/fsf/public/files/csvFullSanctionsList_1_1/content?token="
            + base64.b64encode(b"token-2017").decode().rstrip("=")
        },
        "official": "https://www.sanctionsmap.eu/",
    },
    "UK": {
        "label": "Buyuk Britaniya",
        "files": {"csv": "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv"},
        "official": "https://www.gov.uk/government/publications/the-uk-sanctions-list",
    },
}
KINDS = {"person": "Shaxs", "entity": "Tashkilot", "vessel": "Kema", "aircraft": "Samolyot"}
DOWNLOAD_TIMEOUT = httpx.Timeout(180, connect=20)
STATS_KEY = "sanctions:stats"
# Uzbekistan's main trading partners, for the exporters' box.
UZ_PARTNERS = ["RUS", "CHN", "KAZ", "TUR", "KGZ", "TJK", "AFG", "TKM", "IRN", "BLR", "ARE", "IND", "KOR", "DEU"]

# ------------------------------------------------------------------ names and countries

_CYR = dict(
    zip(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюяўқғҳ",
        "a b v g d e yo j z i y k l m n o p r s t u f x ts ch sh sh _ y _ e yu ya o q g h".split(),
        strict=True,
    )
)


def norm(text: str) -> str:
    """Lower case, Latin letters only, single spaces: 'Ўзбекистон, АЖ' → 'ozbekiston aj'."""
    s = (text or "").lower()
    s = "".join(_CYR.get(ch, ch) for ch in s).replace("_", "")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_COUNTRY_ALIASES = {
    "russian federation": "RUS", "russia": "RUS", "crimea": "UKR", "ukraine": "UKR",
    "iran islamic republic of": "IRN", "iran": "IRN", "korea north": "PRK", "north korea": "PRK",
    "democratic people s republic of korea": "PRK", "dprk": "PRK", "korea democratic people s republic of": "PRK",
    "korea south": "KOR", "south korea": "KOR", "republic of korea": "KOR", "korea republic of": "KOR",
    "syrian arab republic": "SYR", "syria": "SYR", "united states": "USA", "united states of america": "USA",
    "usa": "USA", "united kingdom": "GBR", "uk": "GBR", "great britain": "GBR", "turkey": "TUR", "turkiye": "TUR",
    "venezuela bolivarian republic of": "VEN", "congo democratic republic of the": "COD",
    "democratic republic of the congo": "COD", "congo democratic republic": "COD", "drc": "COD",
    "congo": "COG", "republic of the congo": "COG", "cote d ivoire": "CIV", "ivory coast": "CIV",
    "viet nam": "VNM", "vietnam": "VNM", "lao people s democratic republic": "LAO", "laos": "LAO",
    "bolivia plurinational state of": "BOL", "tanzania united republic of": "TZA", "tanzania": "TZA",
    "moldova republic of": "MDA", "moldova": "MDA", "czech republic": "CZE", "czechia": "CZE",
    "burma": "MMR", "myanmar": "MMR", "kyrgyz republic": "KGZ", "kyrgyzstan": "KGZ",
    "united arab emirates": "ARE", "uae": "ARE", "palestinian": "PSE", "west bank": "PSE", "gaza": "PSE",
    "palestine": "PSE", "state of palestine": "PSE", "occupied palestinian territory": "PSE",
    "taiwan": "TWN", "taiwan province of china": "TWN", "chinese taipei": "TWN",
    "holy see": "VAT", "vatican": "VAT", "macedonia": "MKD", "north macedonia": "MKD",
    "the former yugoslav republic of macedonia": "MKD", "bosnia and herzegovina": "BIH", "bosnia": "BIH",
    "gambia the": "GMB", "the gambia": "GMB", "bahamas the": "BHS", "the bahamas": "BHS", "cabo verde": "CPV",
    "eswatini": "SWZ", "swaziland": "SWZ", "timor leste": "TLS", "east timor": "TLS", "brunei darussalam": "BRN",
    "micronesia federated states of": "FSM", "sao tome and principe": "STP", "south sudan": "SSD",
    "sudan": "SDN", "libyan arab jamahiriya": "LBY", "libya": "LBY", "uzbekistan": "UZB", "ozbekiston": "UZB",
    "belarus": "BLR", "byelorussia": "BLR", "kazakhstan": "KAZ", "tajikistan": "TJK", "turkmenistan": "TKM",
    "afghanistan": "AFG", "china": "CHN", "people s republic of china": "CHN", "hong kong": "CHN", "macau": "CHN",
    "netherlands the": "NLD", "the netherlands": "NLD", "slovak republic": "SVK", "slovakia": "SVK",
    "saint kitts and nevis": "KNA", "saint vincent and the grenadines": "VCT", "saint lucia": "LCA",
}  # fmt: skip


def _country_index() -> dict[str, str]:
    idx: dict[str, str] = {}
    with open(analyze.HERE / "countries.csv", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for n in (r["name"], r["name_long"]):
                if n:
                    idx.setdefault(norm(n), r["iso3"])
    idx.update(_COUNTRY_ALIASES)
    return {k: v for k, v in idx.items() if pairs.valid(v)}


COUNTRY_INDEX = _country_index()
_BY_LENGTH = sorted(COUNTRY_INDEX, key=len, reverse=True)
ISO2_TO_3 = {v: k for k, v in debt.ISO2.items()}


def country_iso(text: str) -> str | None:
    """'Russian Federation', 'Moscow, Russia', 'Crimea region of Ukraine' → ISO3 of a sovereign state."""
    n = norm(text)
    if not n or n in ("0", "unknown"):
        return None
    if n in COUNTRY_INDEX:
        return COUNTRY_INDEX[n]
    padded = f" {n} "
    for name in _BY_LENGTH:  # longest first, so "south sudan" wins over "sudan"
        if len(name) > 3 and f" {name} " in padded:
            return COUNTRY_INDEX[name]
    return None


def _isos(texts) -> set[str]:
    return {iso for t in texts if (iso := country_iso(t))}


def _record(lst, uid, kind, name, aliases=(), countries=(), programs=(), listed="") -> dict:
    name = re.sub(r"\s+", " ", name or "").strip()
    others = sorted({re.sub(r"\s+", " ", a).strip() for a in aliases if a and a.strip()} - {name})[:30]
    return {
        "list": lst,
        "uid": str(uid),
        "kind": kind,
        "name": name[:300],
        "aliases": "; ".join(others)[:2000],
        "countries": " " + " ".join(sorted(countries)) + " " if countries else "",
        "programs": "; ".join(sorted({p.strip() for p in programs if p and p.strip()}))[:300],
        "listed": listed or "",
        "search": " " + " | ".join(norm(x) for x in [name, *others] if norm(x)) + " ",
    }


def _blank(v: str | None) -> str:
    v = (v or "").strip()
    return "" if v == "-0-" else v


# ------------------------------------------------------------------ parsers (one per list)


_NATIONALITY = re.compile(r"(?:nationality|citizen|citizenship)\s+([A-Za-z ,.'()-]+?)[;.]", re.I)


def parse_ofac(sdn: Path, add: Path | None = None, alt: Path | None = None) -> list[dict]:
    """sdn.csv (no header): ent_num, name, type, program, title, call sign, vessel type, tonnage,
    GRT, vessel flag, vessel owner, remarks. add.csv gives addresses, alt.csv other names."""
    countries: dict[str, set[str]] = defaultdict(set)
    if add and add.exists():
        with open(add, encoding="latin-1", newline="") as f:
            for row in csv.reader(f):
                if len(row) >= 5 and (iso := country_iso(_blank(row[4]))):
                    countries[row[0]].add(iso)
    aliases: dict[str, list[str]] = defaultdict(list)
    if alt and alt.exists():
        with open(alt, encoding="latin-1", newline="") as f:
            for row in csv.reader(f):
                if len(row) >= 4 and _blank(row[3]):
                    aliases[row[0]].append(row[3])
    out = []
    with open(sdn, encoding="latin-1", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 12 or not row[0].strip().isdigit():
                continue
            uid, name, typ, program = row[0].strip(), _blank(row[1]), _blank(row[2]).lower(), _blank(row[3])
            remarks = _blank(row[11])
            kind = typ if typ in ("individual", "vessel", "aircraft") else "entity"
            kind = "person" if kind == "individual" else kind
            isos = set(countries.get(uid, ()))
            isos |= _isos(m.group(1) for m in _NATIONALITY.finditer(remarks))
            if (flag := country_iso(_blank(row[9]))) and kind == "vessel":
                isos.add(flag)
            progs = [p.strip(" []") for p in program.split("] [")]
            out.append(_record("OFAC", uid, kind, name, aliases.get(uid, ()), isos, progs))
    return out


def _texts(el, path: str) -> list[str]:
    return [(e.text or "").strip() for e in el.findall(path) if (e.text or "").strip()]


def parse_un(path: Path) -> list[dict]:
    out = []
    root = ET.parse(path).getroot()
    for kind, tag, alias_tag, addr_tag in (
        ("person", "INDIVIDUALS/INDIVIDUAL", "INDIVIDUAL_ALIAS/ALIAS_NAME", "INDIVIDUAL_ADDRESS/COUNTRY"),
        ("entity", "ENTITIES/ENTITY", "ENTITY_ALIAS/ALIAS_NAME", "ENTITY_ADDRESS/COUNTRY"),
    ):
        for el in root.findall(tag):
            parts = [(el.findtext(t) or "").strip() for t in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")]
            name = " ".join(p for p in parts if p)
            texts = _texts(el, addr_tag) + _texts(el, "NATIONALITY/VALUE")
            out.append(
                _record(
                    "UN",
                    (el.findtext("REFERENCE_NUMBER") or el.findtext("DATAID") or "").strip(),
                    kind,
                    name,
                    _texts(el, alias_tag),
                    _isos(texts),
                    [(el.findtext("UN_LIST_TYPE") or "").strip()],
                    (el.findtext("LISTED_ON") or "")[:10],
                )
            )
    return out


def parse_eu(path: Path) -> list[dict]:
    """One row per name, address, … of an entry; rows of one entry share Entity_LogicalId."""
    groups: dict[str, dict] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            uid = r.get("Entity_LogicalId") or ""
            if not uid:
                continue
            g = groups.setdefault(uid, {"names": [], "iso": set(), "prog": set(), "kind": "", "date": ""})
            if (n := (r.get("NameAlias_WholeName") or "").strip()) and n not in g["names"]:
                g["names"].append(n)
            for col in ("Address_CountryIso2Code", "Citizenship_CountryIso2Code"):
                if iso := ISO2_TO_3.get((r.get(col) or "").strip().upper()):
                    g["iso"].add(iso)
            if p := (r.get("Entity_Regulation_Programme") or "").strip():
                g["prog"].add(p)
            code = (r.get("Entity_SubjectType_ClassificationCode") or r.get("Entity_SubjectType") or "").lower()
            g["kind"] = g["kind"] or ("person" if code.startswith("p") else "entity")
            g["date"] = g["date"] or (r.get("Entity_DesignationDate") or "")[:10]
    return [
        _record("EU", uid, g["kind"], g["names"][0], g["names"][1:], g["iso"], g["prog"], g["date"])
        for uid, g in groups.items()
        if g["names"]
    ]


_REGIME = re.compile(r"^The (.+?) \(Sanctions\)", re.I)


def _uk_date(v: str) -> str:
    try:
        return datetime.strptime(v.strip(), "%d/%m/%Y").date().isoformat()
    except ValueError:
        return ""


def parse_uk(path: Path) -> list[dict]:
    """First line is "Report Date: …"; then one row per name of an entry, keyed by Unique ID."""
    groups: dict[str, dict] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        first = f.readline()
        if not first.lower().startswith("report date"):
            f.seek(0)
        for r in csv.DictReader(f):
            uid = (r.get("Unique ID") or "").strip()
            if not uid:
                continue
            g = groups.setdefault(
                uid, {"primary": "", "names": [], "iso": set(), "prog": set(), "kind": "", "date": ""}
            )
            parts = [(r.get(f"Name {i}") or "").strip() for i in (1, 2, 3, 4, 5, 6)]
            name = " ".join(p for p in parts if p)
            if name:
                if (r.get("Name type") or "").lower().startswith("primary") and not g["primary"]:
                    g["primary"] = name
                elif name not in g["names"]:
                    g["names"].append(name)
            texts = [r.get("Address Country") or ""]
            texts += re.split(r"\(\d+\)|,", r.get("Nationality(/ies)") or "")
            g["iso"] |= _isos(texts)
            regime = (r.get("Regime Name") or "").strip()
            if regime:
                m = _REGIME.match(regime)
                g["prog"].add(m.group(1) if m else regime[:60])
            t = (r.get("Designation Type") or "").lower()
            g["kind"] = g["kind"] or {"individual": "person", "ship": "vessel"}.get(t, "entity")
            g["date"] = g["date"] or _uk_date(r.get("Date Designated") or "")
    out = []
    for uid, g in groups.items():
        name = g["primary"] or (g["names"][0] if g["names"] else "")
        if name:
            out.append(_record("UK", uid, g["kind"], name, g["names"], g["iso"], g["prog"], g["date"]))
    return out


# ------------------------------------------------------------------ download and store


async def _download(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    async with client.stream("GET", url, follow_redirects=True) as r:
        if r.status_code >= 400:
            raise collectors.CollectError(f"HTTP {r.status_code}")
        with open(dest, "wb") as f:
            async for chunk in r.aiter_bytes(1 << 16):
                f.write(chunk)


def _parse(lst: str, files: dict[str, Path]) -> list[dict]:
    if lst == "OFAC":
        return parse_ofac(files["sdn"], files.get("add"), files.get("alt"))
    if lst == "UN":
        return parse_un(files["xml"])
    if lst == "EU":
        return parse_eu(files["csv"])
    return parse_uk(files["csv"])


COLUMNS = ("list", "uid", "kind", "name", "aliases", "countries", "programs", "listed", "search")


def store(lst: str, records: list[dict]) -> int:
    """Replace one list's records; drop duplicates of an id the publisher repeats."""
    unique = {r["uid"]: r for r in records}
    with db.tx() as c:
        c.execute("DELETE FROM sanctions WHERE list = ?", (lst,))
        c.executemany(
            f"INSERT INTO sanctions ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
            [tuple(r[k] for k in COLUMNS) for r in unique.values()],
        )
        c.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (f"sanctions:{lst}", json.dumps({"updated": collectors.now_iso(), "count": len(unique)})),
        )
    return len(unique)


async def refresh(only: list[str] | None = None) -> dict:
    """Download and store every list (or the given ones). One failing list does not stop the rest."""
    result: dict[str, int | str] = {}
    async with httpx.AsyncClient(headers={"User-Agent": collectors.UA}, timeout=DOWNLOAD_TIMEOUT) as client:
        for lst, spec in LISTS.items():
            if only and lst not in only:
                continue
            with tempfile.TemporaryDirectory() as tmp:
                try:
                    files = {}
                    for part, url in spec["files"].items():
                        files[part] = Path(tmp) / part
                        await _download(client, url, files[part])
                    records = await asyncio.to_thread(_parse, lst, files)
                    if len(records) < 50:  # a format change or an error page; keep what we had
                        raise collectors.CollectError(f"faqat {len(records)} ta yozuv o'qildi")
                    result[lst] = await asyncio.to_thread(store, lst, records)
                except Exception as e:
                    log.warning("sanctions %s failed: %s", lst, e)
                    result[lst] = f"xato: {type(e).__name__}: {str(e)[:120]}"
                    _note_error(lst, result[lst])
    await asyncio.to_thread(compute_stats)
    return result


def _note_error(lst: str, error: str) -> None:
    info = status().get(lst, {})
    info.update({"error": error, "tried": collectors.now_iso()})
    with db.tx() as c:
        c.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (f"sanctions:{lst}", json.dumps(info)),
        )


def status() -> dict[str, dict]:
    out = {}
    for r in db.rows("SELECT key, value FROM meta WHERE key LIKE ?", ("sanctions:%",)):
        with contextlib.suppress(ValueError):
            out[r["key"].split(":", 1)[1]] = json.loads(r["value"])
    return out


def loaded() -> bool:
    return bool(db.one("SELECT 1 AS x FROM sanctions LIMIT 1"))


def stale(days: float = 8) -> bool:
    st = status()
    if not st or any(lst not in st or "updated" not in st[lst] for lst in LISTS):
        return True
    oldest = min(datetime.fromisoformat(v["updated"]) for v in st.values() if "updated" in v)
    return (datetime.now(UTC) - oldest).total_seconds() > days * 86400


# ------------------------------------------------------------------ statistics


def compute_stats() -> dict:
    per: dict[str, dict] = {}
    for r in db.rows("SELECT list, kind, countries, programs FROM sanctions WHERE countries <> ''"):
        for iso in r["countries"].split():
            s = per.setdefault(iso, {"total": 0, "lists": Counter(), "kinds": Counter(), "programs": Counter()})
            s["total"] += 1
            s["lists"][r["list"]] += 1
            s["kinds"][r["kind"]] += 1
            for p in r["programs"].split("; "):
                if p:
                    s["programs"][f"{r['list']}: {p}"] += 1
    stats = {
        iso: {
            "total": s["total"],
            "lists": dict(s["lists"]),
            "kinds": dict(s["kinds"]),
            "programs": s["programs"].most_common(6),
        }
        for iso, s in per.items()
    }
    db.cache_put(STATS_KEY, stats)
    return stats


def stats() -> dict:
    return db.cache_get(STATS_KEY, 3650) or {}


def risk_points(n: int) -> float:
    """Entries tied to a country → 0..100 on a log scale: 10 → 26, 100 → 50, 1000 → 75, 10 000 → 100.

    It measures how many listed counterparties sit in or come from the country (a trading partner's
    risk), not whether the country itself is under sanctions."""
    return round(min(100.0, 25 * math.log10(1 + max(0, n))), 1)


def totals() -> dict:
    by = {r["list"]: r["n"] for r in db.rows("SELECT list, COUNT(*) AS n FROM sanctions GROUP BY list")}
    return {"lists": by, "all": sum(by.values())}


# ------------------------------------------------------------------ search and listing


def _row(r: dict) -> dict:
    r = dict(r)
    r["countries"] = r["countries"].split()
    r["aliases"] = [a for a in r["aliases"].split("; ") if a]
    r["programs"] = [p for p in r["programs"].split("; ") if p]
    r.pop("search", None)
    return r


def _similarity(q: str, cand: str) -> float:
    if not cand:
        return 0.0
    if q == cand:
        return 1.0
    qs, cs = set(q.split()), set(cand.split())
    overlap = len(qs & cs) / max(len(qs), 1)  # word order and extra middle names do not matter
    return max(difflib.SequenceMatcher(None, q, cand).ratio(), 0.6 * overlap + 0.4 * (overlap == 1))


def search(q: str, country: str = "", lst: str = "", limit: int = 40) -> list[dict]:
    """Names (and other names) that contain every word of the query, best match first."""
    nq = norm(q)
    words = [w for w in nq.split() if len(w) >= 2][:6]
    if not words:
        return []

    def query(terms: list[str]) -> list[dict]:
        sql = "SELECT * FROM sanctions WHERE 1=1"
        params: list = []
        for t in terms:
            sql += " AND search LIKE ?"
            params.append(f"%{t}%")
        if country:
            sql += " AND countries LIKE ?"
            params.append(f"% {country} %")
        if lst in LISTS:
            sql += " AND list = ?"
            params.append(lst)
        return db.rows(sql + " LIMIT 400", params)

    found = query(words)
    loose = False
    if not found:  # spelling differs (Mohammad / Muhammad): match on word stems instead
        stems = [w[:4] for w in words if len(w) >= 4] or words
        found, loose = query(stems), True
    out = []
    for r in found:
        names = [n for n in r["search"].split(" | ") if n.strip()]
        best = max((_similarity(nq, n.strip()) for n in names), default=0.0)
        rec = _row(r)
        rec["score"] = round(best * (0.85 if loose else 1.0), 2)
        out.append(rec)
    out.sort(key=lambda x: (-x["score"], x["name"]))
    return out[:limit]


def recent(limit: int = 20) -> list[dict]:
    """The latest additions, by the date the publisher gives (not every list has one)."""
    rows = db.rows(
        "SELECT * FROM sanctions WHERE listed != '' AND listed <= ? ORDER BY listed DESC, id DESC LIMIT ?",
        (datetime.now(UTC).date().isoformat(), limit),
    )
    return [_row(r) for r in rows]


def match_label(score: float) -> str:
    if score >= 0.95:
        return "to'liq mos"
    if score >= 0.8:
        return "kuchli o'xshash"
    return "qisman o'xshash"


def for_country(iso: str, lst: str = "", kind: str = "", limit: int = 200) -> list[dict]:
    sql = "SELECT * FROM sanctions WHERE countries LIKE ?"
    params: list = [f"% {iso} %"]
    if lst in LISTS:
        sql += " AND list = ?"
        params.append(lst)
    if kind in KINDS:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY listed DESC, name LIMIT ?"
    params.append(limit)
    return [_row(r) for r in db.rows(sql, params)]


def by_year(iso: str) -> list[tuple[str, int]]:
    """How many of a country's entries were listed each year (UN, EU and UK give dates; OFAC does not)."""
    c = Counter()
    for r in db.rows("SELECT listed FROM sanctions WHERE countries LIKE ? AND listed <> ''", (f"% {iso} %",)):
        c[r["listed"][:4]] += 1
    return sorted(c.items())[-12:]


_SANCTION_WORDS = ("sanction", "санкц", "sanksiya", "embargo", "эмбарго")


def news(iso: str, hours: int = 168, limit: int = 15) -> list[dict]:
    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    where = " OR ".join(["title LIKE ?"] * len(_SANCTION_WORDS))
    sql = (
        f"SELECT * FROM articles WHERE published >= ? AND countries LIKE ? AND ({where}) "
        "ORDER BY published DESC LIMIT ?"
    )
    return db.rows(sql, [since, f'%"{iso}"%', *(f"%{w}%" for w in _SANCTION_WORDS), limit])
