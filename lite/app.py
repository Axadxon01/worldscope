"""WorldScope Lite: one process, one SQLite file.

Run:  uvicorn app:app --reload        (from the lite/ folder)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

import httpx
import markdown as md
from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

import accounts
import analyze
import collectors
import db
import debt
import embed
import explain
import facts
import framing
import pairs
import predictions
import risk
import sanctions
import scheduler
import telegram_bot
import trade
import watch

HERE = Path(__file__).parent
REFRESH_MIN = int(os.environ.get("WS_REFRESH_MIN", "30"))
KEEP_DAYS = int(os.environ.get("WS_KEEP_DAYS", "7"))
PASSWORD = os.environ.get("APP_PASSWORD", "")
KINDS = {"rss": "RSS", "gnews": "Google News", "telegram": "Telegram", "gdelt": "GDELT"}

log = logging.getLogger("worldscope")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


class _Redact(logging.Filter):
    """Keep secrets out of the logs: httpx logs request URLs, and Telegram's carries the bot token."""

    SECRET = re.compile(r"(/bot)\d+:[\w-]+|((?:key|token|api_key|secret)=)[^&\s\"']+", re.I)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        clean = self.SECRET.sub(lambda m: (m.group(1) or m.group(2)) + "***", msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


for _h in logging.getLogger().handlers:
    _h.addFilter(_Redact())

state = {"running": False, "last_run": None, "last_added": 0, "ai_job": "", "sanctions": "", "jobs": ""}
AI_DAILY_LIMIT = int(os.environ.get("WS_AI_DAILY_LIMIT", "60"))
_ai_lock = asyncio.Lock()
_background: set[asyncio.Task] = set()
CRON_TOKEN = os.environ.get("CRON_TOKEN", "").strip()
WEEKLY_PAIRS = int(os.environ.get("WS_WEEKLY_PAIRS", "4"))
# Load the sanctions lists by itself when they are missing or more than a week old.
SANCTIONS_AUTO = os.environ.get("WS_SANCTIONS_AUTO", "1") == "1"
# Called by machines with their own secret, not by people with the site password.
MACHINE_PATHS = ("/healthz", "/telegram/webhook", "/cron/")


def _keep(task: asyncio.Task) -> asyncio.Task:
    """Hold a reference so a fire-and-forget task is not garbage-collected mid-run."""
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task


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
                    """INSERT INTO articles
                       (url, title, summary, publisher, published, fetched, source_id, kind, lang,
                        categories, countries, title_key)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
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


EMBED_PER_MINUTE = 90  # the free tier allows 100 embedding items per minute per model
EMBED_ROUNDS = int(os.environ.get("WS_EMBED_ROUNDS", "4"))


def _embed_new(limit: int = EMBED_PER_MINUTE) -> int:
    """Meaning vectors for the newest headlines that have none; failures only cost the grouping.

    Spends at most the daily allowance (embed.DAILY), only on the last 36 hours, and after a quota
    error waits as long as Google asks. Returns how many were done, -1 on an error.
    """
    allow = min(limit, embed.remaining())
    if allow <= 0:
        return 0
    since = (datetime.now(UTC) - timedelta(hours=36)).isoformat()
    rows = db.rows(
        "SELECT id, title FROM articles WHERE emb IS NULL AND published >= ? ORDER BY published DESC LIMIT ?",
        (since, allow),
    )
    if not rows:
        return 0
    try:
        vecs = embed.embed([r["title"] for r in rows])
    except embed.EmbedError as e:  # quota, network: the token-based grouping still works
        until = embed.back_off(e)
        embed.note_error(e)
        log.warning("embedding failed: %s%s", e, f" (waiting until {until:%Y-%m-%d %H:%M} UTC)" if until else "")
        return -1
    embed.spend(len(rows))
    with db.tx() as c:
        for r, v in zip(rows, vecs, strict=True):
            c.execute("UPDATE articles SET emb = ? WHERE id = ?", (embed.pack(v), r["id"]))
    return len(rows)


def _embed_backlog() -> None:
    """A few rounds a minute apart, newest first, within the free per-minute quota."""
    if state.get("embedding"):
        return
    state["embedding"] = True
    try:
        for i in range(EMBED_ROUNDS):
            done = _embed_new()
            if done < EMBED_PER_MINUTE:  # nothing left, or the quota said stop
                break
            if i < EMBED_ROUNDS - 1:
                time.sleep(61)
    finally:
        state["embedding"] = False


async def refresh() -> int:
    if state["running"]:
        return 0
    state["running"] = True
    try:
        sources = db.rows("SELECT * FROM sources WHERE enabled=1")
        results = await collectors.collect(sources)
        added = await asyncio.to_thread(_save, results)
        if embed.enabled():
            _keep(asyncio.create_task(asyncio.to_thread(_embed_backlog)))
        state.update(last_run=collectors.now_iso(), last_added=added)
        failed = [f"{src['label']}: {str(e)[:80]}" for src, _, e in results if e]
        log.info(
            "refresh: %d sources, %d new articles, %d errors%s",
            len(sources), added, len(failed), f" ({'; '.join(failed)})" if failed else "",
        )  # fmt: skip
        return added
    finally:
        state["running"] = False


LOOP_MIN = int(os.environ.get("WS_LOOP_MIN", "10"))


async def _loop():
    """Run whatever is due now, then look again every few minutes while the service is awake."""
    await asyncio.sleep(3)
    while True:
        try:
            await run_due_jobs()
        except Exception:
            log.exception("scheduled jobs failed")
        await asyncio.sleep(min(LOOP_MIN, REFRESH_MIN) * 60)


async def refresh_sanctions(only: list[str] | None = None) -> dict:
    if state["sanctions"]:
        return {"skipped": "ro'yxatlar allaqachon yuklanmoqda"}
    state["sanctions"] = "yuklanmoqda"
    try:
        return await sanctions.refresh(only)
    finally:
        state["sanctions"] = ""


async def _sanctions_when_stale(delay: float = 0) -> None:
    await asyncio.sleep(delay)  # let the first news refresh go first
    try:
        if await asyncio.to_thread(sanctions.stale):
            log.info("sanctions lists: %s", await refresh_sanctions())
    except Exception:
        log.exception("sanctions refresh failed")


@asynccontextmanager
async def lifespan(_app):
    db.init(json.loads((HERE / "sources_default.json").read_text(encoding="utf-8")))
    # WS_LOOP=0 (tests) keeps the background loop from racing the test's own refresh.
    task = asyncio.create_task(_loop()) if os.environ.get("WS_LOOP", "1") == "1" else None
    if telegram_bot.enabled():
        _keep(asyncio.create_task(telegram_bot.setup_webhook()))
    yield
    if task:
        task.cancel()
    db.close()


app = FastAPI(title="WorldScope Lite", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
app.add_middleware(GZipMiddleware, minimum_size=1200, compresslevel=6)
templates = Jinja2Templates(directory=HERE / "templates")
COORDS = {k: [round(v["lat"], 2), round(v["lon"], 2)] for k, v in analyze.COUNTRY_INFO.items()}


# ------------------------------------------------------------------ access


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'self'; form-action 'self'"
)


def _same_site(request: Request) -> bool:
    """True when a state-changing request comes from a page of this site.

    The browser remembers the password and sends it with any request to this site, including one
    started by another site (cross-site request forgery). Browsers tell us where a request came
    from: Sec-Fetch-Site, and the Origin or Referer header. Anything else is refused.
    """
    site = request.headers.get("sec-fetch-site")
    if site:
        return site in ("same-origin", "none")
    host = request.headers.get("host", "")
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value and value != "null":
            return urlsplit(value).netloc == host
    return False


OPEN_PATHS = ("/login", "/static/", "/favicon.ico")
ASSET_VERSION = (os.environ.get("RENDER_GIT_COMMIT") or str(int(time.time())))[:10]


def _user_for(request: Request) -> tuple[str, dict | None]:
    mode = accounts.mode(PASSWORD)
    if mode != "accounts":
        return mode, None
    return mode, accounts.session_user(request.cookies.get(accounts.COOKIE))


@app.middleware("http")
async def access_gate(request: Request, call_next):
    path = request.url.path
    machine = path.startswith(MACHINE_PATHS)
    request.state.user = None
    request.state.mode = "open"
    if not machine and not path.startswith(OPEN_PATHS):
        mode, user = await asyncio.to_thread(_user_for, request)
        request.state.mode, request.state.user = mode, user
        if mode == "setup" or (mode == "accounts" and not user):
            if path.startswith("/api/"):
                return JSONResponse({"error": "kirish kerak"}, status_code=401)
            nxt = path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse("/login" + (f"?next={quote_plus(nxt)}" if nxt not in ("/", "") else ""), 303)
    if request.method not in SAFE_METHODS and not machine and not _same_site(request):
        return Response("So'rov boshqa saytdan keldi va rad etildi.", 403)
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # same-origin, not no-referrer: with no-referrer browsers send "Origin: null" on our own forms.
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = CSP
    if path.startswith("/static/"):
        # versioned links (?v=) can be kept a week; the rest is checked with the ETag each time
        versioned = "v=" in request.url.query
        response.headers["Cache-Control"] = "public, max-age=604800, immutable" if versioned else "no-cache"
    return response


def asset(path: str) -> str:
    return f"/static/{path}?v={ASSET_VERSION}"


# ------------------------------------------------------------------ AI budget


def busy_message() -> str:
    return f"Hozir boshqa AI ish bajarilmoqda ({state['ai_job']}). Bir daqiqadan keyin urinib ko'ring."


def ai_used_today() -> int:
    row = db.one("SELECT calls FROM ai_usage WHERE day = ?", (collectors.now_iso()[:10],))
    return row["calls"] if row else 0


async def run_ai(label: str, fn, *args):
    """Run one AI job at a time, within the daily budget.

    Free AI quotas are small: a double click, two tabs or two visitors must not spend them twice.
    Raises AIError with a message for the page when the job cannot run now.
    """
    if _ai_lock.locked():
        raise analyze.AIError(
            f"Hozir boshqa AI ish bajarilmoqda ({state['ai_job']}). Bir daqiqadan keyin urinib ko'ring."
        )
    async with _ai_lock:
        used = await asyncio.to_thread(ai_used_today)
        if used >= AI_DAILY_LIMIT:
            raise analyze.AIError(f"Bugungi AI limiti tugadi ({AI_DAILY_LIMIT} ta). Ertaga yana ishlaydi.")
        with db.tx() as c:
            c.execute(
                "INSERT INTO ai_usage (day, calls) VALUES (?, 1) "
                "ON CONFLICT (day) DO UPDATE SET calls = ai_usage.calls + 1",
                (collectors.now_iso()[:10],),
            )
        state["ai_job"] = label
        try:
            return await asyncio.to_thread(fn, *args)
        finally:
            state["ai_job"] = ""


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


def _cite_links(text: str, sources: list[dict]) -> Markup:
    """Escape the text and turn [n] into a link to source n; drop numbers with no source."""
    known = {c["n"] for c in sources or []}
    return Markup(
        re.sub(
            r"\[(\d+)\]",
            lambda m: f'<a class="cite" href="#s{m.group(1)}">{m.group(1)}</a>' if int(m.group(1)) in known else "",
            str(escape(text or "")),
        )
    )


templates.env.filters["cites"] = _cite_links
templates.env.globals.update(
    KINDS=KINDS,
    CATS={k: v[0] for k, v in analyze.CATEGORIES.items()} | {"central-asia": "Markaziy Osiyo"},
    COUNTRIES={k: v["name"] for k, v in analyze.COUNTRY_INFO.items()},
    state=state,
    ai_used_today=ai_used_today,
    ai_limit=lambda: AI_DAILY_LIMIT,
    fmt_usd=lambda v: facts.human(v, "usd"),
    db_where=db.describe,
    asset=asset,
    uzname=analyze.display_name,
    glossary_json=Markup(json.dumps(explain.GLOSSARY, ensure_ascii=False).replace("</", "<\\/")),
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
    """For the wake-up workflow: busy while background jobs still run."""
    return {"ok": True, "busy": bool(state["jobs"] or state["running"] or state["ai_job"] or state["sanctions"])}


_FEED_CACHE: dict[tuple, tuple[float, tuple]] = {}
FEED_TTL = 300


def _feed_data(h: int, q: str, kind: str, cat: str, country: str) -> tuple[list, dict, dict | None]:
    """Articles, their grouping and the front-page globe; the grouping is kept 5 minutes or until the
    next refresh, since it is the slowest part of the page."""
    last_map = db.one("SELECT id FROM maps ORDER BY id DESC LIMIT 1")
    key = (h, q, kind, cat, country, state["last_run"], last_map["id"] if last_map else 0)
    hit = _FEED_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < FEED_TTL:
        return hit[1]
    arts = _articles(h, q, kind, cat, country)
    ov = analyze.overview(arts)
    hero = None
    if not (q or kind or cat or country):  # the globe on the front page, unfiltered view only
        latest = db.one("SELECT data FROM maps ORDER BY id DESC LIMIT 1")
        links = json.loads(latest["data"])["links"] if latest else []
        hero = {
            "geo": {v["name"]: k for k, v in analyze.COUNTRY_INFO.items()},
            "links": links,
            "co": [] if links else analyze.co_mentions(_articles(48))[:12],
            "coords": COORDS,
            "names": {k: analyze.display_name(k) for k in analyze.COUNTRY_INFO},
            "types": analyze.LINK_TYPES,
        }
    if len(_FEED_CACHE) > 40:
        _FEED_CACHE.clear()
    _FEED_CACHE[key] = (time.monotonic(), (arts, ov, hero))
    return arts, ov, hero


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
    arts, ov, hero = _feed_data(h, q.strip(), kind, cat, country)
    return templates.TemplateResponse(
        request,
        "feed.html",
        {
            "hero": hero,
            "simple": explain.for_groups(ov["groups"][:80]),
            "top3": [g for g in ov["groups"] if len({a["publisher"] for a in g}) >= 2][:3]
            if not (q or kind or cat or country)
            else [],
            "ekey": explain.key,
            "hero_json": Markup(json.dumps(hero, ensure_ascii=False).replace("</", "<\\/")) if hero else "",
            "arts": arts[:300],
            "ov": ov,
            "f": {"q": q, "kind": kind, "cat": cat, "country": country, "hours": h, "view": view},
            "empty_db": db.one("SELECT COUNT(*) n FROM articles")["n"] == 0,
        },
    )


@app.post("/refresh")
async def do_refresh(request: Request):
    _keep(asyncio.create_task(refresh()))
    back = urlsplit(request.headers.get("referer") or "/")
    target = back.path if back.netloc in ("", request.headers.get("host", "")) and back.path.startswith("/") else "/"
    return RedirectResponse(target + (f"?{back.query}" if back.query and target != "/" else ""), 303)


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
        return RedirectResponse(
            "/brief?error=" + quote_plus("AI tahlili hozircha mavjud emas, keyinroq urinib ko'ring"), 303
        )
    arts = _articles(h, q=focus if focus and len(focus.split()) == 1 else "")
    if focus and len(arts) < 5:  # a single-word focus that matched too little: let the model filter
        arts = _articles(h)
    try:
        lang = lang if lang in analyze.LANG_NAMES else "uz"
        result = await run_ai("AI tahlil", analyze.write_brief, arts, lang, focus)
    except (ValueError, analyze.AIError) as e:
        log.warning("brief failed: %s", e)
        return RedirectResponse("/brief?error=" + quote_plus(str(e)), 303)
    except Exception as e:  # show something useful instead of a 500
        log.exception("brief failed")
        msg = f"AI xatosi: {type(e).__name__}"
        return RedirectResponse("/brief?error=" + quote_plus(msg), 303)
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO briefs (created, lang, hours, focus, model, body, cites) VALUES (?,?,?,?,?,?,?) RETURNING id",
            (collectors.now_iso(), lang, h, focus, result["model"], result["body"], analyze.dumps(result["cites"])),
        )
        new_id = cur.fetchone()["id"]
    return RedirectResponse(f"/brief?id={new_id}", 303)


@app.get("/map", response_class=HTMLResponse)
def map_page(request: Request, id: int | None = None, error: str = "", hours: str = "48", trade: str = ""):
    maps = db.rows("SELECT id, created, lang, hours, focus, auto FROM maps ORDER BY id DESC LIMIT 20")
    if id:
        current = db.one("SELECT * FROM maps WHERE id=?", (id,))
    else:
        current = db.one("SELECT * FROM maps ORDER BY id DESC LIMIT 1")
    timeline, diff = [], None
    if current:
        current["data"] = json.loads(current["data"])
        # the time machine: every saved map in order, and what changed since the one before
        timeline = db.rows("SELECT id, created, auto FROM maps ORDER BY created, id")
        before = db.one(
            "SELECT data, created FROM maps WHERE created < ? OR (created = ? AND id < ?) "
            "ORDER BY created DESC, id DESC LIMIT 1",
            (current["created"], current["created"], current["id"]),
        )
        if before:
            diff = _map_diff(json.loads(before["data"])["links"], current["data"]["links"])
            diff["since"] = before["created"]
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
        "fresh": diff["new"] if diff else [],
        "trade": trade.upper()[:3] if pairs.valid(trade.upper()[:3]) else "",
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
            "timeline": timeline,
            "diff": diff,
            "names": names,
            "payload_isos": pairs.SOVEREIGN,
        },
    )


def _map_diff(old_links: list[dict], new_links: list[dict]) -> dict:
    """Links that appeared or disappeared between two maps (same pair and kind = the same link)."""
    k = lambda link: (link.get("from"), link.get("to"), link.get("type"))  # noqa: E731
    old_keys = {k(x) for x in old_links}
    new_keys = {k(x) for x in new_links}
    return {
        "new": [i for i, x in enumerate(new_links) if k(x) not in old_keys],
        "gone": [x for x in old_links if k(x) not in new_keys],
    }


@app.post("/map")
async def make_map(lang: str = Form("uz"), hours: str = Form("48"), focus: str = Form("")):
    h = _hours(hours, 48)
    focus = focus.strip()[:200]
    if not analyze.ai_available():
        return RedirectResponse(
            "/map?error=" + quote_plus("AI tahlili hozircha mavjud emas, keyinroq urinib ko'ring"), 303
        )
    lang = lang if lang in analyze.LANG_NAMES else "uz"
    try:
        result = await run_ai("xarita", analyze.write_links, _articles(h), lang, focus)
    except (ValueError, analyze.AIError) as e:
        log.warning("map failed: %s", e)
        return RedirectResponse("/map?error=" + quote_plus(str(e)), 303)
    except Exception as e:  # show something useful instead of a 500
        log.exception("map failed")
        return RedirectResponse("/map?error=" + quote_plus(f"AI xatosi: {type(e).__name__}"), 303)
    data = {"summary": result["summary"], "links": result["links"]}
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO maps (created, lang, hours, focus, model, data) VALUES (?,?,?,?,?,?) RETURNING id",
            (collectors.now_iso(), lang, h, focus, result["model"], analyze.dumps(data)),
        )
        new_id = cur.fetchone()["id"]
    return RedirectResponse(f"/map?id={new_id}", 303)


def _pair_db_news(a: str, b: str) -> list[dict]:
    """Stored articles from the last week that are tagged with both countries."""
    return [x for x in _articles(KEEP_DAYS * 24, country=a) if b in x["countries"]][:20]


def _pair_row(k: str) -> dict | None:
    row = db.one("SELECT * FROM pairs WHERE key=?", (k,))
    if not row:
        return None
    data = json.loads(row["data"])
    data["created"] = row["created"]
    return data


def _history(k: str) -> list[dict]:
    return db.rows("SELECT created, score, label FROM pair_history WHERE key = ? ORDER BY created", (k,))


async def analyze_pair(a: str, b: str, lang: str, c: str = "") -> tuple[dict, dict | None]:
    """News + hard figures + the AI, stored with its history. Returns (new, previous)."""
    k = pairs.key(a, b, lang, c)
    previous = await asyncio.to_thread(_pair_row, k)
    fetched = await pairs.pair_news(a, b)
    if c:  # what is said about each side with the third country
        fetched += (await pairs.pair_news(a, c))[:10] + (await pairs.pair_news(b, c))[:10]
    items = pairs.merge_news(await asyncio.to_thread(_pair_db_news, a, b), fetched, limit=55 if c else 45)
    names = {x: analyze.display_name(x) for x in (a, b)}
    try:
        f = await facts.pair_facts(a, b)
        facts_text = facts.prompt_block(f, a, b, names)
        for d in await _mutual_debt(a, b):
            facts_text += (
                f"\n- Public debt {names[d['debtor']]} owes {names[d['creditor']]} (World Bank IDS): "
                f"{d['then']} ({d['then_year']}) → {d['now']} ({d['year']})"
            )
    except Exception:  # the figures are a bonus; never fail the analysis over them
        log.exception("facts failed")
        f, facts_text = {"countries": {}, "trade": None}, ""
    label = f"{names[a]} ↔ {names[b]}" + (f" ({analyze.display_name(c)})" if c else "")
    result = await run_ai(label, pairs.write_pair, a, b, lang, items, facts_text, previous, c)
    result["facts"] = {"rows": facts.rows_for(f, a, b), "trade": f.get("trade")}
    stamp = collectors.now_iso()
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO pairs (key, a, b, c, lang, created, data) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT (key) DO UPDATE SET created = excluded.created, data = excluded.data",
            (k, a, b, c, lang, stamp, analyze.dumps(result)),
        )
        conn.execute(
            "INSERT INTO pair_history (key, created, score, label) VALUES (?,?,?,?)",
            (k, stamp, result["score"], result["label"]),
        )
    try:
        await asyncio.to_thread(predictions.record, k, a, b, result, stamp)
    except Exception:  # the ledger must never cost the analysis
        log.exception("could not record the forecasts")
    return result, previous


async def _mutual_debt(a: str, b: str) -> list[dict]:
    """Public debt each owes the other, where the World Bank records it (borrowers are developing countries)."""
    out = []
    for debtor, creditor in ((a, b), (b, a)):
        try:
            years = await asyncio.wait_for(debt.bilateral(debtor, creditor), 20)
        except Exception:
            years = []
        if years:
            first = next((p for p in years if p[0] >= 2010), years[0])
            out.append(
                {
                    "debtor": debtor,
                    "creditor": creditor,
                    "year": years[-1][0],
                    "now": facts.human(years[-1][1], "usd"),
                    "then_year": first[0],
                    "then": facts.human(first[1], "usd"),
                }
            )
    return out


def _spark(history: list[dict], w: int = 260, h: int = 54) -> list[dict]:
    """Points of a small score chart (-5..5), oldest left."""
    if not history:
        return []
    n = len(history)
    out = []
    for i, p in enumerate(history):
        x = 8 + (w - 16) * (i / (n - 1) if n > 1 else 0.5)
        y = 6 + (h - 12) * (5 - p["score"]) / 10
        out.append(
            {"x": round(x, 1), "y": round(y, 1), "score": p["score"], "date": p["created"][:10], "label": p["label"]}
        )
    return out


@app.get("/pair", response_class=HTMLResponse)
async def pair_page(request: Request, a: str = "", b: str = "", c: str = "", lang: str = "uz", error: str = ""):
    a, b, c = a.upper()[:3], b.upper()[:3], c.upper()[:3]
    lang = lang if lang in analyze.LANG_NAMES else "uz"
    if c and (not pairs.valid(c) or c in (a, b)):
        c = ""
    result, history, nums = None, [], None
    if pairs.valid(a) and pairs.valid(b) and a != b:
        k = pairs.key(a, b, lang, c)
        result = await asyncio.to_thread(_pair_row, k)
        history = await asyncio.to_thread(_history, k)
        if result and result.get("facts"):
            nums = dict(result["facts"])
        else:  # the figures need no AI: show them before any analysis exists
            try:
                f = await asyncio.wait_for(facts.pair_facts(a, b), 40)
                nums = {"rows": facts.rows_for(f, a, b), "trade": f.get("trade")}
            except Exception:
                log.warning("facts for %s-%s unavailable", a, b)
                nums = {"rows": [], "trade": None}
        if nums is not None:
            nums["debts"] = await _mutual_debt(a, b)
    recent = db.rows("SELECT a, b, c, lang, created FROM pairs ORDER BY created DESC LIMIT 12")
    coords = {k: [round(v["lat"], 2), round(v["lon"], 2)] for k, v in analyze.COUNTRY_INFO.items()}
    payload = {
        "a": a,
        "b": b,
        "c": c,
        "coords": coords,
        "geo": {v["name"]: k for k, v in analyze.COUNTRY_INFO.items() if pairs.valid(k)},
        "names": {k: analyze.display_name(k) for k in pairs.SOVEREIGN if k in analyze.COUNTRY_INFO},
        "tiers": {k: pairs.tier(k) for k in pairs.SOVEREIGN},
    }
    return templates.TemplateResponse(
        request,
        "pair.html",
        {
            "a": a,
            "b": b,
            "c": c,
            "lang": lang,
            "error": error,
            "result": result,
            "spark": _spark(history),
            "nums": nums,
            "tg_bot": telegram_bot.enabled(),
            "recent": recent,
            "options": pairs.country_options(),
            "TIERS": pairs.TIERS,
            "TIER_SHORT": pairs.TIER_SHORT,
            "tier": pairs.tier,
            "name": analyze.display_name,
            "ai": analyze.ai_available(),
            "db_news": (
                await asyncio.to_thread(_pair_db_news, a, b)
                if a and b and not result and pairs.valid(a) and pairs.valid(b)
                else []
            ),
            "payload": Markup(json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")),
        },
    )


@app.post("/pair")
async def make_pair(a: str = Form(...), b: str = Form(...), c: str = Form(""), lang: str = Form("uz")):
    a, b, c = a.upper()[:3], b.upper()[:3], c.upper()[:3]
    lang = lang if lang in analyze.LANG_NAMES else "uz"
    back = f"/pair?a={a}&b={b}&lang={lang}" + (f"&c={c}" if c else "")
    if not (pairs.valid(a) and pairs.valid(b)) or a == b:
        return RedirectResponse(back + "&error=" + quote_plus("Ikki xil davlatni tanlang"), 303)
    if c and (not pairs.valid(c) or c in (a, b)):
        return RedirectResponse(back + "&error=" + quote_plus("Uchinchi davlat A va B dan farq qilishi kerak"), 303)
    if not analyze.ai_available():
        return RedirectResponse(
            back + "&error=" + quote_plus("AI tahlili hozircha mavjud emas, keyinroq urinib ko'ring"), 303
        )
    if _ai_lock.locked():  # say so before spending time on the news search
        return RedirectResponse(back + "&error=" + quote_plus(busy_message()), 303)
    try:
        await analyze_pair(a, b, lang, c)
    except analyze.AIError as e:
        log.warning("pair failed: %s", e)
        return RedirectResponse(back + "&error=" + quote_plus(str(e)), 303)
    except Exception as e:  # show something useful instead of a 500
        log.exception("pair failed")
        return RedirectResponse(back + "&error=" + quote_plus(f"AI xatosi: {type(e).__name__}"), 303)
    return RedirectResponse(back, 303)


# ------------------------------------------------------------------ debts


def _debt_report(iso: str, lang: str) -> dict | None:
    row = db.one("SELECT created, data FROM debt_reports WHERE key = ?", (f"{iso}-{lang}",))
    if not row:
        return None
    data = json.loads(row["data"])
    data["created"] = row["created"]
    return data


async def analyze_debt(iso: str, lang: str) -> dict:
    data = await debt.country_debt(iso)
    items = await debt.debt_news(iso)
    report = await run_ai(f"qarzlar: {analyze.display_name(iso)}", debt.write_report, iso, lang, data, items)
    with db.tx() as c:
        c.execute(
            "INSERT INTO debt_reports (key, iso, lang, created, data) VALUES (?,?,?,?,?) "
            "ON CONFLICT (key) DO UPDATE SET created = excluded.created, data = excluded.data",
            (f"{iso}-{lang}", iso, lang, collectors.now_iso(), analyze.dumps(report)),
        )
    return report


@app.get("/debt", response_class=HTMLResponse)
async def debt_page(request: Request, c: str = "", lang: str = "uz", error: str = ""):
    iso = c.upper()[:3]
    lang = lang if lang in analyze.LANG_NAMES else "uz"
    figures, report = None, None
    if pairs.valid(iso):
        try:
            figures = debt.view(await asyncio.wait_for(debt.country_debt(iso), 40))
        except Exception:
            log.exception("debt figures failed")
            error = error or "Jahon banki ma'lumotlarini hozir olib bo'lmadi, birozdan keyin qayta oching."
        report = await asyncio.to_thread(_debt_report, iso, lang)
    recent = db.rows("SELECT iso, lang, created FROM debt_reports ORDER BY created DESC LIMIT 12")
    return templates.TemplateResponse(
        request,
        "debt.html",
        {
            "iso": iso if pairs.valid(iso) else "",
            "lang": lang,
            "error": error,
            "f": figures,
            "r": report,
            "recent": recent,
            "options": pairs.country_options(),
            "TIERS": pairs.TIERS,
            "AREAS": dict(debt.AREAS),
            "name": analyze.display_name,
            "ai": analyze.ai_available(),
        },
    )


@app.post("/debt")
async def make_debt(c: str = Form(...), lang: str = Form("uz")):
    iso = c.upper()[:3]
    lang = lang if lang in analyze.LANG_NAMES else "uz"
    back = f"/debt?c={iso}&lang={lang}"
    if not pairs.valid(iso):
        return RedirectResponse("/debt?error=" + quote_plus("Davlatni tanlang"), 303)
    if not analyze.ai_available():
        return RedirectResponse(
            back + "&error=" + quote_plus("AI tahlili hozircha mavjud emas, keyinroq urinib ko'ring"), 303
        )
    if _ai_lock.locked():
        return RedirectResponse(back + "&error=" + quote_plus(busy_message()), 303)
    try:
        await analyze_debt(iso, lang)
    except analyze.AIError as e:
        log.warning("debt report failed: %s", e)
        return RedirectResponse(back + "&error=" + quote_plus(str(e)), 303)
    except Exception as e:  # show something useful instead of a 500
        log.exception("debt report failed")
        return RedirectResponse(back + "&error=" + quote_plus(f"AI xatosi: {type(e).__name__}"), 303)
    return RedirectResponse(back, 303)


# ------------------------------------------------------------------ sanctions


@app.get("/sanctions", response_class=HTMLResponse)
def sanctions_page(
    request: Request, q: str = "", c: str = "", lst: str = Query("", alias="list"), kind: str = "", error: str = ""
):
    iso = c.upper()[:3] if pairs.valid(c.upper()[:3]) else ""
    lst = lst if lst in sanctions.LISTS else ""
    q = q.strip()[:120]
    st = sanctions.stats()
    results = sanctions.search(q, iso, lst) if q else []
    country = None
    if iso and not q:
        country = {
            "stat": st.get(iso, {"total": 0, "lists": {}, "kinds": {}, "programs": []}),
            "rows": sanctions.for_country(iso, lst, kind),
            "years": sanctions.by_year(iso),
            "news": sanctions.news(iso),
        }
    top = sorted(st.items(), key=lambda kv: -kv[1]["total"])[:25]
    partners = [(p, st.get(p, {}).get("total", 0)) for p in sanctions.UZ_PARTNERS]
    return templates.TemplateResponse(
        request,
        "sanctions.html",
        {
            "q": q, "iso": iso, "lst": lst, "kind": kind if kind in sanctions.KINDS else "", "error": error,
            "results": results, "country": country, "top": top, "partners": partners,
            "uz": st.get("UZB", {}), "uz_rows": sanctions.for_country("UZB", limit=60) if not (q or iso) else [],
            "status": sanctions.status(), "totals": sanctions.totals(), "LISTS": sanctions.LISTS,
            "KINDS": sanctions.KINDS, "PROGRAMS": PROGRAM_LABELS, "options": pairs.country_options(),
            "TIERS": pairs.TIERS, "name": analyze.display_name, "match": sanctions.match_label,
        },
    )  # fmt: skip


# EU programme codes in the EU file, and a few OFAC ones, in words.
PROGRAM_LABELS = {
    "RUS": "Rossiya (Ukrainadagi urush)", "UKR": "Ukraina hududiy yaxlitligiga tahdid",
    "RUSDA": "Rossiya: beqarorlashtirish", "RUSHR": "Rossiya: inson huquqlari", "BLR": "Belarus", "IRN": "Eron",
    "IRQ": "Iroq", "PRK": "KXDR (Shimoliy Koreya)",
    "SYR": "Suriya", "LBY": "Liviya", "TERR": "Terrorizm", "TAQA": "ISHID / Al-Qoida", "AFG": "Afg'oniston",
    "VEN": "Venesuela", "MMR": "Myanma", "CYB": "Kiberhujumlar", "HR": "Inson huquqlari", "CHEM": "Kimyoviy qurol",
    "SDGT": "Xalqaro terrorizm", "SDNTK": "Narkotik savdosi", "IRGC": "Eron inqilobiy gvardiyasi",
    "RUSSIA-EO14024": "Rossiya (EO 14024)", "UKRAINE-EO13662": "Ukraina / Rossiya (EO 13662)",
    "CYBER2": "Kiberjinoyat", "GLOMAG": "Korrupsiya va inson huquqlari (Magnitskiy)", "NPWMD": "Ommaviy qirg'in quroli",
    "IRAN": "Eron", "DPRK3": "KXDR", "SYRIA": "Suriya", "CUBA": "Kuba", "BELARUS-EO14038": "Belarus",
    "ILLICIT-DRUGS-EO14059": "Narkotik savdosi", "TCO": "Transmilliy jinoiy guruhlar",
}  # fmt: skip


@app.post("/sanctions/refresh")
async def sanctions_refresh():
    if not state["sanctions"]:
        _keep(asyncio.create_task(refresh_sanctions()))
    return RedirectResponse("/sanctions", 303)


# ------------------------------------------------------------------ country risk index


@app.get("/risk", response_class=HTMLResponse)
async def risk_page(request: Request, c: str = "", tier: int = 0, error: str = ""):
    iso = c.upper()[:3]
    rows, meta = [], {}
    try:
        rows, meta = await asyncio.wait_for(risk.build(), 40)
    except Exception:
        log.exception("risk index failed")
        error = error or "Jahon banki ma'lumotlarini hozir olib bo'lmadi, birozdan keyin qayta oching."
    for r in rows:
        r["name"] = analyze.display_name(r["iso"])
        r["tier"] = pairs.tier(r["iso"])
    detail = next((r for r in rows if r["iso"] == iso), None)
    shown = [r for r in rows if not tier or r["tier"] == tier]
    payload = {
        "scores": {r["iso"]: [r["score"], r["color"], r["name"], r["band"]] for r in rows},
        "geo": {v["name"]: k for k, v in analyze.COUNTRY_INFO.items() if pairs.valid(k)},
        "selected": iso if detail else "",
    }
    return templates.TemplateResponse(
        request,
        "risk.html",
        {
            "rows": shown, "detail": detail, "meta": meta, "error": error, "tier": tier, "TIERS": pairs.TIERS,
            "risk_text": explain.risk_sentence(detail, detail["name"]) if detail else "",
            "COMPONENTS": risk.COMPONENTS, "BANDS": risk.BANDS, "scored": sum(r["score"] is not None for r in rows),
            "coords": Markup(json.dumps(COORDS)),
            "payload": Markup(json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")),
        },
    )  # fmt: skip


# ------------------------------------------------------------------ media framing


def _framing_row(fid: int | None) -> dict | None:
    row = db.one("SELECT * FROM framings WHERE id = ?", (fid,)) if fid else None
    row = row or (None if fid else db.one("SELECT * FROM framings ORDER BY id DESC LIMIT 1"))
    if not row:
        return None
    return {**row, "data": json.loads(row["data"])}


@app.get("/framing", response_class=HTMLResponse)
def framing_page(request: Request, id: int | None = None, error: str = ""):
    arts = _articles(72)
    return templates.TemplateResponse(
        request,
        "framing.html",
        {
            "current": _framing_row(id), "error": error, "stories": framing.stories(arts),
            "recent": db.rows("SELECT id, created, topic, lang FROM framings ORDER BY id DESC LIMIT 12"),
            "CAMPS": framing.CAMP_LABELS, "ai": analyze.ai_available(), "camp_count": _camp_count(arts),
        },
    )  # fmt: skip


def _camp_count(arts: list[dict]) -> list[tuple[str, int]]:
    from collections import Counter

    n = Counter(a["camp"] for a in framing.tag(arts))
    return [(k, n[k]) for k in framing.CAMP_LABELS if n[k]]


def _ids(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", text or "")][: framing.MAX_ITEMS * 2]


async def _framing_items(topic: str, ids: list[int]) -> list[dict]:
    if ids:
        marks = ",".join("?" * len(ids))
        return await asyncio.to_thread(db.rows, f"SELECT * FROM articles WHERE id IN ({marks})", ids)
    stored = [a for a in await asyncio.to_thread(_articles, KEEP_DAYS * 24, topic)][:200]
    fetched = await framing.search_topic(topic)
    return [*stored, *({**a, "id": -(i + 1)} for i, a in enumerate(fetched))]


@app.post("/framing")
async def make_framing(topic: str = Form(""), ids: str = Form(""), lang: str = Form("uz")):
    topic, lang = topic.strip()[:150], lang if lang in analyze.LANG_NAMES else "uz"
    id_list = _ids(ids)
    if not topic and not id_list:
        return RedirectResponse("/framing?error=" + quote_plus("Mavzu yozing yoki voqeani tanlang"), 303)
    if not analyze.ai_available():
        return RedirectResponse(
            "/framing?error=" + quote_plus("AI tahlili hozircha mavjud emas, keyinroq urinib ko'ring"), 303
        )
    if _ai_lock.locked():
        return RedirectResponse("/framing?error=" + quote_plus(busy_message()), 303)
    items = framing.pick(await _framing_items(topic, id_list))
    if len(framing.camps_in(items)) < framing.MIN_CAMPS:
        msg = (
            "Bu mavzuni kamida ikki xil OAV lageri yoritgani topilmadi. "
            "Boshqacha so'z bilan yozing (masalan inglizcha)."
        )
        return RedirectResponse("/framing?error=" + quote_plus(msg), 303)
    try:
        data = await run_ai("OAV taqqoslash", framing.compare, items, lang, topic)
    except analyze.AIError as e:
        return RedirectResponse("/framing?error=" + quote_plus(str(e)), 303)
    except Exception as e:
        log.exception("framing failed")
        return RedirectResponse("/framing?error=" + quote_plus(f"AI xatosi: {type(e).__name__}"), 303)
    title = topic or (items[0]["title"] if items else "")
    with db.tx() as c:
        row = c.execute(
            "INSERT INTO framings (created, lang, topic, model, data) VALUES (?,?,?,?,?) RETURNING id",
            (collectors.now_iso(), lang, title[:200], data["model"], analyze.dumps(data)),
        ).fetchone()
    return RedirectResponse(f"/framing?id={row['id']}", 303)


# ------------------------------------------------------------------ Telegram and scheduled jobs


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    given = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not telegram_bot.enabled() or not secrets.compare_digest(given, telegram_bot.webhook_secret()):
        return Response(status_code=403)
    try:
        update = await request.json()
    except ValueError:
        return Response(status_code=400)
    # a command touches the database and sends one or two messages: quick enough to answer inline
    await _safe_handle(update)
    return {"ok": True}


async def _safe_handle(update: dict) -> None:
    try:
        await telegram_bot.handle(update)
    except Exception:
        log.exception("telegram update failed")


async def job_digest(force: bool = False) -> dict:
    today = collectors.now_iso()[:10]
    if not force and db.one("SELECT id FROM digests WHERE created >= ?", (today,)):
        return {"skipped": "bugungi digest allaqachon yuborilgan"}
    await refresh()
    arts = await asyncio.to_thread(_articles, 24)
    try:
        body, model = await run_ai("kunlik digest", telegram_bot.build_digest, arts, analyze.ai_available())
    except analyze.AIError as e:  # busy or out of budget: headlines without AI are still worth sending
        log.warning("digest without AI: %s", e)
        body, model = await asyncio.to_thread(telegram_bot.build_digest, arts, False)
    with db.tx() as c:
        c.execute("INSERT INTO digests (created, body, model) VALUES (?,?,?)", (collectors.now_iso(), body, model))
    sent = await telegram_bot.broadcast(body) if telegram_bot.enabled() else 0
    personal = 0
    if telegram_bot.enabled():  # people who linked Telegram also get their own countries
        for u in await asyncio.to_thread(db.rows, "SELECT id, tg_chat FROM users WHERE tg_chat IS NOT NULL"):
            text = await asyncio.to_thread(watch.personal_digest, u["id"], arts)
            if text:
                personal += await telegram_bot.send(u["tg_chat"], text)
    return {"sent": sent, "personal": personal, "model": model}


async def job_weekly() -> dict:
    """Re-analyse the followed pairs whose analysis is oldest; alert followers when one moves."""
    follows = await asyncio.to_thread(
        db.rows,
        """SELECT f.a, f.b, f.lang, f.key, MAX(p.created) AS last
           FROM tg_follows f LEFT JOIN pairs p ON p.key = f.key
           GROUP BY f.a, f.b, f.lang, f.key ORDER BY last""",
    )
    seen, done, alerts, errors = set(), [], 0, []
    for f in follows:
        if f["key"] in seen or len(done) >= WEEKLY_PAIRS:
            continue
        seen.add(f["key"])
        try:
            new, old = await analyze_pair(f["a"], f["b"], f["lang"])
        except analyze.AIError as e:
            errors.append(f"{f['key']}: {e}")
            if "limiti tugadi" in str(e):
                break
            continue
        done.append(f["key"])
        if not telegram_bot.enabled():
            continue
        if old is None or telegram_bot.moved(new, old):
            text = (
                telegram_bot.alert_text(f["a"], f["b"], new, old)
                if old
                else "🆕 Birinchi tahlil tayyor:\n"
                + telegram_bot._pair_line(f["a"], f["b"], new)
                + "\n\n"
                + telegram_bot.pair_link(f["a"], f["b"])
            )
            for r in await asyncio.to_thread(db.rows, "SELECT chat_id FROM tg_follows WHERE key = ?", (f["key"],)):
                alerts += await telegram_bot.send(r["chat_id"], text)
    return {"analysed": done, "alerts": alerts, "errors": errors}


@app.api_route("/cron/{job}", methods=["GET", "POST"])
async def cron(job: str, request: Request, force: int = 0):
    given = request.headers.get("x-cron-token", "")
    if not CRON_TOKEN or not secrets.compare_digest(given, CRON_TOKEN):
        return Response(status_code=404)
    if job == "refresh":
        return {"added": await refresh()}
    if job == "digest":
        return await job_digest(bool(force))
    if job == "weekly":
        return await job_weekly()
    if job == "sanctions":
        return await refresh_sanctions()
    if job == "jobs":  # started in the background: the caller only needs to keep the service awake
        _keep(asyncio.create_task(run_due_jobs(force=bool(force))))
        return {"started": True}
    if job in JOBS:
        return {job: await JOBS[job]()}
    return Response(status_code=404)


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
            "INSERT INTO sources (kind, value, label, lang) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
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


@app.get("/api/layers")
async def api_layers(kind: str = "risk"):
    """Country colours for the globe: {ISO3: [value, colour, label]}."""
    if kind == "risk":
        try:
            rows, _ = await asyncio.wait_for(risk.build(), 30)
        except Exception:
            return JSONResponse({}, status_code=503)
        scored = [r for r in rows if r["score"] is not None]
        return {r["iso"]: [r["score"], r["color"], f"{r['score']} · {r['band']}"] for r in scored}
    if kind == "sanctions":
        st = await asyncio.to_thread(sanctions.stats)
        out = {}
        for iso, s in st.items():
            p = sanctions.risk_points(s["total"])
            _, color = risk.band(p)
            out[iso] = [s["total"], color, f"{s['total']} ta yozuv"]
        return out
    return JSONResponse({}, status_code=404)


@app.get("/api/articles")
def api_articles(hours: str = "24", q: str = "", kind: str = "", cat: str = "", country: str = ""):
    arts = _articles(_hours(hours), q, kind, cat, country)[:500]
    return JSONResponse([{k: v for k, v in a.items() if k != "emb"} for a in arts])


# ------------------------------------------------------------------ scheduled jobs


async def notify(user_id: int, kind: str, text: str) -> None:
    """An alert on the watch page, and in Telegram when the person linked it."""
    await asyncio.to_thread(watch.note, user_id, kind, text)
    if telegram_bot.enabled():
        u = await asyncio.to_thread(accounts.get, user_id) if user_id else None
        if u and u.get("tg_chat"):
            await telegram_bot.send(u["tg_chat"], telegram_bot.esc(text))


async def job_refresh() -> dict:
    return {"added": await refresh()}


async def job_automap() -> dict:
    """The day's AI map, so the globe shows tanks and markets without anyone pressing a button."""
    if not analyze.ai_available():
        return {"skipped": "AI kaliti yo'q"}
    if AI_DAILY_LIMIT - await asyncio.to_thread(ai_used_today) < 8:
        return {"skipped": "AI limiti tejalmoqda"}
    arts = await asyncio.to_thread(_articles, 48)
    if len(arts) < 30:
        return {"skipped": "yangilik kam"}
    result = await run_ai("kunlik xarita", analyze.write_links, arts, "uz", "")
    data = {"summary": result["summary"], "links": result["links"]}
    with db.tx() as c:
        row = c.execute(
            "INSERT INTO maps (created, lang, hours, focus, model, data, auto) VALUES (?,?,?,?,?,?,1) RETURNING id",
            (collectors.now_iso(), "uz", 48, "", result["model"], analyze.dumps(data)),
        ).fetchone()
    return {"map": row["id"], "links": len(result["links"])}


async def job_sanctions() -> dict:
    out = await refresh_sanctions()
    alerts = await asyncio.to_thread(watch.check_sanctions)
    for user_id, text in alerts:
        await notify(user_id, "sanksiya", text)
    return {"lists": out, "alerts": len(alerts)}


async def job_watch_risk() -> dict:
    if not await asyncio.to_thread(db.one, "SELECT 1 AS x FROM watch_items WHERE kind = 'country' LIMIT 1"):
        return {"skipped": "kuzatilayotgan davlat yo'q"}
    rows, _ = await risk.build()
    alerts = await asyncio.to_thread(watch.check_risk, rows)
    for user_id, text in alerts:
        await notify(user_id, "xavf", text)
    return {"alerts": len(alerts)}


PRED_PER_DAY = int(os.environ.get("WS_PRED_PER_DAY", "3"))


async def check_prediction(p: dict, lang: str = "uz") -> dict:
    fetched = await pairs.pair_news(p["a"], p["b"])
    stored = await asyncio.to_thread(_pair_db_news, p["a"], p["b"])
    items = [x for x in pairs.merge_news(stored, fetched, limit=40) if x["published"] >= p["made"]]
    verdict = await run_ai("bashorat tekshiruvi", predictions.judge, p, items, lang)
    await asyncio.to_thread(predictions.save, p["id"], verdict)
    return verdict


async def job_predictions() -> dict:
    await asyncio.to_thread(predictions.backfill)
    if not analyze.ai_available():
        return {"skipped": "AI kaliti yo'q"}
    done, errors = 0, []
    for p in await asyncio.to_thread(predictions.due, PRED_PER_DAY):
        try:
            await check_prediction(p)
            done += 1
        except analyze.AIError as e:
            errors.append(str(e))
            break
    return {"checked": done, "errors": errors}


def _top_groups(hours: int = 24, minimum: int = 2) -> list[list[dict]]:
    """The day's stories told by at least `minimum` different publishers, most-covered first."""
    groups = analyze.overview(_articles(hours))["groups"]
    return [g for g in groups if len({a["publisher"] for a in g}) >= minimum]


async def job_explain() -> dict:
    """A one-sentence plain-Uzbek explanation for each of the day's big stories."""
    if not analyze.ai_available():
        return {"skipped": "AI kaliti yo'q"}
    groups = (await asyncio.to_thread(_top_groups))[: explain.PER_RUN]
    done = await run_ai("oddiy izohlar", explain.write, groups)
    return {"explained": len(done)}


JOBS = {
    "refresh": job_refresh,
    "explain": job_explain,
    "automap": job_automap,
    "digest": lambda: job_digest(False),
    "weekly": job_weekly,
    "sanctions": job_sanctions,
    "watch_risk": job_watch_risk,
    "predictions": job_predictions,
}


def _job_wanted(name: str) -> bool:
    """A due job that has nothing to do here is marked done without running."""
    if name == "digest":
        return telegram_bot.enabled()
    if name == "weekly":
        return telegram_bot.enabled() and db.one("SELECT 1 AS x FROM tg_follows LIMIT 1") is not None
    if name == "sanctions":
        return SANCTIONS_AUTO and sanctions.stale(7)
    return True


async def run_due_jobs(force: bool = False) -> dict:
    """Run every due job once, one after another. Safe to call often: it returns at once when busy."""
    if state["jobs"]:
        return {"skipped": "boshqa ishlar bajarilmoqda"}
    results: dict = {}
    state["jobs"] = "boshlanmoqda"
    try:
        names = scheduler.ORDER if force else await asyncio.to_thread(scheduler.due)
        for name in names:
            state["jobs"] = name
            try:
                if await asyncio.to_thread(_job_wanted, name):
                    results[name] = await JOBS[name]()
                else:
                    results[name] = {"skipped": "kerak emas"}
            except analyze.AIError as e:
                results[name] = {"error": str(e)}
            except Exception as e:
                log.exception("job %s failed", name)
                results[name] = {"error": type(e).__name__}
            await asyncio.to_thread(scheduler.mark, name)
        if results:
            log.info("jobs: %s", {k: v for k, v in results.items() if v})
    finally:
        state["jobs"] = ""
    return results


# ------------------------------------------------------------------ accounts


def _uid(request: Request) -> int:
    u = getattr(request.state, "user", None)
    return u["id"] if u else 0


def _is_admin(request: Request) -> bool:
    u = getattr(request.state, "user", None)
    return (u and u["role"] == "admin") or getattr(request.state, "mode", "open") == "open"


def _https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def _signed_in(request: Request, user: dict, to: str) -> Response:
    token = accounts.start_session(user["id"])
    resp = RedirectResponse(to if to.startswith("/") and not to.startswith("//") else "/", 303)
    resp.set_cookie(
        accounts.COOKIE, token, max_age=accounts.SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=_https(request), path="/",
    )  # fmt: skip
    return resp


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", error: str = ""):
    mode = accounts.mode(PASSWORD)
    if mode == "accounts" and accounts.session_user(request.cookies.get(accounts.COOKIE)):
        return RedirectResponse("/", 303)
    return templates.TemplateResponse(request, "login.html", {"mode": mode, "next": next, "error": error})


@app.post("/login")
def login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
    setup: str = Form(""),
    next: str = Form("/"),
):
    mode = accounts.mode(PASSWORD)
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    back = "/login?next=" + quote_plus(next) + "&error="
    if not accounts.allowed(ip):
        return RedirectResponse(back + quote_plus("Juda ko'p urinish. 10 daqiqadan keyin qayta urinib ko'ring."), 303)
    if mode in ("setup", "open"):  # the first account: whoever knows the setup password becomes admin
        if mode == "setup" and not secrets.compare_digest(setup.encode(), PASSWORD.encode()):
            accounts.failed(ip)
            return RedirectResponse(back + quote_plus("Sozlash paroli noto'g'ri"), 303)
        if password != password2:
            return RedirectResponse(back + quote_plus("Ikki parol bir xil emas"), 303)
        try:
            user = accounts.create(username, password, role="admin")
        except accounts.AccountError as e:
            return RedirectResponse(back + quote_plus(str(e)), 303)
        accounts.forget(ip)
        log.info("first administrator created: %s", user["username"])
        return _signed_in(request, user, next)
    user = accounts.authenticate(username, password)
    if not user:
        accounts.failed(ip)
        return RedirectResponse(back + quote_plus("Login yoki parol noto'g'ri"), 303)
    accounts.forget(ip)
    return _signed_in(request, user, next)


