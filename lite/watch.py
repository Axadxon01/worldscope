"""Watch list: the countries and companies a person follows, and the alerts they get.

A company or person is checked against the sanctions lists every time the lists are refreshed:
a match that was not there before is an alert. A country is checked once a day against the risk
index: a move of WS_WATCH_RISK_DELTA points (5) or more since the last check is an alert. Alerts are
kept on the watch page and, when the person has linked Telegram, sent there too.
"""

from __future__ import annotations

import html
import json
import os
from datetime import UTC, datetime

import analyze
import db
import pairs
import sanctions

COUNTRY, COMPANY = "country", "company"
MATCH_MIN = 0.85  # "kuchli o'xshash" and above
RISK_DELTA = float(os.environ.get("WS_WATCH_RISK_DELTA", "5"))
MAX_ITEMS = 40
KEEP_ALERTS = 60


class WatchError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def items(user_id: int) -> list[dict]:
    out = db.rows("SELECT * FROM watch_items WHERE user_id = ? ORDER BY kind, created", (user_id,))
    for r in out:
        r["state"] = json.loads(r["state"] or "{}")
    return out


def matches(name: str) -> list[dict]:
    return [m for m in sanctions.search(name, limit=12) if m["score"] >= MATCH_MIN]


def add(user_id: int, kind: str, value: str) -> dict:
    value = (value or "").strip()
    if kind == COUNTRY:
        value = value.upper()[:3]
        if not pairs.valid(value):
            raise WatchError("Davlatni ro'yxatdan tanlang")
        state: dict = {}
    elif kind == COMPANY:
        value = " ".join(value.split())[:120]
        if len(sanctions.norm(value)) < 3:
            raise WatchError("Nomni kamida 3 harf bilan yozing")
        state = {"known": sorted(f"{m['list']}:{m['uid']}" for m in matches(value)), "checked": _now()}
    else:
        raise WatchError("Noma'lum tur")
    if db.one("SELECT COUNT(*) AS n FROM watch_items WHERE user_id = ?", (user_id,))["n"] >= MAX_ITEMS:
        raise WatchError(f"Ro'yxatda ko'pi bilan {MAX_ITEMS} ta narsa bo'lishi mumkin")
    with db.tx() as c:
        c.execute(
            "INSERT INTO watch_items (user_id, kind, value, created, state) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
            (user_id, kind, value, _now(), json.dumps(state)),
        )
    return {"kind": kind, "value": value, "state": state}


def remove(user_id: int, item_id: int) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM watch_items WHERE id = ? AND user_id = ?", (item_id, user_id))


def _save_state(item_id: int, state: dict) -> None:
    with db.tx() as c:
        c.execute("UPDATE watch_items SET state = ? WHERE id = ?", (json.dumps(state), item_id))


def note(user_id: int, kind: str, text: str) -> None:
    with db.tx() as c:
        c.execute("INSERT INTO alerts (user_id, created, kind, text) VALUES (?,?,?,?)", (user_id, _now(), kind, text))
        old = c.execute(
            "SELECT id FROM alerts WHERE user_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?", (user_id, KEEP_ALERTS)
        ).fetchone()
        if old:
            c.execute("DELETE FROM alerts WHERE user_id = ? AND id <= ?", (user_id, old["id"]))


def alerts(user_id: int, limit: int = 20) -> list[dict]:
    return db.rows("SELECT * FROM alerts WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))


# ------------------------------------------------------------------ checks


def check_sanctions() -> list[tuple[int, str]]:
    """New sanctions matches for every watched name: [(user_id, alert text)]."""
    out = []
    for it in db.rows("SELECT * FROM watch_items WHERE kind = ?", (COMPANY,)):
        state = json.loads(it["state"] or "{}")
        found = matches(it["value"])
        now = {f"{m['list']}:{m['uid']}": m for m in found}
        known = set(state.get("known", []))
        new = [now[k] for k in sorted(set(now) - known)]
        state.update(known=sorted(set(now) | known), checked=_now())
        _save_state(it["id"], state)
        if new:
            lines = [f"⚖️ «{it['value']}» sanksiya ro'yxatida yangi moslik topildi:"]
            for m in new[:5]:
                where = ", ".join(analyze.display_name(c) for c in m["countries"][:3])
                lines.append(
                    f"• {m['name']} — {m['list']} ({sanctions.match_label(m['score'])}){' · ' + where if where else ''}"
                )
            lines.append("Nom mosligi — o'sha shaxs ekanini anglatmaydi, rasmiy manbada tekshiring.")
            out.append((it["user_id"], "\n".join(lines)))
    return out


def check_risk(rows: list[dict]) -> list[tuple[int, str]]:
    """Risk index moves of RISK_DELTA points or more since the last check."""
    by_iso = {r["iso"]: r for r in rows}
    out = []
    for it in db.rows("SELECT * FROM watch_items WHERE kind = ?", (COUNTRY,)):
        r = by_iso.get(it["value"])
        if not r or r["score"] is None:
            continue
        state = json.loads(it["state"] or "{}")
        last = state.get("score")
        state.update(score=r["score"], band=r["band"], checked=_now())
        _save_state(it["id"], state)
        if last is not None and abs(r["score"] - last) >= RISK_DELTA:
            arrow = "▲ oshdi" if r["score"] > last else "▼ pasaydi"
            out.append(
                (
                    it["user_id"],
                    f"🧭 {analyze.display_name(it['value'])}: xavf indeksi {arrow} — "
                    f"{last} → {r['score']} ({r['band']}).",
                )
            )
    return out


def view(user_id: int, risk_rows: list[dict] | None, sanc_stats: dict) -> list[dict]:
    """Items with what to show next to each: score and change, or the current matches."""
    by_iso = {r["iso"]: r for r in risk_rows or []}
    out = []
    for it in items(user_id):
        v = {"id": it["id"], "kind": it["kind"], "value": it["value"], "created": it["created"], "state": it["state"]}
        if it["kind"] == COUNTRY:
            r = by_iso.get(it["value"])
            v["name"] = analyze.display_name(it["value"])
            v["score"] = r["score"] if r else None
            v["color"] = r["color"] if r else "#94a3b8"
            v["band"] = r["band"] if r else ""
            v["since"] = it["state"].get("score")
            v["sanctions"] = sanc_stats.get(it["value"], {}).get("total", 0)
        else:
            v["matches"] = matches(it["value"]) if sanctions.loaded() else []
        out.append(v)
    return out


def personal_digest(user_id: int, articles: list[dict]) -> str | None:
    """For a linked person: the two most-covered stories about each watched country today."""
    countries = [it["value"] for it in items(user_id) if it["kind"] == COUNTRY]
    if not countries:
        return None
    parts = []
    for iso in countries[:8]:
        about = [a for a in articles if iso in a.get("countries", [])]
        if not about:
            continue
        groups = analyze.cluster(about)[:2]
        lines = [f"<b>{html.escape(analyze.display_name(iso))}</b>"]
        for g in groups:
            pubs = len({a["publisher"] for a in g})
            lines.append(f"• {html.escape(g[0]['title'])} <i>({pubs} manba)</i>")
        parts.append("\n".join(lines))
    if not parts:
        return None
    return "📌 <b>Siz kuzatayotgan davlatlar</b>\n\n" + "\n\n".join(parts)
