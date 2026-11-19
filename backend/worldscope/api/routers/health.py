"""Liveness and readiness probes for Docker and load balancers."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from worldscope.api.deps import RedisDep, SessionDep
from worldscope.services.system_status import ComponentStatus, check_database, check_redis

router = APIRouter(tags=["health"])


class Liveness(BaseModel):
    status: str


class Readiness(BaseModel):
    status: str
    database: ComponentStatus
    redis: ComponentStatus


@router.get("/health", response_model=Liveness)
async def liveness() -> Liveness:
    """The process is up. Says nothing about dependencies."""
    return Liveness(status="ok")


@router.get("/health/ready", response_model=Readiness)
async def readiness(session: SessionDep, redis: RedisDep, response: Response) -> Readiness:
    """Ready to serve: the database and Redis both answer. 503 otherwise."""
    database = await check_database(session)
    cache = await check_redis(redis)
    ready = database.status == "ok" and cache.status == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ok" if ready else "unavailable", database=database, redis=cache)
