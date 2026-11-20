from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from worldscope.api.deps import get_redis, get_session
from worldscope.api.main import create_app
from worldscope.core.config import get_settings


class FakeSession:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def execute(self, *_: Any, **__: Any) -> None:
        if self.fail:
            raise ConnectionError("db down")


class FakeRedis:
    def __init__(self, fail: bool = False, heartbeat: str | None = None) -> None:
        self.fail = fail
        self.heartbeat = heartbeat

    async def ping(self) -> bool:
        if self.fail:
            raise ConnectionError("redis down")
        return True

    async def get(self, _: str) -> str | None:
        if self.fail:
            raise ConnectionError("redis down")
        return self.heartbeat


def client_with(session: FakeSession, redis: FakeRedis) -> TestClient:
    app = create_app(get_settings())

    async def _session() -> AsyncIterator[FakeSession]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_redis] = lambda: redis
    return TestClient(app, raise_server_exceptions=False)


def test_liveness_needs_no_dependencies() -> None:
    client = client_with(FakeSession(fail=True), FakeRedis(fail=True))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_database_and_redis_answer() -> None:
    response = client_with(FakeSession(), FakeRedis()).get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize(
    ("session", "redis", "down"),
    [
        (FakeSession(fail=True), FakeRedis(), "database"),
        (FakeSession(), FakeRedis(fail=True), "redis"),
    ],
)
def test_not_ready_returns_503(session: FakeSession, redis: FakeRedis, down: str) -> None:
    response = client_with(session, redis).get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body[down]["status"] == "down"
    # The probe names the component but never leaks connection details.
    assert "redis down" not in response.text
    assert "db down" not in response.text


def test_status_reports_workers_and_llm() -> None:
    beat = (datetime.now(UTC) - timedelta(seconds=30)).isoformat()
    response = client_with(FakeSession(), FakeRedis(heartbeat=beat)).get("/api/v1/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["workers"]["status"] == "ok"
    assert 25 <= body["workers"]["age_seconds"] <= 60
    assert body["llm"] == {"status": "ok", "detail": "fake"}


def test_status_degraded_without_worker_heartbeat() -> None:
    body = client_with(FakeSession(), FakeRedis()).get("/api/v1/system/status").json()
    assert body["status"] == "degraded"
    assert body["workers"]["status"] == "unknown"


def test_security_headers_and_request_id() -> None:
    client = client_with(FakeSession(), FakeRedis())
    response = client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert len(response.headers["x-request-id"]) == 32

    echoed = client.get("/health", headers={"X-Request-ID": "abc-12345678"})
    assert echoed.headers["x-request-id"] == "abc-12345678"

    rejected = client.get("/health", headers={"X-Request-ID": "<script>alert(1)</script>"})
    assert rejected.headers["x-request-id"] != "<script>alert(1)</script>"


def test_unhandled_errors_do_not_leak_details() -> None:
    app = create_app(get_settings())

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "secret internal detail" not in response.text


def test_docs_hidden_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    get_settings.cache_clear()
    client = TestClient(create_app(get_settings()))
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_status_reports_ai_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "none")
    get_settings.cache_clear()
    body = client_with(FakeSession(), FakeRedis()).get("/api/v1/system/status").json()
    assert body["llm"] == {"status": "not_configured", "detail": "disabled"}


def test_status_ok_when_workers_are_not_part_of_this_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WORKERS_ENABLED", "false")
    get_settings.cache_clear()
    body = client_with(FakeSession(), FakeRedis()).get("/api/v1/system/status").json()
    assert body["workers"]["status"] == "disabled"
    assert body["status"] == "ok"
