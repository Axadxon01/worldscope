"""A country's goods trade with every partner, from UN Comtrade, for the trade layer on the globe.

One request for exports and one for imports, for the latest year the country has reported
(Comtrade's free preview needs no key). Cached for 30 days. Partners Comtrade lumps together
("Areas, nes", bunkers, free zones) are kept as "other" so the totals still add up.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx

import collectors
import db
import pairs

log = logging.getLogger("worldscope.trade")
HERE = Path(__file__).parent
URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
CACHE_DAYS = 30
_CODES = json.loads((HERE / "comtrade_codes.json").read_text())
PARTNER_ISO = {v["p"]: iso for iso, v in _CODES.items() if v.get("p")}
TOP = 15


def parse(rows: list[dict]) -> dict:
    """Comtrade rows (one flow) → {"world": total, "partners": [[ISO3, value], ...], "other": value}."""
    world, other, partners = None, 0.0, {}
    for r in rows:
        v, code = r.get("primaryValue"), r.get("partnerCode")
        if v is None:
            continue
        if code == 0:
            world = float(v)
            continue
        iso = PARTNER_ISO.get(code)
        if iso and pairs.valid(iso):
            partners[iso] = partners.get(iso, 0.0) + float(v)
        else:
            other += float(v)
    ranked = sorted(partners.items(), key=lambda kv: -kv[1])
    return {"world": world, "partners": [[k, round(v)] for k, v in ranked], "other": round(other)}


async def _flow(client: httpx.AsyncClient, reporter: int, period: int, flow: str) -> list[dict] | None:
    params = {"reporterCode": reporter, "period": period, "flowCode": flow, "cmdCode": "TOTAL",
              "customsCode": "C00", "motCode": 0}  # fmt: skip
    for attempt in range(3):
        try:
            r = await client.get(URL, params=params)
        except httpx.HTTPError as e:
            log.warning("comtrade %s %s: %s", reporter, flow, type(e).__name__)
            return None
        if r.status_code == 429:  # the free preview allows a request every second or two
            await asyncio.sleep(2.5 * (attempt + 1))
            continue
        if r.status_code != 200:
            return None
        try:
            return r.json().get("data") or []
        except ValueError:
            return None
    return None


async def flows(iso: str, force: bool = False) -> dict:
    """{"iso", "year", "exports": {...}, "imports": {...}} or {"iso", "year": None} when not reported."""
    key = f"flows:{iso}"
    if not force and (hit := await asyncio.to_thread(db.cache_get, key, CACHE_DAYS)):
        return hit
    rc = (_CODES.get(iso) or {}).get("r")
    out: dict = {"iso": iso, "year": None}
    if rc:
        year = datetime.now(UTC).year
        async with httpx.AsyncClient(headers={"User-Agent": collectors.UA}, timeout=25) as client:
            for period in (year - 1, year - 2, year - 3):
                x = await _flow(client, rc, period, "X")
                if not x:
                    continue
                await asyncio.sleep(1.2)
                m = await _flow(client, rc, period, "M") or []
                out = {"iso": iso, "year": period, "exports": parse(x), "imports": parse(m)}
                break
    if out["year"] or rc is None:
        await asyncio.to_thread(db.cache_put, key, out)
    return out


def top(side: dict | None, n: int = TOP) -> list[list]:
    return (side or {}).get("partners", [])[:n]