@app.post("/logout")
def logout(request: Request):
    accounts.end_session(request.cookies.get(accounts.COOKIE))
    resp = RedirectResponse("/login", 303)
    resp.delete_cookie(accounts.COOKIE, path="/")
    return resp


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, msg: str = "", error: str = "", code: str = ""):
    user = request.state.user
    full = accounts.get(user["id"]) if user else None
    return templates.TemplateResponse(
        request,
        "account.html",
        {"u": full, "msg": msg, "error": error, "code": code, "bot": telegram_bot.enabled(),
         "bot_name": telegram_bot.username()},
    )  # fmt: skip


@app.post("/account/password")
def change_password(request: Request, old: str = Form(""), new: str = Form(""), new2: str = Form("")):
    user = request.state.user
    if not user:
        return RedirectResponse("/account?error=" + quote_plus("Avval tizimga kiring"), 303)
    if not accounts.authenticate(user["username"], old):
        return RedirectResponse("/account?error=" + quote_plus("Hozirgi parol noto'g'ri"), 303)
    if new != new2:
        return RedirectResponse("/account?error=" + quote_plus("Ikki yangi parol bir xil emas"), 303)
    try:
        accounts.set_password(user["id"], new)
    except accounts.AccountError as e:
        return RedirectResponse("/account?error=" + quote_plus(str(e)), 303)
    return _signed_in(request, user, "/account?msg=" + quote_plus("Parol o'zgardi"))


