"""When each background job is due. The app runs due jobs when it starts and every few minutes
while awake; on Render's free plan a scheduled GitHub workflow wakes it (no secret needed), and the
app decides by itself what to do. Last runs are kept in the database (meta "job:<name>"), so a
restart does not repeat a job that already ran.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

import db

TASHKENT = timedelta(hours=5)
DIGEST_HOUR = 7  # Tashkent time: the morning message goes out from 07:00...
DIGEST_UNTIL = 12  # ...until noon; a wake-up later in the day waits for the next morning

# name → minimum time between runs
EVERY = {
    "refresh": timedelta(minutes=25),
    "explain": timedelta(hours=2),
    "automap": timedelta(hours=20),
    "digest": timedelta(hours=20),
    "weekly": timedelta(days=6, hours=12),
    "sanctions": timedelta(days=7),
    "watch_risk": timedelta(hours=20),
    "predictions": timedelta(hours=20),
    "backup": timedelta(days=6, hours=20),
    "screening": timedelta(days=6, hours=20),
}
ORDER = [
    "refresh",
    "explain",
    "sanctions",
    "screening",
    "digest",
    "automap",
    "watch_risk",
    "predictions",
    "weekly",
    "backup",
]


def last_runs() -> dict[str, datetime]:
    out = {}
    for r in db.rows("SELECT key, value FROM meta WHERE key LIKE ?", ("job:%",)):
        with contextlib.suppress(ValueError):
            out[r["key"][4:]] = datetime.fromisoformat(r["value"])
    return out


def mark(name: str, when: datetime | None = None) -> None:
    when = (when or datetime.now(UTC)).replace(microsecond=0)
    with db.tx() as c:
        c.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (f"job:{name}", when.isoformat()),
        )


def morning(now: datetime) -> datetime:
    """Today's 07:00 in Tashkent, as a UTC time."""
    local = now + TASHKENT
    return local.replace(hour=DIGEST_HOUR, minute=0, second=0, microsecond=0) - TASHKENT


def is_due(name: str, now: datetime, last: datetime | None) -> bool:
    if name == "digest":  # once each morning in Tashkent, whenever the service is first awake then
        start = morning(now)
        in_window = start <= now and (now + TASHKENT).hour < DIGEST_UNTIL
        return in_window and (last is None or last < start)
    return last is None or now - last >= EVERY[name]


def due(now: datetime | None = None, last: dict[str, datetime] | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    last = last if last is not None else last_runs()
    return [n for n in ORDER if is_due(n, now, last.get(n))]
