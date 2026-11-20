"""Application settings, read from environment variables (and `.env` in development).

The process refuses to start when a required value is missing or unsafe: a wrong
configuration should fail loudly at boot, never half-work at runtime.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "production"]
# "none" turns AI features off on purpose; it is not the same as a missing key.
LLMProviderName = Literal["anthropic", "none", "fake"]

MIN_SECRET_KEY_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "WorldScope Intelligence"
    environment: Environment = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    # Required: no defaults, so a missing value stops the process at startup.
    database_url: str = Field(description="SQLAlchemy URL, e.g. postgresql+psycopg://...")
    redis_url: str = Field(description="Redis URL used for the task queue, cache and rate limits")
    secret_key: SecretStr = Field(description="Signs sessions and tokens; 32+ random characters")

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    # AI provider. Keys stay on the backend and are never sent to the browser.
    llm_provider: LLMProviderName = "anthropic"
    anthropic_api_key: SecretStr | None = None
    llm_model_fast: str = "claude-haiku-5-5"
    llm_model_deep: str = "claude-sonnet-5-5"
    ai_daily_budget_usd: float = Field(default=3.0, ge=0)

    worker_heartbeat_key: str = "worldscope:worker:heartbeat"
    worker_heartbeat_ttl_seconds: int = Field(default=300, ge=60)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        # `ANTHROPIC_API_KEY=` in .env means "not set", not "set to an empty key".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("database_url")
    @classmethod
    def _postgres_only(cls, value: str) -> str:
        # Hosting platforms (Render, Heroku-style) hand out plain postgres:// URLs;
        # point them at the psycopg 3 driver this codebase uses everywhere.
        for prefix in ("postgresql://", "postgres://"):
            if value.startswith(prefix):
                value = "postgresql+psycopg://" + value.removeprefix(prefix)
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must be a PostgreSQL URL (postgresql+psycopg://...)")
        return value

    @model_validator(mode="after")
    def _production_rules(self) -> Settings:
        if self.environment != "production":
            return self
        secret = self.secret_key.get_secret_value()
        if len(secret) < MIN_SECRET_KEY_LENGTH or "change-me" in secret:
            raise ValueError("SECRET_KEY must be 32+ random characters in production")
        if self.llm_provider == "fake":
            raise ValueError("LLM_PROVIDER=fake is for tests only, not production")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic")
        if any(origin == "*" for origin in self.cors_origins):
            raise ValueError("CORS_ORIGINS may not contain '*' in production")
        return self

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "fake":
            return True
        if self.llm_provider == "none":
            return False
        return self.anthropic_api_key is not None


@lru_cache
def get_settings() -> Settings:
    # Required values come from the environment, not from arguments.
    return Settings()