@app.post("/account/telegram")
def telegram_code(request: Request, action: str = Form("link")):
    user = request.state.user
    if not user:
        return RedirectResponse("/account?error=" + quote_plus("Avval tizimga kiring"), 303)
    if action == "unlink":
        accounts.unlink_chat(user["id"])
        return RedirectResponse("/account?msg=" + quote_plus("Telegram uzildi"), 303)
    return RedirectResponse("/account?code=" + accounts.link_code(user["id"]), 303)


def _admin_ctx(request: Request, **extra) -> dict:
    last = scheduler.last_runs()
    return {
        "users": accounts.list_users(), "me": request.state.user, "jobs": last, "EVERY": scheduler.EVERY,
        "embed": embed.status(), "sanc": sanctions.status(), "db_desc": db.describe(),
        "ai_used": ai_used_today(), "preds": predictions.stats(), **extra,
    }  # fmt: skip


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, msg: str = "", error: str = ""):
    if not _is_admin(request):
        return Response("Faqat administrator uchun", 403)
    return templates.TemplateResponse(request, "admin.html", _admin_ctx(request, msg=msg, error=error))


@app.post("/admin/users", response_class=HTMLResponse)
def admin_add_user(request: Request, username: str = Form(""), role: str = Form("user")):
    if not _is_admin(request):
        return Response("Faqat administrator uchun", 403)
    pw = accounts.temp_password()
    try:
        u = accounts.create(username, pw, role)
    except accounts.AccountError as e:
        return RedirectResponse("/admin?error=" + quote_plus(str(e)), 303)
    # shown once, on this page only; the person changes it on their account page
    return templates.TemplateResponse(request, "admin.html", _admin_ctx(request, created=(u["username"], pw)))


