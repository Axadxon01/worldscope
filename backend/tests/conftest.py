"""Shared test setup.

Unit tests run with placeholder connection strings and never touch a network. Tests
marked `integration` need real PostgreSQL and Redis and run only when
WORLDSCOPE_INTEGRATION=1 (CI sets it; `make test-integration` does locally).
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

UNIT_ENV = {
    "ENVIRONMENT": "test",
    "DATABASE_URL": "postgresql+psycopg://test:test@localhost:5432/test",
    "REDIS_URL": "redis://localhost:6379/0",
    "SECRET_KEY": "t" * 48,  # test-only placeholder, long enough to pass validation
    "LLM_PROVIDER": "fake",
    "LOG_JSON": "false",
}

INTEGRATION = os.environ.get("WORLDSCOPE_INTEGRATION") == "1"

if not INTEGRATION:
    for key, value in UNIT_ENV.items():
        os.environ.setdefault(key, value)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if INTEGRATION:
        return
    skip = pytest.mark.skip(reason="set WORLDSCOPE_INTEGRATION=1 with PostgreSQL and Redis running")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _fresh_settings() -> Iterator[None]:
    """Settings and the sync engine are cached; clear them so each test sees its own env."""
    from worldscope.core.config import get_settings
    from worldscope.db.session import get_sync_engine

    for cached in (get_settings, get_sync_engine):
        cached.cache_clear()
    yield
    for cached in (get_settings, get_sync_engine):
        cached.cache_clear()
