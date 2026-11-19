"""Runs against real PostgreSQL (with pgvector) and Redis.

Enabled with WORLDSCOPE_INTEGRATION=1 plus DATABASE_URL, REDIS_URL and SECRET_KEY.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import select
from sqlalchemy.sql import column, table

from worldscope.api.main import create_app
from worldscope.core.config import get_settings
from worldscope.db.session import get_sync_engine
from worldscope.tasks.system import write_heartbeat

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
pg_extension = table("pg_extension", column("extname"))


def alembic_config() -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config


def installed_extensions() -> set[str]:
    with get_sync_engine().connect() as conn:
        return set(conn.execute(select(pg_extension.c.extname)).scalars())


@pytest.fixture
def migrated() -> None:
    command.upgrade(alembic_config(), "head")


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
    command.downgrade(config, "base")
    assert not {"vector", "pg_trgm", "citext"} & installed_extensions()
    command.upgrade(config, "head")
    assert {"vector", "pg_trgm", "citext"} <= installed_extensions()


@pytest.mark.usefixtures("migrated")
def test_ready_and_status_against_real_services(redis_client: Redis) -> None:
    with TestClient(create_app(get_settings())) as client:
        _check_live_status(client, redis_client)


def _check_live_status(client: TestClient, redis_client: Redis) -> None:
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
