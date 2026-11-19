"""Health of the platform's moving parts, reported honestly.

Used by the readiness probe (database + Redis decide whether the API can serve) and by
the status indicator in the web app, which also shows worker liveness and whether an
AI provider is configured.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from worldscope import __version__
from worldscope.core.config import Settings
from worldscope.core.logging import get_logger

log = get_logger(__name__)

CHECK_TIMEOUT_SECONDS = 2.0
# Beat sends a heartbeat every 60 seconds; two missed beats means the workers are stale.
HEARTBEAT_STALE_AFTER_SECONDS = 150

ComponentState = Literal["ok", "down", "not_configured"]


class ComponentStatus(BaseModel):
    status: ComponentState
    detail: str | None = None


class WorkerStatus(BaseModel):
    status: Literal["ok", "stale", "unknown"]
    last_heartbeat_at: datetime | None = None
    age_seconds: int | None = None


class SystemStatus(BaseModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    environment: str
    checked_at: datetime
    database: ComponentStatus
    redis: ComponentStatus
    workers: WorkerStatus
    llm: ComponentStatus


async def check_database(session: AsyncSession) -> ComponentStatus:
    try:
        await asyncio.wait_for(session.execute(select(literal(1))), CHECK_TIMEOUT_SECONDS)
    except Exception as exc:  # any failure means "not reachable"; the cause is logged
        log.warning("database_check_failed", error=type(exc).__name__)
        return ComponentStatus(status="down", detail="Database not reachable")
    return ComponentStatus(status="ok")


async def check_redis(redis: Redis) -> ComponentStatus:
    try:
        await asyncio.wait_for(redis.ping(), CHECK_TIMEOUT_SECONDS)
    except Exception as exc:
        log.warning("redis_check_failed", error=type(exc).__name__)
        return ComponentStatus(status="down", detail="Redis not reachable")
    return ComponentStatus(status="ok")


async def check_workers(redis: Redis, key: str, now: datetime | None = None) -> WorkerStatus:
    now = now or datetime.now(UTC)
    try:
        raw = await asyncio.wait_for(redis.get(key), CHECK_TIMEOUT_SECONDS)
    except Exception:
        return WorkerStatus(status="unknown")
    if raw is None:
        return WorkerStatus(status="unknown")
    try:
        beat = datetime.fromisoformat(str(raw))
    except ValueError:
        return WorkerStatus(status="unknown")
    if beat.tzinfo is None:
        beat = beat.replace(tzinfo=UTC)
    age = max(0, int((now - beat).total_seconds()))
    state: Literal["ok", "stale"] = "ok" if age <= HEARTBEAT_STALE_AFTER_SECONDS else "stale"
    return WorkerStatus(status=state, last_heartbeat_at=beat, age_seconds=age)


def check_llm(settings: Settings) -> ComponentStatus:
    if settings.llm_configured:
        return ComponentStatus(status="ok", detail=settings.llm_provider)
    return ComponentStatus(
        status="not_configured",
        detail=f"{settings.llm_provider}: API key not set",
    )


OverallState = Literal["ok", "degraded", "down"]


def overall(
    database: ComponentStatus, redis: ComponentStatus, workers: WorkerStatus
) -> OverallState:
    if database.status != "ok" or redis.status != "ok":
        return "down"
    if workers.status != "ok":
        return "degraded"
    return "ok"


async def collect_status(session: AsyncSession, redis: Redis, settings: Settings) -> SystemStatus:
    database, cache = await asyncio.gather(check_database(session), check_redis(redis))
    workers = await check_workers(redis, settings.worker_heartbeat_key)
    return SystemStatus(
        status=overall(database, cache, workers),
        version=__version__,
        environment=settings.environment,
        checked_at=datetime.now(UTC),
        database=database,
        redis=cache,
        workers=workers,
        llm=check_llm(settings),
    )