@app.post("/admin/users/{uid}/reset", response_class=HTMLResponse)
def admin_reset(request: Request, uid: int):
    if not _is_admin(request):
        return Response("Faqat administrator uchun", 403)
    u = accounts.get(uid)
    if not u:
        return RedirectResponse("/admin?error=" + quote_plus("Topilmadi"), 303)
    pw = accounts.temp_password()
    accounts.set_password(uid, pw)
    return templates.TemplateResponse(request, "admin.html", _admin_ctx(request, created=(u["username"], pw)))


@app.post("/admin/users/{uid}/delete")
def admin_delete(request: Request, uid: int):
    if not _is_admin(request):
        return Response("Faqat administrator uchun", 403)
    me = request.state.user
    u = accounts.get(uid)
    if not u:
        return RedirectResponse("/admin?error=" + quote_plus("Topilmadi"), 303)
    if me and me["id"] == uid:
        return RedirectResponse("/admin?error=" + quote_plus("O'zingizni o'chira olmaysiz"), 303)
    if u["role"] == "admin" and accounts.admins() <= 1:
        return RedirectResponse("/admin?error=" + quote_plus("Oxirgi administratorni o'chirib bo'lmaydi"), 303)
    accounts.delete(uid)
    return RedirectResponse("/admin?msg=" + quote_plus(f"{u['username']} o'chirildi"), 303)


