"""Housekeeping tasks."""

from __future__ import annotations

from datetime import UTC, datetime

from celery.signals import worker_ready
from redis import Redis

from worldscope.core.config import get_settings
from worldscope.core.logging import get_logger
from worldscope.tasks.celery_app import celery_app

log = get_logger(__name__)


def write_heartbeat(client: Redis, key: str, ttl_seconds: int, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).isoformat()
    client.set(key, stamp, ex=ttl_seconds)
    return stamp


@celery_app.task(name="worldscope.tasks.system.heartbeat")  # type: ignore[untyped-decorator]
def heartbeat() -> str:
    """Proves a worker is consuming the queue; the status page reads the timestamp."""
    settings = get_settings()
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        stamp = write_heartbeat(
            client, settings.worker_heartbeat_key, settings.worker_heartbeat_ttl_seconds
        )
    finally:
        client.close()
    log.debug("worker_heartbeat", at=stamp)
    return stamp


@worker_ready.connect  # type: ignore[untyped-decorator]
def heartbeat_on_start(**_: object) -> None:
    """Queue a heartbeat as soon as a worker boots, so status turns green without
    waiting for the first scheduled beat. It goes through the queue on purpose: the
    heartbeat proves tasks are consumed, not just that the process started."""
    heartbeat.delay()
