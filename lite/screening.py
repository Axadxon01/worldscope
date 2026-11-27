"""Partner screening for businesses: a list of counterparties checked against the OFAC, UN, EU and UK
sanctions lists, re-checked every week, with alerts when something new appears and a dated report
that a bank or partner can verify online.

A name match is a lead for a person to check, not a finding: every result says so. A match a person
has looked at and found to be someone else can be marked as such; it then no longer raises the status
or an alert, and the report shows who marked it and why.
"""

from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
import re
import secrets
from datetime import UTC, datetime

import analyze
import db
import sanctions

PLANS = {
    "free": {"label": "Bepul", "lists": 1, "items": 10, "auto": False, "reports": True},
    "business": {"label": "Biznes", "lists": 20, "items": 1000, "auto": True, "reports": True},
}
MATCH, REVIEW = 0.92, 0.75  # a hit's score: at or above MATCH is "match", from REVIEW "review"
TOP_HITS = 5
STATUS_UZ = {"new": "tekshirilmagan", "clear": "topilmadi", "review": "ko'rib chiqing", "match": "mos keldi"}
# words that say what kind of company it is, not which one: they would only add false hits or hide true ones
LEGAL = re.compile(
    r"\b(llc|ltd|limited|inc|corp|corporation|co|company|gmbh|ag|sa|srl|bv|plc|jsc|pjsc|ojsc|cjsc|ooo|oao|zao|pao|"
    r"mchj|xk|qk|aj|ak|ttk|ип|ооо|оао|зао|пао|ао|тоо|ао|llp|lp|fze|fzco|dmcc|sdn|bhd|pte|pvt|"
    r"mas'uliyati cheklangan jamiyati|aksiyadorlik jamiyati)\b\.?",
    re.I,
)


class ScreenError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def plan(user: dict | None) -> dict:
    return PLANS.get((user or {}).get("plan") or "free", PLANS["free"]) | {"key": (user or {}).get("plan") or "free"}


def clean_name(name: str) -> str:
    """'«Uzbek Trade» MCHJ' → 'Uzbek Trade': quotes, legal forms and extra spaces out."""
    n = re.sub(r"[\"'«»“”„`]+", " ", name or "")
    n = LEGAL.sub(" ", n)
    return " ".join(n.split()).strip(" ,.-")


# ------------------------------------------------------------------ reading what people upload

HEADER_WORDS = ("name", "nomi", "nom", "наимен", "название", "company", "kompaniya", "partner", "hamkor")


def _rows_from_table(rows: list[list[str]]) -> list[dict]:
    rows = [[(c or "").strip() for c in r] for r in rows if any((c or "").strip() for c in r)]
    if rows and any(w in rows[0][0].lower() for w in HEADER_WORDS):
        rows = rows[1:]
    out = []
    for r in rows:
        name = r[0][:200]
        if len(clean_name(name)) < 2:
            continue
        out.append(
            {"name": name, "country": (r[1] if len(r) > 1 else "")[:60], "note": (r[2] if len(r) > 2 else "")[:200]}
        )
    return out


def parse_text(text: str) -> list[dict]:
    """One partner per line: 'Name', 'Name; Country' or 'Name, Country, note'."""
    rows = []
    for line in (text or "").splitlines():
        parts = re.split(r"\s*[;\t|]\s*", line) if re.search(r"[;\t|]", line) else [line]
        rows.append(parts)
    return _rows_from_table(rows)


