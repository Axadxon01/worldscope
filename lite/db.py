"""SQLite storage for WorldScope Lite. One file, no server."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("WS_DB_PATH", Path(__file__).parent / "data" / "worldscope.db"))
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
    key         TEXT PRIMARY KEY,       -- "AAA-BBB-lang", codes sorted
    a           TEXT NOT NULL,
    b           TEXT NOT NULL,
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
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def tx():
    with _lock:
        conn = connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init(default_sources: list[dict]) -> None:
    with tx() as c:
        c.executescript(SCHEMA)
        if c.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == 0:
            for s in default_sources:
                c.execute(
                    "INSERT OR IGNORE INTO sources (kind, value, label, lang) VALUES (?,?,?,?)",
                    (s["kind"], s["value"], s["label"], s.get("lang", "en")),
                )


def rows(sql: str, params: tuple | list = ()) -> list[dict]:
    with tx() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def one(sql: str, params: tuple | list = ()) -> dict | None:
    r = rows(sql, params)
    return r[0] if r else None


def loads(value: str | None) -> list:
    try:
        return json.loads(value or "[]")
    except ValueError:
        return []
