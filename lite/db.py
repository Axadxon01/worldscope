"""Storage for WorldScope Lite: PostgreSQL when DATABASE_URL is set, otherwise one SQLite file.

The app writes SQL once, in the dialect both understand (qmark parameters, ON CONFLICT, RETURNING);
this module adapts it for Postgres. On Postgres the tables live in their own schema ("lite" by
default), so the database can be shared with other apps without touching their tables.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
PG_SCHEMA = re.sub(r"\W", "", os.environ.get("WS_DB_SCHEMA", "lite")) or "lite"
DB_PATH = Path(os.environ.get("WS_DB_PATH", Path(__file__).parent / "data" / "worldscope.db"))
IS_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,          -- rss | gnews | telegram | gdelt
    value       TEXT NOT NULL,          -- feed URL, search query, channel name or GDELT query
    label       TEXT NOT NULL,
    lang        TEXT NOT NULL DEFAULT 'en',
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_ok     TEXT,
    last_error  TEXT,
    last_count  INTEGER,
    UNIQUE (kind, value, lang)
);
CREATE TABLE IF NOT EXISTS articles (
    id          INTEGER PRIMARY KEY,
    url         TEXT NOT NULL UNIQUE,
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    publisher   TEXT NOT NULL DEFAULT '',
    published   TEXT NOT NULL,           -- ISO 8601 UTC
    fetched     TEXT NOT NULL,
    source_id   INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    kind        TEXT NOT NULL,
    lang        TEXT NOT NULL DEFAULT '',
    categories  TEXT NOT NULL DEFAULT '[]',   -- JSON list of slugs
    countries   TEXT NOT NULL DEFAULT '[]',   -- JSON list of ISO3 codes
    cluster     INTEGER,
    title_key   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS articles_published ON articles (published DESC);
CREATE INDEX IF NOT EXISTS articles_title_key ON articles (title_key);
CREATE TABLE IF NOT EXISTS maps (
    id          INTEGER PRIMARY KEY,
    created     TEXT NOT NULL,
    lang        TEXT NOT NULL,
    hours       INTEGER NOT NULL,
    focus       TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL,
    data        TEXT NOT NULL           -- JSON {summary, links}
);
CREATE TABLE IF NOT EXISTS pairs (
    key         TEXT PRIMARY KEY,       -- "AAA-BBB-lang", codes sorted, "~CCC" for a triangle
    a           TEXT NOT NULL,
    b           TEXT NOT NULL,
    c           TEXT NOT NULL DEFAULT '',
    lang        TEXT NOT NULL,
    created     TEXT NOT NULL,
    data        TEXT NOT NULL           -- JSON analysis
);
CREATE TABLE IF NOT EXISTS briefs (
    id          INTEGER PRIMARY KEY,
    created     TEXT NOT NULL,
    lang        TEXT NOT NULL,
    hours       INTEGER NOT NULL,
    focus       TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL,
    body        TEXT NOT NULL,          -- markdown with [n] citations
    cites       TEXT NOT NULL           -- JSON list of {n, article_id, title, url, publisher}
);
CREATE TABLE IF NOT EXISTS pair_history (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL,
    created     TEXT NOT NULL,
    score       INTEGER NOT NULL,
    label       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS pair_history_key ON pair_history (key, created);
CREATE TABLE IF NOT EXISTS tg_subscribers (
    chat_id     TEXT PRIMARY KEY,
    created     TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    name        TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tg_follows (
    chat_id     TEXT NOT NULL,
    key         TEXT NOT NULL,          -- pairs.key(a, b, lang)
    a           TEXT NOT NULL,
    b           TEXT NOT NULL,
    lang        TEXT NOT NULL,
    created     TEXT NOT NULL,
    PRIMARY KEY (chat_id, key)
);
CREATE TABLE IF NOT EXISTS digests (
    id          INTEGER PRIMARY KEY,
    created     TEXT NOT NULL,
    body        TEXT NOT NULL,          -- Telegram HTML
    model       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS debt_reports (
    key         TEXT PRIMARY KEY,       -- "ISO-lang"
    iso         TEXT NOT NULL,
    lang        TEXT NOT NULL,
    created     TEXT NOT NULL,
    data        TEXT NOT NULL           -- JSON report
);
CREATE TABLE IF NOT EXISTS meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cache (
    key         TEXT PRIMARY KEY,       -- e.g. "wb:UZB" or "trade:AUS-UZB"
    fetched     TEXT NOT NULL,
    data        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sanctions (
    id          INTEGER PRIMARY KEY,
    list        TEXT NOT NULL,          -- OFAC | UN | EU | UK
    uid         TEXT NOT NULL,          -- the publisher's own id
    kind        TEXT NOT NULL,          -- person | entity | vessel | aircraft
    name        TEXT NOT NULL,
    aliases     TEXT NOT NULL DEFAULT '',   -- "; "-separated
    countries   TEXT NOT NULL DEFAULT '',   -- " RUS IRN " (padded, for LIKE '% RUS %')
    programs    TEXT NOT NULL DEFAULT '',
    listed      TEXT NOT NULL DEFAULT '',   -- YYYY-MM-DD where the list gives it
    search      TEXT NOT NULL DEFAULT '',   -- normalised names, " | "-separated
    UNIQUE (list, uid)
);
CREATE TABLE IF NOT EXISTS framings (
    id          INTEGER PRIMARY KEY,
    created     TEXT NOT NULL,
    lang        TEXT NOT NULL,
    topic       TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL,
    data        TEXT NOT NULL           -- JSON {story, facts, camps, items, ...}
);
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,
    username    TEXT NOT NULL UNIQUE,
    pw          TEXT NOT NULL,          -- pbkdf2$rounds$salt$hash
    role        TEXT NOT NULL DEFAULT 'user',   -- admin | user
    created     TEXT NOT NULL,
    last_login  TEXT,
    tg_chat     TEXT,                   -- Telegram chat linked with /bogla
    tg_code     TEXT,
    tg_code_exp TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,       -- sha256 of the cookie value, never the value itself
    user_id     INTEGER NOT NULL,
    created     TEXT NOT NULL,
    expires     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watch_items (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,       -- 0 = the open site with no accounts
    kind        TEXT NOT NULL,          -- country | company
    value       TEXT NOT NULL,          -- ISO3 or a name as typed
    created     TEXT NOT NULL,
    state       TEXT NOT NULL DEFAULT '{}',   -- JSON: known matches, last score
    UNIQUE (user_id, kind, value)
);
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    created     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_user ON alerts (user_id, created);
CREATE TABLE IF NOT EXISTS predictions (
    id          INTEGER PRIMARY KEY,
    ref         TEXT NOT NULL,          -- the pair key the scenario came from
    a           TEXT NOT NULL,
    b           TEXT NOT NULL,
    title       TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    chance      INTEGER NOT NULL,       -- 0..100, as the analysis gave it
    made        TEXT NOT NULL,
    due         TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',   -- open | happened | not_happened | partly | unclear
    checked     TEXT,
    verdict     TEXT NOT NULL DEFAULT '',
    evidence    TEXT NOT NULL DEFAULT '[]',
    UNIQUE (ref, made, title)
);
CREATE TABLE IF NOT EXISTS ai_usage (
    day         TEXT PRIMARY KEY,       -- YYYY-MM-DD, UTC
    calls       INTEGER NOT NULL DEFAULT 0
);
"""


