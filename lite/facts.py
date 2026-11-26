"""Hard numbers for a pair of countries, from free public APIs, cached for a month.

- World Bank (no key): size of the economy, people, military spending, openness to trade,
  remittances. Each value comes with its year, because small countries report late.
- UN Comtrade public preview (no key): goods trade between the two, in both directions.

Everything here is optional: if an API is down, the pair analysis runs without it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

import collectors
import db

HERE = Path(__file__).parent
CACHE_DAYS = 30

# (World Bank code, label, unit)
INDICATORS = [
    ("NY.GDP.MKTP.CD", "YaIM", "usd"),
    ("NY.GDP.PCAP.CD", "Aholi jon boshiga YaIM", "usd"),
    ("SP.POP.TOTL", "Aholi", "people"),
    ("MS.MIL.XPND.CD", "Harbiy xarajat", "usd"),
    ("NE.TRD.GNFS.ZS", "Tashqi savdo / YaIM", "pct"),
    ("BX.TRF.PWKR.DT.GD.ZS", "Pul o'tkazmalari / YaIM", "pct"),
]
_CODES = json.loads((HERE / "comtrade_codes.json").read_text())


def human(value: float | None, unit: str) -> str:
    """1.8e12 → "1.8 trln $"; numbers the way a reader says them."""
    if value is None:
        return "—"
    if unit == "pct":
        return f"{value:.1f}%"
    suffix = " $" if unit == "usd" else ""
    for size, word in ((1e12, "trln"), (1e9, "mlrd"), (1e6, "mln"), (1e3, "ming")):
        if abs(value) >= size:
            return f"{value / size:.1f} {word}{suffix}"
    return f"{value:,.0f}{suffix}"


async def _world_bank(client: httpx.AsyncClient, isos: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {iso: {} for iso in isos}
    for code, _, _ in INDICATORS:
        try:
            r = await client.get(
                f"https://api.worldbank.org/v2/country/{';'.join(isos)}/indicator/{code}",
                params={"format": "json", "mrnev": 1, "per_page": 50},
            )
            rows = r.json()[1] if r.status_code == 200 and len(r.json()) > 1 else []
        except (httpx.HTTPError, ValueError, IndexError, TypeError):
            rows = []
        for row in rows or []:
            iso = row.get("countryiso3code")
            if iso in out and row.get("value") is not None:
                out[iso][code] = {"value": row["value"], "year": row.get("date")}
    return out


async def _trade(client: httpx.AsyncClient, a: str, b: str) -> dict | None:
    """Goods exports a→b and b→a for the latest year either side has reported."""
    url = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
    year = datetime.now(UTC).year
    for period in (year - 2, year - 3):
        for rep, par, flip in ((a, b, False), (b, a, True)):
            rc, pc = (_CODES.get(rep) or {}).get("r"), (_CODES.get(par) or {}).get("p")
            if not rc or not pc:
                continue
            try:
                r = await client.get(
                    url,
                    params={
                        "reporterCode": rc,
                        "partnerCode": pc,
                        "period": period,
                        "cmdCode": "TOTAL",
                        "flowCode": "X,M",
                    },
                )
                data = r.json().get("data") or [] if r.status_code == 200 else []
            except (httpx.HTTPError, ValueError):
                data = []
            flows = {d.get("flowCode"): d.get("primaryValue") for d in data if d.get("primaryValue") is not None}
            if not flows:
                continue
            # from the reporter's books: X = reporter exports to partner, M = reporter imports from partner
            rep_x, rep_m = flows.get("X"), flows.get("M")
            a_to_b, b_to_a = (rep_m, rep_x) if flip else (rep_x, rep_m)
            return {"year": str(period), "a_to_b": a_to_b, "b_to_a": b_to_a, "reporter": rep}
    return None


async def pair_facts(a: str, b: str) -> dict:
    """World Bank figures for both countries and their bilateral trade, from cache when fresh."""
    result: dict = {"countries": {}, "trade": None}
    missing = []
    for iso in (a, b):
        cached = await asyncio.to_thread(db.cache_get, f"wb:{iso}", CACHE_DAYS)
        if cached is None:
            missing.append(iso)
        else:
            result["countries"][iso] = cached
    x, y = sorted((a, b))  # trade is fetched and cached for the sorted pair, then turned to face a→b
    tkey = f"trade:{x}-{y}"
    trade_cached = await asyncio.to_thread(db.cache_get, tkey, CACHE_DAYS)
    async with httpx.AsyncClient(headers={"User-Agent": collectors.UA}, timeout=httpx.Timeout(20.0)) as client:
        if missing:
            fetched = await _world_bank(client, missing)
            for iso, vals in fetched.items():
                result["countries"][iso] = vals
                if vals:
                    await asyncio.to_thread(db.cache_put, f"wb:{iso}", vals)
        if trade_cached is not None:
            trade = trade_cached.get("trade")
        else:
            trade = await _trade(client, x, y)
            await asyncio.to_thread(db.cache_put, tkey, {"trade": trade})
    if trade and a != x:
        trade = {**trade, "a_to_b": trade.get("b_to_a"), "b_to_a": trade.get("a_to_b")}
    result["trade"] = trade
    return result


def rows_for(facts: dict, a: str, b: str) -> list[dict]:
    """Table rows for the page: label, A's value, B's value, years."""
    out = []
    for code, label, unit in INDICATORS:
        va, vb = facts["countries"].get(a, {}).get(code), facts["countries"].get(b, {}).get(code)
        if not va and not vb:
            continue
        out.append(
            {
                "label": label,
                "a": human(va["value"], unit) if va else "—",
                "a_year": va["year"] if va else "",
                "b": human(vb["value"], unit) if vb else "—",
                "b_year": vb["year"] if vb else "",
            }
        )
    return out


def prompt_block(facts: dict, a: str, b: str, names: dict[str, str]) -> str:
    """The same numbers as plain text for the model."""
    lines = []
    for code, label, unit in INDICATORS:
        parts = []
        for iso in (a, b):
            v = facts["countries"].get(iso, {}).get(code)
            if v:
                parts.append(f"{names[iso]} {human(v['value'], unit)} ({v['year']})")
        if parts:
            lines.append(f"- {label} [{code}]: " + "; ".join(parts))
    t = facts.get("trade")
    if t:
        lines.append(
            f"- Goods trade {t['year']} (UN Comtrade): {names[a]}→{names[b]} {human(t.get('a_to_b'), 'usd')}, "
            f"{names[b]}→{names[a]} {human(t.get('b_to_a'), 'usd')}"
        )
    else:
        lines.append("- Goods trade between them: no figure in UN Comtrade for recent years")
    return "\n".join(lines)