@app.post("/admin/jobs")
async def admin_jobs(request: Request, job: str = Form("")):
    if not _is_admin(request):
        return Response("Faqat administrator uchun", 403)
    if job not in JOBS:
        return RedirectResponse("/admin?error=" + quote_plus("Noma'lum ish"), 303)
    _keep(asyncio.create_task(_run_one(job)))
    return RedirectResponse("/admin?msg=" + quote_plus(f"«{job}» fonda boshlandi"), 303)


async def _run_one(job: str) -> None:
    try:
        log.info("job %s by hand: %s", job, await JOBS[job]())
        await asyncio.to_thread(scheduler.mark, job)
    except Exception:
        log.exception("job %s failed", job)


BACKUP_TABLES = ["users", "watch_items", "alerts", "pairs", "pair_history", "maps", "briefs", "debt_reports",
                 "framings", "predictions", "digests", "tg_subscribers", "tg_follows", "sources", "meta"]  # fmt: skip


@app.get("/admin/export")
def admin_export(request: Request):
    """Everything people made or chose (not the news or the lists, which are fetched again), as JSON:
    keep it before the free database expires and load it into the new one with /admin/import."""
    if not _is_admin(request):
        return Response("Faqat administrator uchun", 403)
    dump = {"version": 1, "made": collectors.now_iso(), "tables": {}}
    for t in BACKUP_TABLES:
        dump["tables"][t] = db.rows(f"SELECT * FROM {t}")
    name = f"worldscope-zaxira-{collectors.now_iso()[:10]}.json"
    return Response(
        json.dumps(dump, ensure_ascii=False, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post("/admin/import")
async def admin_import(request: Request, file: UploadFile = File(...)):  # noqa: B008 (FastAPI's own idiom)
    if not _is_admin(request):
        return Response("Faqat administrator uchun", 403)
    try:
        dump = json.loads(await file.read())
        tables = dump["tables"]
    except (ValueError, KeyError, TypeError):
        return RedirectResponse("/admin?error=" + quote_plus("Fayl zaxira emas"), 303)
    added = await asyncio.to_thread(_import_tables, tables)
    accounts._exists_cache["yes"] = False
    return RedirectResponse("/admin?msg=" + quote_plus(f"Tiklandi: {added} ta yozuv"), 303)


def _import_tables(tables: dict) -> int:
    added = 0
    with db.tx() as c:
        for t in BACKUP_TABLES:
            rows = tables.get(t) or []
            if not rows:
                continue
            cols = [k for k in rows[0] if re.fullmatch(r"[a-z_]+", k) and k != "id"]
            sql = f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) ON CONFLICT DO NOTHING"
            for r in rows:
                cur = c.execute(sql, [r.get(k) for k in cols])
                added += cur.rowcount or 0
    return added


# ------------------------------------------------------------------ watch list


@app.get("/watch", response_class=HTMLResponse)
async def watch_page(request: Request, msg: str = "", error: str = ""):
    uid = _uid(request)
    have_country = await asyncio.to_thread(
        db.one, "SELECT 1 AS x FROM watch_items WHERE user_id = ? AND kind = 'country' LIMIT 1", (uid,)
    )
    rows = []
    if have_country:
        try:
            rows, _ = await asyncio.wait_for(risk.build(), 25)
        except Exception:
            log.warning("risk index unavailable for the watch page")
    items = await asyncio.to_thread(watch.view, uid, rows, sanctions.stats())
    return templates.TemplateResponse(
        request,
        "watch.html",
        {
            "items": items, "alerts": watch.alerts(uid), "msg": msg, "error": error,
            "options": pairs.country_options(), "TIERS": pairs.TIERS, "name": analyze.display_name,
            "linked": bool(request.state.user and request.state.user.get("tg_chat")),
            "signed": bool(request.state.user), "lists": sanctions.loaded(), "match": sanctions.match_label,
        },
    )  # fmt: skip


@app.post("/watch/add")
def watch_add(request: Request, kind: str = Form(...), value: str = Form("")):
    try:
        watch.add(_uid(request), kind, value)
    except watch.WatchError as e:
        return RedirectResponse("/watch?error=" + quote_plus(str(e)), 303)
    return RedirectResponse("/watch?msg=" + quote_plus("Qo'shildi"), 303)


@app.post("/watch/{item_id}/delete")
def watch_delete(request: Request, item_id: int):
    watch.remove(_uid(request), item_id)
    return RedirectResponse("/watch", 303)


# ------------------------------------------------------------------ one country


async def _country_facts(iso: str) -> dict:
    cached = await asyncio.to_thread(db.cache_get, f"wb:{iso}", facts.CACHE_DAYS)
    if cached is not None:
        return cached
    async with httpx.AsyncClient(timeout=20) as client:
        got = (await facts._world_bank(client, [iso])).get(iso, {})
    if got:
        await asyncio.to_thread(db.cache_put, f"wb:{iso}", got)
    return got


async def _soon(coro, seconds: float):
    """The result if it arrives in time; otherwise None, and the work goes on to fill the cache."""
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.wait_for(asyncio.shield(task), seconds)
    except TimeoutError:
        _keep(task)
        return None
    except Exception:
        log.exception("country page part failed")
        return None


@app.get("/country", response_class=HTMLResponse)
@app.get("/country/{iso}", response_class=HTMLResponse)
async def country_page(request: Request, iso: str = ""):
    iso = iso.upper()[:3]
    if not pairs.valid(iso):
        return templates.TemplateResponse(
            request, "country.html", {"iso": "", "options": pairs.country_options(), "TIERS": pairs.TIERS}
        )
    wb, rk, dt, tr = await asyncio.gather(
        _soon(_country_facts(iso), 8),
        _soon(risk.build(), 12),
        _soon(debt.country_debt(iso), 8),
        _soon(trade.flows(iso), 8),
    )
    r = next((x for x in (rk[0] if rk else []) if x["iso"] == iso), None)
    arts = await asyncio.to_thread(_articles, KEEP_DAYS * 24, "", "", "", iso)
    groups = analyze.cluster(arts)[:12]
    latest = await asyncio.to_thread(db.one, "SELECT data, created FROM maps ORDER BY id DESC LIMIT 1")
    links = []
    if latest:
        links = [x for x in json.loads(latest["data"])["links"] if iso in (x.get("from"), x.get("to"))]
    pair_rows = await asyncio.to_thread(
        db.rows, "SELECT a, b, c, lang, created, data FROM pairs WHERE a = ? OR b = ? ORDER BY created DESC LIMIT 12",
        (iso, iso),
    )  # fmt: skip
    pair_list = []
    for p in pair_rows:
        d = json.loads(p["data"])
        other = p["b"] if p["a"] == iso else p["a"]
        pair_list.append({"other": other, "score": d.get("score", 0), "label": d.get("label", ""),
                          "lang": p["lang"], "c": p["c"], "created": p["created"]})  # fmt: skip
    camps = {}
    for a in framing.tag(arts):
        camps[a["camp"]] = camps.get(a["camp"], 0) + 1
    preds = await asyncio.to_thread(
        db.rows, "SELECT * FROM predictions WHERE a = ? OR b = ? ORDER BY due LIMIT 12", (iso, iso)
    )
    watched = await asyncio.to_thread(
        db.one, "SELECT id FROM watch_items WHERE user_id = ? AND kind = 'country' AND value = ?", (_uid(request), iso)
    )
    debt_rows = debt.view(dt)["debt_rows"][:3] if dt else []
    payload = {
        "geo": {v["name"]: k for k, v in analyze.COUNTRY_INFO.items()},
        "links": links, "coords": COORDS, "focus": iso,
        "names": {k: analyze.display_name(k) for k in analyze.COUNTRY_INFO},
    }  # fmt: skip
    return templates.TemplateResponse(
        request,
        "country.html",
        {
            "iso": iso, "name": analyze.display_name, "en": analyze.COUNTRY_INFO[iso]["name"], "tier": pairs.tier(iso),
            "flag": "".join(chr(0x1F1E6 + ord(ch) - 65) for ch in debt.ISO2.get(iso, "")),
            "risk_text": explain.risk_sentence(r, analyze.display_name(iso)), "stale": explain.stale,
            "TIER_SHORT": pairs.TIER_SHORT, "wb": wb, "INDICATORS": facts.INDICATORS, "human": facts.human,
            "r": r, "debt_rows": debt_rows, "debt_wait": dt is None, "sanc": sanctions.stats().get(iso),
            "flows": tr, "trade_top": trade.top, "groups": groups, "links": links, "pairs_list": pair_list,
            "camps": sorted(camps.items(), key=lambda kv: -kv[1]), "CAMPS": framing.CAMP_LABELS, "preds": preds,
            "STATUS": predictions.STATUS_UZ, "watched": watched, "TYPES": analyze.LINK_TYPES,
            "payload": Markup(json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")),
        },
    )  # fmt: skip


# ------------------------------------------------------------------ forecasts


@app.get("/predictions", response_class=HTMLResponse)
def predictions_page(request: Request, show: str = "all", msg: str = "", error: str = ""):
    predictions.backfill()
    return templates.TemplateResponse(
        request,
        "predictions.html",
        {
            "st": predictions.stats(), "rows": predictions.all_rows(show if show in ("open", "done") else ""),
            "show": show, "STATUS": predictions.STATUS_UZ, "name": analyze.display_name, "msg": msg, "error": error,
            "ai": analyze.ai_available(), "now": collectors.now_iso(), "per_day": PRED_PER_DAY,
        },
    )  # fmt: skip


@app.post("/predictions/{pid}/check")
async def predictions_check(request: Request, pid: int):
    p = await asyncio.to_thread(db.one, "SELECT * FROM predictions WHERE id = ?", (pid,))
    if not p or p["status"] != "open":
        return RedirectResponse("/predictions?error=" + quote_plus("Bu bashorat ochiq emas"), 303)
    if p["due"] > collectors.now_iso():
        return RedirectResponse("/predictions?error=" + quote_plus("Muddati hali kelmagan"), 303)
    if not analyze.ai_available():
        return RedirectResponse(
            "/predictions?error=" + quote_plus("AI tahlili hozircha mavjud emas, keyinroq urinib ko'ring"), 303
        )
    try:
        v = await check_prediction(p)
    except analyze.AIError as e:
        return RedirectResponse("/predictions?error=" + quote_plus(str(e)), 303)
    done = predictions.STATUS_UZ[v["outcome"]]
    return RedirectResponse("/predictions?msg=" + quote_plus(f"Tekshirildi: {done}"), 303)


# ------------------------------------------------------------------ data for the globe


@app.get("/api/trade/{iso}")
async def api_trade(iso: str):
    iso = iso.upper()[:3]
    if not pairs.valid(iso):
        return JSONResponse({"error": "davlat"}, status_code=404)
    try:
        return await asyncio.wait_for(trade.flows(iso), 40)
    except Exception:
        log.exception("trade flows failed")
        return JSONResponse({"iso": iso, "year": None, "error": "Comtrade javob bermadi"}, status_code=503)


@app.get("/api/maps")
def api_maps():
    return db.rows("SELECT id, created, auto FROM maps ORDER BY created, id")