MIGRATIONS = [
    "ALTER TABLE pairs ADD COLUMN c TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE articles ADD COLUMN emb TEXT",  # JSON meaning vector of the headline (embed.py)
    "ALTER TABLE maps ADD COLUMN auto INTEGER NOT NULL DEFAULT 0",  # 1 = made by the daily schedule
]


# ------------------------------------------------------------------ connections


def _pg_sql(sql: str) -> str:
    """qmark → psycopg placeholders, and case-insensitive LIKE as SQLite has it."""
    return re.sub(r"\bLIKE\b", "ILIKE", sql.replace("%", "%%").replace("?", "%s"))


class _PgConn:
    """Just enough of the sqlite3 connection API for this app, on top of psycopg."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params=()):
        return self._conn.execute(_pg_sql(sql), tuple(params))

    def executescript(self, script: str):
        for stmt in (s.strip() for s in script.split(";")):
            if stmt:
                self._conn.execute(stmt)

    def executemany(self, sql: str, seq):
        with self._conn.cursor() as cur:
            cur.executemany(_pg_sql(sql), [tuple(p) for p in seq])

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def usable(self) -> bool:
        return not self._conn.closed and not self._conn.broken

    def close(self):
        self._conn.close()


def connect():
    if IS_PG:
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
            connect_timeout=15,
            options=f"-c search_path={PG_SCHEMA}",
            keepalives=1,
            keepalives_idle=30,
        )
        return _PgConn(conn)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# One connection, reused: every query already waits for _lock, and opening a PostgreSQL connection
# per query (TLS, auth) cost more than the query itself.
_conn = None
_last_used = 0.0
IDLE_PING = 30  # seconds; a connection idle longer is checked before use


def _usable(conn) -> bool:
    if conn is None:
        return False
    if IS_PG:
        if not conn.usable():
            return False
        if time.monotonic() - _last_used > IDLE_PING:
            try:  # the server may have closed an idle connection; find out before the real query
                conn.execute("SELECT 1").fetchone()
                conn.commit()
            except Exception:
                return False
    return True


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            with contextlib.suppress(Exception):
                _conn.close()
        _conn = None


@contextmanager
def tx():
    global _conn, _last_used
    with _lock:
        if not _usable(_conn):
            if _conn is not None:
                with contextlib.suppress(Exception):
                    _conn.close()
            _conn = connect()
        conn = _conn
        try:
            yield conn
            conn.commit()
        except BaseException:
            try:
                conn.rollback()
            except Exception:  # the connection itself broke: open a new one next time
                with contextlib.suppress(Exception):
                    conn.close()
                _conn = None
            raise
        finally:
            _last_used = time.monotonic()


RETIRED = ["https://www.arabnews.com/rss.xml"]  # answers 403 to servers


def init(default_sources: list[dict]) -> None:
    script = SCHEMA
    if IS_PG:
        script = script.replace("id          INTEGER PRIMARY KEY", "id          BIGSERIAL PRIMARY KEY")
        script = re.sub(r"--[^\n]*", "", script)
        with tx() as c:
            c.execute(f"CREATE SCHEMA IF NOT EXISTS {PG_SCHEMA}")
    with tx() as c:
        c.executescript(script)
    for stmt in MIGRATIONS:  # columns added after a table existed; each may already be there
        try:
            with tx() as c:
                c.execute(stmt)
        except Exception:  # "duplicate column" is worded differently by SQLite and Postgres
            pass
    with tx() as c:
        # Links must be web addresses; drop anything older code may have stored (e.g. javascript:).
        c.execute("DELETE FROM articles WHERE url NOT LIKE 'http://%' AND url NOT LIKE 'https://%'")
        # Add default sources this database has never been given. One the user deleted stays deleted.
        row = c.execute("SELECT value FROM meta WHERE key = 'seeded'").fetchone()
        seeded = set(json.loads(row["value"])) if row else set()
        if not row and c.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"] > 0:
            # a database from before this bookkeeping: everything already there counts as seeded
            seeded = {f"{r['kind']}|{r['value']}" for r in c.execute("SELECT kind, value FROM sources").fetchall()}
        for s in default_sources:
            k = f"{s['kind']}|{s['value']}"
            if k in seeded:
                continue
            c.execute(
                "INSERT INTO sources (kind, value, label, lang, enabled) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                (s["kind"], s["value"], s["label"], s.get("lang", "en"), 1 if s.get("enabled", True) else 0),
            )
            seeded.add(k)
        c.execute(
            "INSERT INTO meta (key, value) VALUES ('seeded', ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (json.dumps(sorted(seeded)),),
        )
        # defaults that turned out to refuse us: switched off once, the user can switch them back on
        for value in RETIRED:
            if c.execute("SELECT 1 FROM meta WHERE key = ?", (f"retired:{value}",)).fetchone():
                continue
            c.execute("UPDATE sources SET enabled = 0 WHERE value = ?", (value,))
            c.execute("INSERT INTO meta (key, value) VALUES (?, '1') ON CONFLICT DO NOTHING", (f"retired:{value}",))


def rows(sql: str, params: tuple | list = ()) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def one(sql: str, params: tuple | list = ()) -> dict | None:
    r = rows(sql, params)
    return r[0] if r else None


def describe() -> str:
    """Where the data lives, for the status line (no credentials)."""
    if IS_PG:
        host = re.sub(r"^.*@", "", DATABASE_URL).split("/")[0].split(":")[0]
        return f"PostgreSQL ({host.split('.')[0]}, sxema {PG_SCHEMA})"
    return "SQLite (vaqtinchalik fayl)"


def loads(value: str | None) -> list:
    try:
        return json.loads(value or "[]")
    except ValueError:
        return []


def cache_get(key: str, max_age_days: float) -> dict | list | None:
    from datetime import UTC, datetime, timedelta

    row = one("SELECT fetched, data FROM cache WHERE key = ?", (key,))
    if not row:
        return None
    if datetime.fromisoformat(row["fetched"]) < datetime.now(UTC) - timedelta(days=max_age_days):
        return None
    return json.loads(row["data"])


def cache_put(key: str, data) -> None:
    from datetime import UTC, datetime

    with tx() as c:
        c.execute(
            "INSERT INTO cache (key, fetched, data) VALUES (?,?,?) "
            "ON CONFLICT (key) DO UPDATE SET fetched = excluded.fetched, data = excluded.data",
            (key, datetime.now(UTC).replace(microsecond=0).isoformat(), json.dumps(data, ensure_ascii=False)),
        )
