#!/usr/bin/env python3
"""Check every RSS/Atom source in backend/config/sources.yaml against the live web.

For each feed it reports: HTTP status, whether the body parses as RSS or Atom, the
number of items, the age of the newest item, and whether robots.txt allows fetching
the feed URL. Exit code 1 if any enabled source fails.

Run from the repository root:  cd backend && uv run python ../scripts/check_feeds.py
Options: --json for machine-readable output, --only ID to check one source.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlsplit

import httpx
import yaml
from defusedxml import ElementTree

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "backend" / "config" / "sources.yaml"
USER_AGENT = "WorldScopeBot/0.1 (+https://github.com/Axadxon01/worldscope; feed checker)"
MAX_BYTES = 5 * 1024 * 1024
ATOM = "{http://www.w3.org/2005/Atom}"
RSS1 = "{http://purl.org/rss/1.0/}"


@dataclass
class Result:
    id: str
    ok: bool
    status: int | None
    kind: str | None
    items: int
    newest_age_days: float | None
    robots_allowed: bool | None
    error: str | None = None


def _parse_date(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip()
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_feed(body: bytes) -> tuple[str, int, datetime | None]:
    root = ElementTree.fromstring(body)
    tag = root.tag
    if tag == "rss":
        items = root.findall("./channel/item")
        dates = [
            i.findtext("pubDate") or i.findtext("{http://purl.org/dc/elements/1.1/}date")
            for i in items
        ]
        kind = "rss"
    elif tag == f"{ATOM}feed":
        items = root.findall(f"{ATOM}entry")
        dates = [i.findtext(f"{ATOM}updated") or i.findtext(f"{ATOM}published") for i in items]
        kind = "atom"
    elif tag.endswith("RDF"):
        items = root.findall(f"{RSS1}item")
        dates = [i.findtext("{http://purl.org/dc/elements/1.1/}date") for i in items]
        kind = "rss1"
    else:
        raise ValueError(f"not a feed (root element {tag})")
    parsed = [d for d in (_parse_date(x) for x in dates) if d is not None]
    return kind, len(items), max(parsed) if parsed else None


def robots_allows(client: httpx.Client, url: str) -> bool | None:
    parts = urlsplit(url)
    try:
        response = client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
    except httpx.HTTPError:
        return None
    if response.status_code >= 400:
        return True  # no robots.txt means no restriction
    parser = robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def check(source: dict[str, object]) -> Result:
    url = str(source["url"])
    sid = str(source["id"])
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True
    ) as client:
        robots = robots_allows(client, url)
        try:
            response = client.get(url)
        except httpx.HTTPError as exc:
            return Result(sid, False, None, None, 0, None, robots, type(exc).__name__)
        if response.status_code != 200:
            return Result(sid, False, response.status_code, None, 0, None, robots, "HTTP error")
        if len(response.content) > MAX_BYTES:
            return Result(sid, False, 200, None, 0, None, robots, "feed larger than 5 MB")
        try:
            kind, count, newest = parse_feed(response.content)
        except Exception as exc:  # any parse failure is reported, not raised
            return Result(sid, False, 200, None, 0, None, robots, f"parse: {exc}"[:120])
    age = round((datetime.now(UTC) - newest).total_seconds() / 86400, 1) if newest else None
    ok = count > 0 and robots is not False and (age is None or age <= 30)
    error = None if ok else ("robots.txt disallows" if robots is False else "empty or stale")
    return Result(sid, ok, 200, kind, count, age, robots, error)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--only")
    args = parser.parse_args()

    sources = yaml.safe_load(SOURCES.read_text())["sources"]
    feeds = [s for s in sources if s["type"] in ("rss", "official") and s.get("url")]
    if args.only:
        feeds = [s for s in feeds if s["id"] == args.only]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, feeds))

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        for r in results:
            mark = "ok  " if r.ok else "FAIL"
            age = "-" if r.newest_age_days is None else f"{r.newest_age_days}d"
            print(
                f"{mark} {r.id:<28} {r.kind or '-':<5} items={r.items:<4} newest={age:<7} "
                f"robots={r.robots_allowed} {r.error or ''}"
            )
        print(f"\n{sum(r.ok for r in results)}/{len(results)} feeds healthy")

    enabled = {s["id"] for s in feeds if s.get("enabled")}
    return 1 if any(not r.ok and r.id in enabled for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