def parse_file(filename: str, content: bytes) -> list[dict]:
    """A CSV (comma, semicolon or tab), an Excel .xlsx (first sheet) or a plain list."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        try:
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception as e:
            raise ScreenError("Excel faylni o'qib bo'lmadi. CSV qilib saqlab, qayta yuklang.") from e
        ws = wb.worksheets[0]
        rows = [["" if v is None else str(v) for v in row[:3]] for row in ws.iter_rows(values_only=True, max_row=5000)]
        return _rows_from_table(rows)
    if name.endswith(".xls"):
        raise ScreenError("Eski .xls formati qo'llanmaydi: Excel'da .xlsx yoki .csv qilib saqlang.")
    text = ""
    for enc in ("utf-8-sig", "cp1251", "latin-1"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if name.endswith((".csv", ".tsv")) or (text.count("\n") and re.search(r"[,;\t]", text.splitlines()[0])):
        try:
            dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return _rows_from_table(list(csv.reader(io.StringIO(text), dialect)))
    return parse_text(text)


def resolve_country(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) == 3 and t.upper() in analyze.COUNTRY_INFO:
        return t.upper()
    for iso in analyze.COUNTRY_INFO:  # an Uzbek name: "Qozog'iston"
        if analyze.display_name(iso).lower() == t.lower():
            return iso
    return sanctions.country_iso(t) or ""


# ------------------------------------------------------------------ lists


def lists_of(user_id: int) -> list[dict]:
    rows = db.rows("SELECT * FROM screen_lists WHERE user_id = ? ORDER BY id", (user_id,))
    for r in rows:
        counts = db.rows("SELECT status, COUNT(*) AS n FROM screen_items WHERE list_id = ? GROUP BY status", (r["id"],))
        r["counts"] = {c["status"]: c["n"] for c in counts}
        r["total"] = sum(r["counts"].values())
    return rows


def get_list(list_id: int, user_id: int) -> dict:
    row = db.one("SELECT * FROM screen_lists WHERE id = ? AND user_id = ?", (list_id, user_id))
    if not row:
        raise ScreenError("Ro'yxat topilmadi")
    return row


def items(list_id: int) -> list[dict]:
    rows = db.rows("SELECT * FROM screen_items WHERE list_id = ? ORDER BY "
                   "CASE status WHEN 'match' THEN 0 WHEN 'review' THEN 1 WHEN 'new' THEN 2 ELSE 3 END, name",
                   (list_id,))  # fmt: skip
    for r in rows:
        r["hits"] = json.loads(r["hits"] or "[]")
        r["dismissed"] = json.loads(r["dismissed"] or "{}")
    return rows


def create_list(user: dict, name: str) -> int:
    p = plan(user)
    n = db.one("SELECT COUNT(*) AS n FROM screen_lists WHERE user_id = ?", (user["id"],))["n"]
    if n >= p["lists"]:
        raise ScreenError(
            f"{p['label']} tarifida ko'pi bilan {p['lists']} ta ro'yxat. Ko'proq kerak bo'lsa — Biznes tarifi."
        )
    name = " ".join((name or "").split())[:80] or "Hamkorlarim"
    with db.tx() as c:
        row = c.execute(
            "INSERT INTO screen_lists (user_id, name, created, auto) VALUES (?,?,?,?) RETURNING id",
            (user["id"], name, _now(), 1 if p["auto"] else 0),
        ).fetchone()
    return row["id"]


def add_items(user: dict, list_id: int, rows: list[dict]) -> int:
    get_list(list_id, user["id"])
    p = plan(user)
    have = db.one("SELECT COUNT(*) AS n FROM screen_items WHERE list_id = ?", (list_id,))["n"]
    known = {r["name"].lower() for r in db.rows("SELECT name FROM screen_items WHERE list_id = ?", (list_id,))}
    fresh = []
    for r in rows:
        if r["name"].lower() in known:
            continue
        known.add(r["name"].lower())
        fresh.append(r)
    room = p["items"] - have
    if fresh and room <= 0:
        raise ScreenError(f"{p['label']} tarifida bitta ro'yxatda ko'pi bilan {p['items']} ta nom.")
    fresh = fresh[:room]
    with db.tx() as c:
        for r in fresh:
            c.execute(
                "INSERT INTO screen_items (list_id, name, country, note) VALUES (?,?,?,?)",
                (list_id, r["name"], resolve_country(r.get("country", "")), r.get("note", "")),
            )
    return len(fresh)


def delete_list(user_id: int, list_id: int) -> None:
    get_list(list_id, user_id)
    with db.tx() as c:
        c.execute("DELETE FROM screen_items WHERE list_id = ?", (list_id,))
        c.execute("DELETE FROM screen_lists WHERE id = ?", (list_id,))


def delete_item(user_id: int, item_id: int) -> int:
    row = db.one("SELECT i.list_id FROM screen_items i JOIN screen_lists l ON l.id = i.list_id "
                 "WHERE i.id = ? AND l.user_id = ?", (item_id, user_id))  # fmt: skip
    if not row:
        raise ScreenError("Topilmadi")
    with db.tx() as c:
        c.execute("DELETE FROM screen_items WHERE id = ?", (item_id,))
    return row["list_id"]


# ------------------------------------------------------------------ checking


def hit_key(h: dict) -> str:
    return f"{h['list']}:{h['uid']}"


def score(query: str, candidate: str) -> float:
    """How alike two names are, legal forms aside: the same words in any order = 1; a name that only
    shares one word of a longer one ("Sberbank" / "Sberbank Insurance") stays below a match."""
    qn, cn = sanctions.norm(clean_name(query)), sanctions.norm(clean_name(candidate))
    if not qn or not cn:
        return 0.0
    qs, cs = set(qn.split()), set(cn.split())
    if qn == cn or qs == cs:
        return 1.0
    inter = len(qs & cs)
    words = 0.5 * inter / len(qs) + 0.5 * inter / len(qs | cs)
    return round(max(difflib.SequenceMatcher(None, qn, cn).ratio(), words), 2)


def check_name(name: str, country: str = "") -> list[dict]:
    """The best list entries for a partner, scored; the partner's own country lifts an exact tie."""
    q = clean_name(name) or name
    found = sanctions.search(q, limit=40)
    hits = []
    for h in found:
        h["score"] = max(score(q, n) for n in [h["name"], *h["aliases"]])
        if h["score"] < REVIEW:
            continue
        same_country = bool(country) and country in h["countries"]
        hits.append({
            "list": h["list"], "uid": h["uid"], "name": h["name"], "kind": h["kind"], "score": h["score"],
            "countries": h["countries"], "programs": h["programs"][:3], "listed": h["listed"],
            "aliases": h["aliases"][:3], "same_country": same_country,
        })  # fmt: skip
    hits.sort(key=lambda h: (-h["score"], not h["same_country"]))
    return hits[:TOP_HITS]


