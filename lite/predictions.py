"""Forecast ledger: every scenario a pair analysis gives is written down with its chance and the
date by which it should be visible, and checked against the news once that date has passed.

The check asks the AI one narrow question with the news since the forecast was made: did this
happen, partly happen, not happen, or is it still unclear? Its answer must cite the news items.
The scores page then shows how often the forecasts came true and how well their stated chances
matched reality (the Brier score: 0 is perfect, 0.25 is what always saying 50% would get).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import analyze
import db

WORD_CHANCE = {"high": 60, "medium": 30, "low": 10}
OUTCOMES = {"happened": 1.0, "partly": 0.5, "not_happened": 0.0}
STATUS_UZ = {
    "open": "kutilmoqda",
    "happened": "amalga oshdi",
    "partly": "qisman",
    "not_happened": "amalga oshmadi",
    "unclear": "aniq emas",
}
DEFAULT_MONTHS = 12


def months_from(horizon: str, given: int | None = None) -> int:
    """'6 oy' → 6, '1-2 yil' → 18, '2 years' → 24; the analysis's own number wins when it gave one."""
    if given:
        return max(1, min(36, int(given)))
    t = (horizon or "").lower()
    nums = [float(x) for x in re.findall(r"\d+(?:[.,]\d+)?", t.replace(",", "."))]
    if not nums:
        return DEFAULT_MONTHS
    n = sum(nums) / len(nums)  # a range "1-2" counts as its middle
    if re.search(r"yil|year|год|лет", t):
        n *= 12
    elif re.search(r"hafta|week|недел", t):
        n /= 4.3
    elif not re.search(r"oy|month|мес", t):
        n *= 12 if n <= 5 else 1  # a bare small number reads as years
    return max(1, min(36, round(n)))


def chance_of(s: dict) -> int:
    c = s.get("chance")
    if isinstance(c, int | float) and 0 <= c <= 100:
        return int(c)
    return WORD_CHANCE.get(str(s.get("probability", "")).lower(), 30)


