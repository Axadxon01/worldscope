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
from datetime import datetime

import httpx

log = logging.getLogger("worldscope.embed")

MODEL = os.environ.get("WS_EMBED_MODEL", "models/gemini-embedding-001")
DIM = 256
THRESHOLD = float(os.environ.get("WS_EMBED_MERGE", "0.92"))
API = "https://generativelanguage.googleapis.com/v1beta"


def enabled() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY")) and os.environ.get("WS_EMBED", "1") != "0"


def embed(texts: list[str]) -> list[list[float]]:
    """One vector per text; raises httpx errors to the caller. Callers keep within 100 texts a minute."""
    out: list[list[float]] = []
    key = os.environ["GEMINI_API_KEY"]
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        for i in range(0, len(texts), 100):
            chunk = texts[i : i + 100]
            body = {
                "requests": [
                    {
                        "model": MODEL,
                        "content": {"parts": [{"text": t}]},
                        "outputDimensionality": DIM,
                        "taskType": "SEMANTIC_SIMILARITY",
                    }
                    for t in chunk
                ]
            }
            r = client.post(f"{API}/{MODEL}:batchEmbedContents", json=body, headers={"x-goog-api-key": key})
            r.raise_for_status()
            out += [e["values"] for e in r.json()["embeddings"]]
    return out


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