def status_of(hits: list[dict], dismissed: dict) -> str:
    live = [h for h in hits if hit_key(h) not in dismissed]
    if any(h["score"] >= MATCH for h in live):
        return "match"
    if live:
        return "review"
    return "clear"


def run_list(list_id: int) -> dict:
    """Check every name of one list; returns how many and which names have a match they did not have."""
    rows = items(list_id)
    new_alerts = []
    now = _now()
    for r in rows:
        hits = check_name(r["name"], r["country"])
        before = {hit_key(h) for h in r["hits"]}
        status = status_of(hits, r["dismissed"])
        fresh = [h for h in hits if hit_key(h) not in before and hit_key(h) not in r["dismissed"]]
        if r["status"] != "new" and fresh and status in ("match", "review"):
            new_alerts.append({"name": r["name"], "status": status, "hit": fresh[0]})
        with db.tx() as c:
            c.execute("UPDATE screen_items SET hits = ?, status = ?, checked = ? WHERE id = ?",
                      (json.dumps(hits, ensure_ascii=False), status, now, r["id"]))  # fmt: skip
    with db.tx() as c:
        c.execute("UPDATE screen_lists SET checked = ? WHERE id = ?", (now, list_id))
    return {"checked": len(rows), "new": new_alerts}


def dismiss(user: dict, item_id: int, key: str, why: str) -> int:
    row = db.one("SELECT i.* FROM screen_items i JOIN screen_lists l ON l.id = i.list_id "
                 "WHERE i.id = ? AND l.user_id = ?", (item_id, user["id"]))  # fmt: skip
    if not row:
        raise ScreenError("Topilmadi")
    dismissed = json.loads(row["dismissed"] or "{}")
    hits = json.loads(row["hits"] or "[]")
    if key in dismissed:
        dismissed.pop(key)  # undo
    elif any(hit_key(h) == key for h in hits):
        dismissed[key] = f"{user['username']}, {_now()[:10]}: {(why or 'boshqa shaxs/tashkilot').strip()[:160]}"
    with db.tx() as c:
        c.execute("UPDATE screen_items SET dismissed = ?, status = ? WHERE id = ?",
                  (json.dumps(dismissed, ensure_ascii=False), status_of(hits, dismissed), item_id))  # fmt: skip
    return row["list_id"]


def due_lists() -> list[dict]:
    """Lists to re-check this week: those of business accounts with automatic checks on."""
    return db.rows(
        "SELECT l.id, l.user_id, l.name FROM screen_lists l JOIN users u ON u.id = l.user_id "
        "WHERE l.auto = 1 AND u.plan = 'business' ORDER BY l.id"
    )


# ------------------------------------------------------------------ the report and its check


def make_report(user: dict, list_id: int) -> str:
    lst = get_list(list_id, user["id"])
    rows = items(list_id)
    if not rows or any(r["status"] == "new" for r in rows):
        raise ScreenError("Avval ro'yxatni tekshiring: hisobot faqat tekshirilgan nomlar bo'yicha tuziladi.")
    status = sanctions.status()
    data = {
        "list": lst["name"], "owner": user["username"], "issued": _now(), "checked": lst["checked"],
        "sources": {k: {"count": v.get("count"), "updated": v.get("updated")} for k, v in status.items()
                    if k in sanctions.LISTS},
        "counts": {s: sum(r["status"] == s for r in rows) for s in ("match", "review", "clear")},
        "items": [{"name": r["name"], "country": r["country"], "status": r["status"],
                   "hits": [{k: h[k] for k in ("list", "uid", "name", "score", "countries", "listed")}
                            | {"dismissed": r["dismissed"].get(hit_key(h), "")} for h in r["hits"]]}
                  for r in rows],
    }  # fmt: skip
    body = json.dumps(data, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha256(body.encode()).hexdigest()
    code = secrets.token_hex(6).upper()
    with db.tx() as c:
        c.execute("INSERT INTO screen_reports (code, list_id, user_id, created, data, digest) VALUES (?,?,?,?,?,?)",
                  (code, list_id, user["id"], data["issued"], body, digest))  # fmt: skip
    return code


def report(code: str) -> dict | None:
    row = db.one("SELECT * FROM screen_reports WHERE code = ?", ((code or "").upper()[:20],))
    if not row:
        return None
    row["data"] = json.loads(row["data"])
    row["valid"] = (
        hashlib.sha256(json.dumps(row["data"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        == row["digest"]
    )
    return row


def results_csv(list_id: int) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Nomi", "Davlat", "Natija", "Eng yaqin yozuv", "Ro'yxat", "Moslik", "Ko'rib chiqilgan", "Tekshirilgan"])
    for r in items(list_id):
        h = r["hits"][0] if r["hits"] else {}
        w.writerow([
            r["name"], r["country"], STATUS_UZ.get(r["status"], r["status"]), h.get("name", ""), h.get("list", ""),
            f"{round(100 * h['score'])}%" if h else "", r["dismissed"].get(hit_key(h), "") if h else "",
            (r["checked"] or "")[:16],
        ])  # fmt: skip
    return out.getvalue()
