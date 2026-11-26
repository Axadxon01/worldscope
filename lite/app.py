"""WorldScope Lite: one process, one SQLite file.

Run:  uvicorn app:app --reload        (from the lite/ folder)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import markdown as md
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

import analyze
import collectors
import db

HERE = Path(__file__).parent
REFRESH_MIN = int(os.environ.get("WS_REFRESH_MIN", "30"))
KEEP_DAYS = int(os.environ.get("WS_KEEP_DAYS", "7"))
PASSWORD = os.environ.get("APP_PASSWORD", "")
KINDS = {"rss": "RSS", "gnews": "Google News", "telegram": "Telegram", "gdelt": "GDELT"}

log = logging.getLogger("worldscope")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

state = {"running": False, "last_run": None, "last_added": 0}
_background: set[asyncio.Task] = set()


# ------------------------------------------------------------------ collection


def _save(results) -> int:
    added = 0
    stamp = collectors.now_iso()
    with db.tx() as c:
        for src, items, error in results:
            if error is not None:
                c.execute("UPDATE sources SET last_error=?, last_count=0 WHERE id=?", (error, src["id"]))
                continue
            for it in items:
                key = analyze.title_key(it["title"])
                # The same headline from the same publisher arrives through several routes.
                dup = c.execute(
                    "SELECT 1 FROM articles WHERE title_key=? AND publisher=? AND title_key<>'' LIMIT 1",
                    (key, it["publisher"]),
                ).fetchone()
                if dup:
                    continue
                cats, countries = analyze.tag(it["title"], it["summary"])
                cur = c.execute(
                    """INSERT OR IGNORE INTO articles
                       (url, title, summary, publisher, published, fetched, source_id, kind, lang,
                        categories, countries, title_key)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        it["url"],
                        it["title"],
                        it["summary"],
                        it["publisher"] or src["label"],
                        min(it["published"], stamp),
                        stamp,
                        src["id"],
                        src["kind"],
                        it.get("lang") or src["lang"],
                        analyze.dumps(cats),
                        analyze.dumps(countries),
                        key,
                    ),
                )
                added += cur.rowcount
            c.execute(
                "UPDATE sources SET last_ok=?, last_error=NULL, last_count=? WHERE id=?",
                (stamp, len(items), src["id"]),
            )
        cutoff = (datetime.now(UTC) - timedelta(days=KEEP_DAYS)).isoformat()
        c.execute("DELETE FROM articles WHERE published < ?", (cutoff,))
    return added


async def refresh() -> int:
    if state["running"]:
        return 0
    state["running"] = True
    try:
        sources = db.rows("SELECT * FROM sources WHERE enabled=1")
        results = await collectors.collect(sources)
        added = await asyncio.to_thread(_save, results)
        state.update(last_run=collectors.now_iso(), last_added=added)
        errors = sum(1 for _, _, e in results if e)
        log.info("refresh: %d sources, %d new articles, %d errors", len(sources), added, errors)
        return added
    finally:
        state["running"] = False


async def _loop():
    while True:
        try:
            await refresh()
        except Exception:
            log.exception("refresh failed")
        await asyncio.sleep(REFRESH_MIN * 60)


@asynccontextmanager
async def lifespan(_app):
    db.init(json.loads((HERE / "sources_default.json").read_text(encoding="utf-8")))
    task = asyncio.create_task(_loop())
    yield
    task.cancel()


