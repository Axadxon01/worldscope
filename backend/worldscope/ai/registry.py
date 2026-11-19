"""Builds the configured LLM provider."""

from __future__ import annotations

from worldscope.ai.providers.anthropic import AnthropicProvider
from worldscope.ai.providers.base import LLMError, LLMProvider
from worldscope.ai.providers.fake import FakeLLMProvider
from worldscope.core.config import Settings


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeLLMProvider()
    if settings.llm_provider == "anthropic":
        if settings.anthropic_api_key is None:
            raise LLMError("ANTHROPIC_API_KEY is not set; AI features are unavailable")
        return AnthropicProvider(settings.anthropic_api_key.get_secret_value())
    raise LLMError(f"Unknown LLM provider {settings.llm_provider!r}")  # pragma: no cover
