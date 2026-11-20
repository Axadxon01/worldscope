"""Runs against real PostgreSQL (with pgvector) and Redis.

Enabled with WORLDSCOPE_INTEGRATION=1 plus DATABASE_URL, REDIS_URL and SECRET_KEY.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import inspect, select
from sqlalchemy.sql import column, table

from tests.integration.conftest import alembic_config
from worldscope.api.main import create_app
from worldscope.core.config import get_settings
from worldscope.db.base import Base
from worldscope.db.session import get_sync_engine
from worldscope.tasks.system import write_heartbeat

pytestmark = pytest.mark.integration

pg_extension = table("pg_extension", column("extname"))
APP_TABLES = {
    "regions",
    "countries",
    "topics",
    "sources",
    "source_fetch_runs",
    "articles",
    "users",
    "sessions",
    "invitations",
    "audit_log",
    "bookmarks",
}


def installed_extensions() -> set[str]:
    with get_sync_engine().connect() as conn:
        return set(conn.execute(select(pg_extension.c.extname)).scalars())


def tables() -> set[str]:
    return set(inspect(get_sync_engine()).get_table_names()) - {"alembic_version"}


@pytest.fixture
def redis_client() -> Iterator[Redis]:
    client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    client.delete(get_settings().worker_heartbeat_key)
    yield client
    client.delete(get_settings().worker_heartbeat_key)
    client.close()


def test_migrations_run_up_and_down_cleanly() -> None:
    config = alembic_config()
    command.upgrade(config, "head")
    assert {"vector", "pg_trgm", "citext"} <= installed_extensions()
    assert tables() == APP_TABLES
    command.downgrade(config, "base")
    assert tables() == set()
    command.upgrade(config, "head")
    assert tables() == APP_TABLES
    get_sync_engine().dispose()


def test_models_match_migrations() -> None:
    """The ORM models and the migrated schema agree, so no change is left unmigrated."""
    command.upgrade(alembic_config(), "head")
    engine = get_sync_engine()
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)
    engine.dispose()
    assert differences == []


def test_ready_and_status_against_real_services(redis_client: Redis) -> None:
    command.upgrade(alembic_config(), "head")
    with TestClient(create_app(get_settings())) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200, ready.text

        before = client.get("/api/v1/system/status").json()
        assert before["database"]["status"] == "ok"
        assert before["redis"]["status"] == "ok"
        assert before["workers"]["status"] == "unknown"
        assert before["status"] == "degraded"

        settings = get_settings()
        write_heartbeat(redis_client, settings.worker_heartbeat_key, 300)
        after = client.get("/api/v1/system/status").json()
        assert after["workers"]["status"] == "ok"
        assert after["status"] == "ok"