app = FastAPI(title="WorldScope Lite", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


# ------------------------------------------------------------------ access


@app.middleware("http")
async def password_gate(request: Request, call_next):
    if PASSWORD and request.url.path != "/healthz":
        ok = False
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                _, _, given = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(given, PASSWORD)
            except (ValueError, UnicodeDecodeError):
                ok = False
        if not ok:
            return Response("Parol kerak", 401, {"WWW-Authenticate": 'Basic realm="WorldScope"'})
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


# ------------------------------------------------------------------ helpers


def _ago(iso: str) -> str:
    try:
        d = datetime.now(UTC) - datetime.fromisoformat(iso)
    except ValueError:
        return ""
    m = int(d.total_seconds() // 60)
    if m < 1:
        return "hozir"
    if m < 60:
        return f"{m} daq oldin"
    if m < 1440:
        return f"{m // 60} soat oldin"
    return f"{m // 1440} kun oldin"


def _render_brief(body: str, cites: list[dict]) -> Markup:
    known = {c["n"] for c in cites}
    html = md.markdown(str(escape(body)), extensions=["sane_lists"])
    return Markup(
        re.sub(
            r"\[(\d+)\]",
            lambda m: f'<a class="cite" href="#c{m.group(1)}">{m.group(1)}</a>' if int(m.group(1)) in known else "",
            html,
        )
    )


templates.env.filters["ago"] = _ago
templates.env.globals.update(
    KINDS=KINDS,
    CATS={k: v[0] for k, v in analyze.CATEGORIES.items()} | {"central-asia": "Markaziy Osiyo"},
    COUNTRIES={k: v["name"] for k, v in analyze.COUNTRY_INFO.items()},
    state=state,
)


def _articles(hours: int, q: str = "", kind: str = "", cat: str = "", country: str = "") -> list[dict]:
    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    sql = "SELECT * FROM articles WHERE published >= ?"
    params: list = [since]
    if kind in KINDS:
        sql += " AND kind = ?"
        params.append(kind)
    if q:
        sql += " AND (title LIKE ? OR summary LIKE ? OR publisher LIKE ?)"
        params += [f"%{q}%"] * 3
    if cat:
        sql += " AND categories LIKE ?"
        params.append(f'%"{cat}"%')
    if country:
        sql += " AND countries LIKE ?"
        params.append(f'%"{country}"%')
    sql += " ORDER BY published DESC LIMIT 1500"
    out = db.rows(sql, params)
    for a in out:
        a["categories"] = db.loads(a["categories"])
        a["countries"] = db.loads(a["countries"])
    return out


def _hours(v: str | None, default: int = 24) -> int:
    try:
        return max(1, min(int(v or default), KEEP_DAYS * 24))
    except ValueError:
        return default


# ------------------------------------------------------------------ pages


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def feed(
    request: Request,
    q: str = "",
    kind: str = "",
    cat: str = "",
    country: str = "",
    hours: str = "24",
    view: str = "stories",
):
    h = _hours(hours)
    arts = _articles(h, q.strip(), kind, cat, country)
    ov = analyze.overview(arts)
    return templates.TemplateResponse(
        request,
        "feed.html",
        {
            "arts": arts[:300],
            "ov": ov,
            "f": {"q": q, "kind": kind, "cat": cat, "country": country, "hours": h, "view": view},
            "empty_db": db.one("SELECT COUNT(*) n FROM articles")["n"] == 0,
        },
    )


@app.post("/refresh")
async def do_refresh(request: Request):
    task = asyncio.create_task(refresh())
    _background.add(task)  # keep a reference so the task is not garbage-collected mid-run
    task.add_done_callback(_background.discard)
    return RedirectResponse(request.headers.get("referer") or "/", 303)


@app.get("/brief", response_class=HTMLResponse)
def brief_page(request: Request, id: int | None = None, error: str = ""):
    briefs = db.rows("SELECT id, created, lang, hours, focus FROM briefs ORDER BY id DESC LIMIT 20")
    if id:
        current = db.one("SELECT * FROM briefs WHERE id=?", (id,))
    else:
        current = db.one("SELECT * FROM briefs ORDER BY id DESC LIMIT 1")
    if current:
        current["cites"] = db.loads(current["cites"])
        current["html"] = _render_brief(current["body"], current["cites"])
    return templates.TemplateResponse(
        request,
        "brief.html",
        {
            "briefs": briefs,
            "current": current,
            "ai": analyze.ai_available(),
            "model": analyze.model_label(),
            "error": error,
        },
    )


@app.post("/brief")
async def make_brief(lang: str = Form("uz"), hours: str = Form("24"), focus: str = Form("")):
    h = _hours(hours)
    focus = focus.strip()[:200]
    if not analyze.ai_available():
        return RedirectResponse("/brief?error=" + quote_plus("AI kaliti o'rnatilmagan"), 303)
    arts = _articles(h, q=focus if focus and len(focus.split()) == 1 else "")
    if focus and len(arts) < 5:  # a single-word focus that matched too little: let the model filter
        arts = _articles(h)
    try:
        result = await asyncio.to_thread(analyze.write_brief, arts, lang if lang in analyze.LANG_NAMES else "uz", focus)
    except (ValueError, analyze.AIError) as e:
        log.warning("brief failed: %s", e)
        return RedirectResponse("/brief?error=" + quote_plus(str(e)), 303)
    except Exception as e:  # show something useful instead of a 500
        log.exception("brief failed")
        msg = f"AI xatosi: {type(e).__name__}"
        return RedirectResponse("/brief?error=" + quote_plus(msg), 303)
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO briefs (created, lang, hours, focus, model, body, cites) VALUES (?,?,?,?,?,?,?)",
            (collectors.now_iso(), lang, h, focus, result["model"], result["body"], analyze.dumps(result["cites"])),
        )
        new_id = cur.lastrowid
    return RedirectResponse(f"/brief?id={new_id}", 303)


@app.get("/map", response_class=HTMLResponse)
def map_page(request: Request, id: int | None = None, error: str = "", hours: str = "48"):
    maps = db.rows("SELECT id, created, lang, hours, focus FROM maps ORDER BY id DESC LIMIT 20")
    if id:
        current = db.one("SELECT * FROM maps WHERE id=?", (id,))
    else:
        current = db.one("SELECT * FROM maps ORDER BY id DESC LIMIT 1")
    if current:
        current["data"] = json.loads(current["data"])
    h = _hours(hours, 48)
    coords = {k: [round(v["lat"], 2), round(v["lon"], 2)] for k, v in analyze.COUNTRY_INFO.items()}
    lang = current["lang"] if current else "uz"
    names = {k: analyze.display_name(k, lang) for k in analyze.COUNTRY_INFO}
    payload = {
        "geo": {v["name"]: k for k, v in analyze.COUNTRY_INFO.items()},
        "links": current["data"]["links"] if current else [],
        "co": analyze.co_mentions(_articles(h)),
        "coords": coords,
        "names": names,
        "types": analyze.LINK_TYPES,
        "cats": {k: v[0] for k, v in analyze.CATEGORIES.items()},
    }
    return templates.TemplateResponse(
        request,
        "map.html",
        {
            "maps": maps,
            "current": current,
            "ai": analyze.ai_available(),
            "error": error,
            "hours": h,
            "payload": Markup(json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")),
            "types": analyze.LINK_TYPES,
        },
    )


@app.post("/map")
async def make_map(lang: str = Form("uz"), hours: str = Form("48"), focus: str = Form("")):
    h = _hours(hours, 48)
    focus = focus.strip()[:200]
    if not analyze.ai_available():
        return RedirectResponse("/map?error=" + quote_plus("AI kaliti o'rnatilmagan"), 303)
    lang = lang if lang in analyze.LANG_NAMES else "uz"
    try:
        result = await asyncio.to_thread(analyze.write_links, _articles(h), lang, focus)
    except (ValueError, analyze.AIError) as e:
        log.warning("map failed: %s", e)
        return RedirectResponse("/map?error=" + quote_plus(str(e)), 303)
    except Exception as e:  # show something useful instead of a 500
        log.exception("map failed")
        return RedirectResponse("/map?error=" + quote_plus(f"AI xatosi: {type(e).__name__}"), 303)
    data = {"summary": result["summary"], "links": result["links"]}
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO maps (created, lang, hours, focus, model, data) VALUES (?,?,?,?,?,?)",
            (collectors.now_iso(), lang, h, focus, result["model"], analyze.dumps(data)),
        )
        new_id = cur.lastrowid
    return RedirectResponse(f"/map?id={new_id}", 303)


@app.get("/sources", response_class=HTMLResponse)
def sources_page(request: Request, error: str = ""):
    srcs = db.rows("""SELECT s.*, (SELECT COUNT(*) FROM articles a WHERE a.source_id=s.id) AS stored
                      FROM sources s ORDER BY kind, label""")
    return templates.TemplateResponse(
        request,
        "sources.html",
        {
            "srcs": srcs,
            "error": error,
            "editions": list(collectors.GNEWS_EDITIONS),
        },
    )


@app.post("/sources/add")
def add_source(kind: str = Form(...), value: str = Form(...), label: str = Form(""), lang: str = Form("en")):
    value, label = value.strip(), label.strip()
    err = ""
    if kind not in KINDS or not value:
        err = "Turi va qiymati kerak"
    elif kind == "rss" and not re.match(r"https?://", value):
        err = "RSS manzili http:// yoki https:// bilan boshlanishi kerak"
    elif kind == "telegram":
        value = value.lstrip("@").split("t.me/")[-1].removeprefix("s/").strip("/")
        if not re.fullmatch(r"[A-Za-z0-9_]{4,64}", value):
            err = "Telegram kanal nomi noto'g'ri (masalan: kunuzofficial)"
    if err:
        return RedirectResponse("/sources?error=" + err.replace(" ", "+"), 303)
    label = label or f"{KINDS[kind]}: {value[:60]}"
    with db.tx() as c:
        c.execute(
            "INSERT OR IGNORE INTO sources (kind, value, label, lang) VALUES (?,?,?,?)",
            (kind, value, label[:120], lang[:10]),
        )
    return RedirectResponse("/sources", 303)


@app.post("/sources/{sid}/toggle")
def toggle_source(sid: int):
    with db.tx() as c:
        c.execute("UPDATE sources SET enabled = 1 - enabled WHERE id=?", (sid,))
    return RedirectResponse("/sources", 303)


@app.post("/sources/{sid}/delete")
def delete_source(sid: int):
    with db.tx() as c:
        c.execute("DELETE FROM sources WHERE id=?", (sid,))
    return RedirectResponse("/sources", 303)


@app.get("/api/articles")
def api_articles(hours: str = "24", q: str = "", kind: str = "", cat: str = "", country: str = ""):
    return JSONResponse(_articles(_hours(hours), q, kind, cat, country)[:500])
