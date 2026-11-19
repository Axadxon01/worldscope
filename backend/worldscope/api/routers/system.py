"""System status shown in the web app's status indicator."""

from __future__ import annotations

from fastapi import APIRouter

from worldscope.api.deps import RedisDep, SessionDep, SettingsDep
from worldscope.services.system_status import SystemStatus, collect_status

router = APIRouter(prefix="/api/v1/system", tags=["system"])


@router.get("/status", response_model=SystemStatus)
async def system_status(
    session: SessionDep, redis: RedisDep, settings: SettingsDep
) -> SystemStatus:
    return await collect_status(session, redis, settings)
