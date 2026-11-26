"""Meaning vectors for headlines, so one story told in Uzbek, Russian and English becomes one group.

Uses Gemini's embedding model (its own free quota: 100 headlines a minute, separate from the text
models). Measured on a day of real headlines with taskType SEMANTIC_SIMILARITY: the same story across
languages scored 0.925-0.97; the closest different stories 0.912 ("Uzbekistan studies Malaysia's
Islamic finance" vs "... Malaysia's pensions"). Groups merge at 0.92, and only when the headlines
also agree on countries and on any numbers they quote. Off when there is no GEMINI_API_KEY or WS_EMBED=0.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta

import httpx

log = logging.getLogger("worldscope.embed")

MODEL = os.environ.get("WS_EMBED_MODEL", "models/gemini-embedding-001")
DIM = 256
THRESHOLD = float(os.environ.get("WS_EMBED_MERGE", "0.92"))
API = "https://generativelanguage.googleapis.com/v1beta"
# The free tier counts every headline as a request: about 1000 a day and 100 a minute. Spend at most
# this many a day (by Google's day, which starts at midnight Pacific time), newest headlines first.
DAILY = int(os.environ.get("WS_EMBED_DAILY", "900"))
PACIFIC = timedelta(hours=-8)


class EmbedError(Exception):
    """An embedding call failed; .status is the HTTP status (0 for network errors)."""

    def __init__(self, status: int, message: str):
        super().__init__(f"{status} {message}".strip())
        self.status = status
        self.message = message


def enabled() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY")) and os.environ.get("WS_EMBED", "1") != "0"


def _error(r: httpx.Response) -> EmbedError:
    try:
        err = r.json().get("error", {})
        msg = f"{err.get('status', '')}: {err.get('message', '')}"[:300]
    except ValueError:
        msg = r.text[:200]
    return EmbedError(r.status_code, msg)


def _post(client: httpx.Client, model: str, chunk: list[str], key: str) -> httpx.Response:
    body = {
        "requests": [
            {
                "model": model,
                "content": {"parts": [{"text": t}]},
                "outputDimensionality": DIM,
                "taskType": "SEMANTIC_SIMILARITY",
            }
            for t in chunk
        ]
    }
    return client.post(f"{API}/{model}:batchEmbedContents", json=body, headers={"x-goog-api-key": key})


def discover(client: httpx.Client, key: str) -> str | None:
    """The best embedding model this key can use, when the configured one is gone."""
    r = client.get(f"{API}/models", params={"pageSize": 200}, headers={"x-goog-api-key": key})
    if r.status_code != 200:
        return None
    names = [
        m["name"]
        for m in r.json().get("models", [])
        if "batchEmbedContents" in m.get("supportedGenerationMethods", [])
        or "embedContent" in m.get("supportedGenerationMethods", [])
    ]
    names.sort(key=lambda n: ("gemini-embedding" not in n, "exp" in n or "preview" in n, n))
    return names[0] if names else None


def embed(texts: list[str], model: str | None = None) -> list[list[float]]:
    """One vector per text. Raises EmbedError. A vanished model is replaced by discover() once."""
    out: list[list[float]] = []
    key = os.environ["GEMINI_API_KEY"]
    model = model or current_model()
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        for i in range(0, len(texts), 100):
            chunk = texts[i : i + 100]
            try:
                r = _post(client, model, chunk, key)
                if r.status_code == 404:
                    found = discover(client, key)
                    if found and found != model:
                        log.warning("embedding model %s is gone; using %s", model, found)
                        model = found
                        _remember_model(found)
                        r = _post(client, model, chunk, key)
            except httpx.HTTPError as e:
                raise EmbedError(0, type(e).__name__) from e
            if r.status_code != 200:
                raise _error(r)
            out += [e["values"] for e in r.json()["embeddings"]]
    return out


# ------------------------------------------------------------------ budget and back-off (meta table)


def _meta(key: str) -> str | None:
    import db

    row = db.one("SELECT value FROM meta WHERE key = ?", (key,))
    return row["value"] if row else None


def _set_meta(key: str, value: str) -> None:
    import db

    with db.tx() as c:
        c.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def current_model() -> str:
    return _meta("embed:model") or MODEL


def _remember_model(name: str) -> None:
    _set_meta("embed:model", name)


def google_day(now: datetime | None = None) -> str:
    return ((now or datetime.now(UTC)) + PACIFIC).date().isoformat()


def remaining(now: datetime | None = None) -> int:
    """Headlines still allowed today; 0 while a quota error asks us to wait."""
    now = now or datetime.now(UTC)
    until = _meta("embed:blocked_until")
    if until and datetime.fromisoformat(until) > now:
        return 0
    used = json.loads(_meta("embed:used") or "{}")
    return max(0, DAILY - int(used.get(google_day(now), 0)))


def spend(n: int, now: datetime | None = None) -> None:
    day = google_day(now)
    used = json.loads(_meta("embed:used") or "{}")
    _set_meta("embed:used", json.dumps({day: int(used.get(day, 0)) + n}))


def back_off(err: EmbedError, now: datetime | None = None) -> datetime | None:
    """After a 429: wait for the next Google day when the daily quota is used up, else two minutes."""
    now = now or datetime.now(UTC)
    if err.status != 429:
        return None
    if "day" in err.message.lower() or "perday" in err.message.lower().replace(" ", ""):
        local = now + PACIFIC
        until = datetime(local.year, local.month, local.day, tzinfo=UTC) + timedelta(days=1) - PACIFIC
    else:
        until = now + timedelta(minutes=2)
    _set_meta("embed:blocked_until", until.replace(microsecond=0).isoformat())
    return until


def status() -> dict:
    now = datetime.now(UTC)
    used = json.loads(_meta("embed:used") or "{}")
    return {
        "enabled": enabled(),
        "model": current_model(),
        "used_today": int(used.get(google_day(now), 0)),
        "daily": DAILY,
        "blocked_until": _meta("embed:blocked_until"),
        "last_error": _meta("embed:last_error"),
    }


def note_error(err: Exception) -> None:
    stamp = datetime.now(UTC).replace(microsecond=0).isoformat()
    _set_meta("embed:last_error", f"{stamp} {err}"[:400])


def pack(vec: list[float]) -> str:
    return json.dumps([round(x, 4) for x in vec], separators=(",", ":"))


def _vector(article: dict):
    v = article.get("emb")
    if isinstance(v, str) and v:
        try:
            return json.loads(v)
        except ValueError:
            return None
    return v or None


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def _numbers(title: str) -> set[str]:
    return {n.replace(",", ".") for n in _NUM.findall(title)}


def compatible(a: dict, b: dict) -> bool:
    """Similar meaning is not enough: same countries, no conflicting figures, close in time."""
    ca, cb = set(a.get("countries") or []), set(b.get("countries") or [])
    if ca and cb and not ca & cb:
        return False
    na, nb = _numbers(a["title"]), _numbers(b["title"])
    if na and nb and not na & nb:
        return False
    try:
        gap = abs((datetime.fromisoformat(a["published"]) - datetime.fromisoformat(b["published"])).total_seconds())
    except (KeyError, ValueError):
        gap = 0
    return gap <= 72 * 3600


def merge_groups(groups: list[list[dict]], limit: int = 800) -> list[list[dict]]:
    """Join token-based groups whose lead headlines mean the same thing."""
    try:
        import numpy as np
    except ImportError:
        return groups
    reps = [(gi, g[0], _vector(g[0])) for gi, g in enumerate(groups[:limit])]
    reps = [(gi, a, v) for gi, a, v in reps if v and len(v) == DIM]
    if len(reps) < 2:
        return groups
    m = np.array([v for _, _, v in reps], dtype=np.float32)
    m /= np.linalg.norm(m, axis=1, keepdims=True) + 1e-9
    sims = m @ m.T
    parent = list(range(len(groups)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in zip(*np.nonzero(np.triu(sims, 1) >= THRESHOLD), strict=True):
        (gi, a, _), (gj, b, _) = reps[i], reps[j]
        if compatible(a, b):
            parent[find(gj)] = find(gi)
    merged: dict[int, list[dict]] = {}
    for gi, g in enumerate(groups):
        merged.setdefault(find(gi), []).extend(g)
    return list(merged.values())