def record(ref: str, a: str, b: str, data: dict, made: str) -> int:
    """Store the scenarios of one analysis; returns how many were new."""
    n = 0
    made_dt = datetime.fromisoformat(made)
    with db.tx() as c:
        for s in data.get("scenarios") or []:
            title = str(s.get("title", "")).strip()
            if not title:
                continue
            due = made_dt + timedelta(days=round(30.4 * months_from(s.get("horizon", ""), s.get("months"))))
            detail = " ".join(x for x in (s.get("trigger", ""), s.get("outcome", "")) if x)[:900]
            cur = c.execute(
                """INSERT INTO predictions (ref, a, b, title, detail, chance, made, due)
                   VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
                (ref, a, b, title[:200], detail, chance_of(s), made, due.replace(microsecond=0).isoformat()),
            )
            n += cur.rowcount or 0
    return n


def backfill() -> int:
    """Scenarios of analyses stored before the ledger existed."""
    n = 0
    for row in db.rows("SELECT key, a, b, created, data FROM pairs"):
        try:
            n += record(row["key"], row["a"], row["b"], json.loads(row["data"]), row["created"])
        except (ValueError, TypeError):
            continue
    return n


def due(limit: int = 3, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    return db.rows(
        "SELECT * FROM predictions WHERE status = 'open' AND due <= ? ORDER BY due LIMIT ?",
        (now.replace(microsecond=0).isoformat(), limit),
    )


def all_rows(status: str = "", limit: int = 300) -> list[dict]:
    sql, params = "SELECT * FROM predictions", []
    if status == "open":
        sql += " WHERE status = 'open'"
    elif status == "done":
        sql += " WHERE status <> 'open'"
    sql += " ORDER BY CASE WHEN status = 'open' THEN due END ASC, checked DESC, id DESC LIMIT ?"
    params.append(limit)
    out = db.rows(sql, params)
    for r in out:
        r["evidence"] = json.loads(r["evidence"] or "[]")
    return out


# ------------------------------------------------------------------ the check

CHECK_SYSTEM = """You check an old forecast against what happened since. You get the forecast (made on a
date, with the chance it was given and the date by which it should have become visible) and a
numbered list of news items about the two countries published after the forecast was made.

Decide, using only the news items:
- "happened": the forecast's main point clearly came true;
- "partly": some of it came true, or it is clearly under way;
- "not_happened": the items show the opposite, or the date passed with plenty of coverage and no sign of it;
- "unclear": the items are too few or say nothing about it.
Do not use outside knowledge of events. Return ONLY JSON:
{"outcome": "happened|partly|not_happened|unclear", "explanation": "2-3 sentences in {lang}", "evidence": [n, ...]}
Every outcome except "unclear" needs at least one evidence number."""


def prompt(p: dict, items: list[dict]) -> str:
    news = "\n".join(analyze.numbered(items)) if items else "(no news found)"
    return (
        f"Countries: {analyze.display_name(p['a'], 'en')} and {analyze.display_name(p['b'], 'en')}\n"
        f"Forecast made {p['made'][:10]}, chance {p['chance']}%, should be visible by {p['due'][:10]}:\n"
        f"{p['title']}. {p['detail']}\n\nNews since then:\n{news}\n\nReturn the JSON."
    )


def clean(raw: dict, items: list[dict]) -> dict:
    outcome = str(raw.get("outcome", "")).strip().lower()
    if outcome not in (*OUTCOMES, "unclear"):
        outcome = "unclear"
    ev = []
    for n in raw.get("evidence") or []:
        try:
            n = int(n)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(items) and n not in ev:
            ev.append(n)
    if outcome != "unclear" and not ev:
        outcome = "unclear"  # a verdict without evidence is not a verdict
    return {
        "outcome": outcome,
        "explanation": str(raw.get("explanation", "")).strip()[:700],
        "evidence": [
            {"n": n, "title": items[n - 1]["title"], "url": items[n - 1]["url"], "publisher": items[n - 1]["publisher"]}
            for n in ev
        ],
    }


def judge(p: dict, items: list[dict], lang: str = "uz") -> dict:
    system = CHECK_SYSTEM.replace("{lang}", analyze.LANG_NAMES.get(lang, "Uzbek"))
    for attempt in range(2):
        text, _model = analyze.complete(system, prompt(p, items), json_mode=True)
        try:
            raw = analyze._json_from(text)
            break
        except analyze.AIError:
            if attempt == 1:
                raise
    return clean(raw, items)


def save(pid: int, verdict: dict, now: datetime | None = None) -> None:
    """Store a check. 'unclear' keeps the forecast open for another month, at most twice."""
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    p = db.one("SELECT * FROM predictions WHERE id = ?", (pid,))
    if not p:
        return
    status = verdict["outcome"]
    with db.tx() as c:
        if status == "unclear" and p["due"] < (now + timedelta(days=1)).isoformat():
            tries = p["verdict"].count("[aniq emas]")
            if tries < 2:
                c.execute(
                    "UPDATE predictions SET due = ?, checked = ?, verdict = ? WHERE id = ?",
                    (
                        (now + timedelta(days=30)).isoformat(),
                        now.isoformat(),
                        (p["verdict"] + " [aniq emas]").strip(),
                        pid,
                    ),
                )
                return
        c.execute(
            "UPDATE predictions SET status = ?, checked = ?, verdict = ?, evidence = ? WHERE id = ?",
            (status, now.isoformat(), verdict["explanation"], json.dumps(verdict["evidence"], ensure_ascii=False), pid),
        )


# ------------------------------------------------------------------ scores


def stats() -> dict:
    rows = db.rows("SELECT chance, status FROM predictions")
    scored = [(r["chance"] / 100, OUTCOMES[r["status"]]) for r in rows if r["status"] in OUTCOMES]
    out = {
        "total": len(rows),
        "open": sum(r["status"] == "open" for r in rows),
        "unclear": sum(r["status"] == "unclear" for r in rows),
        "scored": len(scored),
        "brier": round(sum((p - o) ** 2 for p, o in scored) / len(scored), 3) if scored else None,
        "hits": None,
        "buckets": [],
    }
    if scored:
        # a forecast "hits" when it said likely (≥50%) and it happened, or unlikely and it did not
        hits = sum((p >= 0.5) == (o >= 0.5) for p, o in scored)
        out["hits"] = round(100 * hits / len(scored))
        for lo, hi in ((0, 20), (20, 40), (40, 60), (60, 80), (80, 101)):
            inb = [(p, o) for p, o in scored if lo <= p * 100 < hi]
            if inb:
                out["buckets"].append(
                    {
                        "range": f"{lo}–{min(hi, 100)}%",
                        "n": len(inb),
                        "said": round(100 * sum(p for p, _ in inb) / len(inb)),
                        "came": round(100 * sum(o for _, o in inb) / len(inb)),
                    }
                )
    return out
