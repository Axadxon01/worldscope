from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from worldscope.core.config import Settings

BASE: dict[str, Any] = {
    "database_url": "postgresql+psycopg://u:p@db:5432/worldscope",
    "redis_url": "redis://redis:6379/0",
    "secret_key": "x" * 40,
}


def build(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> Settings:
    for key in ("DATABASE_URL", "REDIS_URL", "SECRET_KEY", "ENVIRONMENT", "LLM_PROVIDER"):
        monkeypatch.delenv(key, raising=False)
    values: dict[str, Any] = {**BASE, **overrides}
    return Settings(_env_file=None, **values)


def test_missing_required_value_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DATABASE_URL", "REDIS_URL", "SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValidationError) as info:
        Settings(_env_file=None)
    missing = {err["loc"][0] for err in info.value.errors()}
    assert missing == {"database_url", "redis_url", "secret_key"}


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@db:5432/worldscope",
        "postgres://u:p@db:5432/worldscope",
        "postgresql+psycopg://u:p@db:5432/worldscope",
    ],
)
def test_platform_postgres_urls_use_psycopg(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    settings = build(monkeypatch, database_url=url)
    assert settings.database_url == "postgresql+psycopg://u:p@db:5432/worldscope"


def test_non_postgres_urls_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError, match="PostgreSQL URL"):
        build(monkeypatch, database_url="mysql://u:p@db/worldscope")


def test_development_allows_missing_llm_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build(monkeypatch, environment="development", llm_provider="anthropic")
    assert settings.anthropic_api_key is None
    assert settings.llm_configured is False


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"secret_key": "short"}, "SECRET_KEY"),
        ({"secret_key": "change-me" + "x" * 40}, "SECRET_KEY"),
        ({"llm_provider": "fake"}, "tests only"),
        ({"llm_provider": "anthropic"}, "ANTHROPIC_API_KEY"),
        ({"anthropic_api_key": "k", "cors_origins": "*"}, "CORS_ORIGINS"),
    ],
)
def test_production_rules(
    monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        build(monkeypatch, environment="production", **overrides)


def test_production_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build(
        monkeypatch,
        environment="production",
        anthropic_api_key="sk-test",
        cors_origins="https://app.example.org, https://admin.example.org",
    )
    assert settings.cors_origins == ["https://app.example.org", "https://admin.example.org"]
    assert settings.llm_configured is True


def test_secret_is_not_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build(monkeypatch)
    assert "x" * 40 not in repr(settings)


def test_blank_api_key_counts_as_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build(monkeypatch, llm_provider="anthropic", anthropic_api_key="  ")
    assert settings.anthropic_api_key is None
    assert settings.llm_configured is False


def test_ai_can_be_turned_off_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = build(monkeypatch, environment="production", llm_provider="none")
    assert settings.llm_configured is False
