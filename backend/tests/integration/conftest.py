"""Fixtures for tests against real PostgreSQL and Redis.

The database must be reached as an ordinary role (not a superuser): superusers bypass
row-level security, which would make the isolation tests pass for the wrong reason.
infra/postgres/init/10-app-role.sh sets that up for Docker, CI and `make test-integration`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine, text

from worldscope.api.main import create_app
from worldscope.core.config import get_settings
from worldscope.db.session import get_sync_engine
from worldscope.services.catalog_sync import sync_all

BACKEND = Path(__file__).resolve().parents[2]
INTERNAL_KEY = "integration-internal-key-" + "k" * 24
SETUP_CODE = "integration-setup-code-0123"
PASSWORD = "correct horse battery staple"


def alembic_config() -> Config:
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    return config


@pytest.fixture(scope="module")
def catalog() -> None:
    """Schema at head with reference data and sources loaded (once per module)."""
    command.upgrade(alembic_config(), "head")
    engine = get_sync_engine()
    with engine.begin() as conn:
        sync_all(conn)
    engine.dispose()


def wipe(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE bookmarks, sessions, invitations, audit_log, users, articles "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture
def db(catalog: None) -> Iterator[Engine]:
    engine = get_sync_engine()
    wipe(engine)
    client = Redis.from_url(get_settings().redis_url)
    for key in client.scan_iter("worldscope:limit:*"):
        client.delete(key)
    client.close()
    yield engine
    engine.dispose()


@pytest.fixture
def api(db: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("INTERNAL_API_KEY", INTERNAL_KEY)
    monkeypatch.setenv("SETUP_CODE", SETUP_CODE)
    get_settings.cache_clear()
    app = create_app(get_settings())
    with TestClient(app, headers={"X-Internal-Key": INTERNAL_KEY}) as client:
        yield client


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@dataclass
class Account:
    id: str
    email: str
    token: str
    role: str

    @property
    def headers(self) -> dict[str, str]:
        return bearer(self.token)


def setup_admin(api: TestClient, email: str = "admin@example.org") -> Account:
    response = api.post(
        "/api/v1/auth/setup",
        json={
            "code": SETUP_CODE,
            "email": email,
            "display_name": "Ada Admin",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return Account(body["user"]["id"], email, body["token"], "admin")


def invite(api: TestClient, admin: Account, email: str, role: str = "analyst") -> str:
    response = api.post(
        "/api/v1/admin/invitations", json={"email": email, "role": role}, headers=admin.headers
    )
    assert response.status_code == 201, response.text
    token: str = response.json()["token"]
    return token


def join(api: TestClient, admin: Account, email: str, role: str = "analyst") -> Account:
    token = invite(api, admin, email, role)
    response = api.post(
        "/api/v1/invitations/accept",
        json={"token": token, "display_name": email.split("@")[0], "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return Account(body["user"]["id"], email, body["token"], role)


def add_article(engine: Engine, title: str = "Talks resume in Geneva") -> str:
    """Insert one article from an enabled source; returns its public id."""
    url = f"https://example.org/{hashlib.sha256(title.encode()).hexdigest()[:12]}"
    with engine.begin() as conn:
        source_id = conn.execute(text("SELECT id FROM sources WHERE slug = 'uk-fcdo'")).scalar_one()
        public_id = conn.execute(
            text(
                "INSERT INTO articles (source_id, canonical_url, url_hash, title, snippet, "
                "language, published_at, retrieved_at) VALUES (:s, :u, :h, :t, :n, 'en', :p, :p) "
                "RETURNING public_id"
            ),
            {
                "s": source_id,
                "u": url,
                "h": hashlib.sha256(url.encode()).digest(),
                "t": title,
                "n": "Summary from the feed.",
                "p": datetime.now(UTC),
            },
        ).scalar_one()
    return str(public_id)


def scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as conn:
        return conn.execute(text(sql), params).scalar()
