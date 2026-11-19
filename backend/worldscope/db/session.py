"""Database engines.

One driver (psycopg 3) serves both the async API and the synchronous Celery workers
and migrations, so there is a single DATABASE_URL format everywhere.

The API's async engine is created in the app lifespan (see api/main.py), so its pool
belongs to the server's event loop. Workers and migrations use the sync engine.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from worldscope.core.config import get_settings


def create_api_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


@lru_cache
def get_sync_engine() -> Engine:
    return create_engine(get_settings().database_url, pool_pre_ping=True, pool_size=5)
