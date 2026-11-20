from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, Field

from worldscope.ai.providers.anthropic import OUTPUT_TOOL, AnthropicProvider
from worldscope.ai.providers.base import LLMError, Message, StructuredOutputError
from worldscope.ai.providers.fake import FakeLLMProvider
from worldscope.ai.registry import build_llm_provider
from worldscope.core.config import get_settings


class Classification(BaseModel):
    categories: list[str] = Field(min_length=1, max_length=3)
    confidence: float = Field(ge=0, le=1)


QUESTION = [Message(role="user", content="Classify: <article>Border talks resume</article>")]


# ---------------------------------------------------------------- fake provider


async def test_fake_complete_returns_scripted_text() -> None:
    llm = FakeLLMProvider(["Talks resumed."])
    result = await llm.complete(system="s", messages=QUESTION, model="m")
    assert result.text == "Talks resumed."
    assert result.usage.input_tokens > 0
    assert llm.calls[0]["kind"] == "complete"


async def test_fake_structured_retries_once_then_succeeds() -> None:
    llm = FakeLLMProvider(
        [{"categories": [], "confidence": 2}, {"categories": ["Diplomacy"], "confidence": 0.8}]
    )
    result = await llm.structured(system="s", messages=QUESTION, schema=Classification, model="m")
    assert result.value == Classification(categories=["Diplomacy"], confidence=0.8)
    assert result.attempts == 2


async def test_fake_structured_gives_up_after_two_bad_outputs() -> None:
    llm = FakeLLMProvider(['{"categories": []}', '{"confidence": 5}'])
    with pytest.raises(StructuredOutputError):
        await llm.structured(system="s", messages=QUESTION, schema=Classification, model="m")


async def test_fake_fails_loudly_when_unscripted() -> None:
    with pytest.raises(LLMError, match="no scripted reply"):
        await FakeLLMProvider().complete(system="s", messages=QUESTION, model="m")


# ---------------------------------------------------------------- anthropic adapter


def response(content: list[Any], model: str = "claude-haiku-5-5") -> SimpleNamespace:
    return SimpleNamespace(
        content=content, model=model, usage=SimpleNamespace(input_tokens=120, output_tokens=30)
    )


def tool_use(payload: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", name=OUTPUT_TOOL, input=payload)


class FakeMessages:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def provider(responses: list[Any]) -> tuple[AnthropicProvider, FakeMessages]:
    messages = FakeMessages(responses)
    return AnthropicProvider(client=SimpleNamespace(messages=messages)), messages


async def test_anthropic_complete_joins_text_blocks_and_omits_default_temperature() -> None:
    llm, api = provider(
        [
            response(
                [
                    SimpleNamespace(type="text", text="Hello "),
                    SimpleNamespace(type="text", text="world"),
                ]
            )
        ]
    )
    result = await llm.complete(system="sys", messages=QUESTION, model="claude-haiku-5-5")
    assert result.text == "Hello world"
    assert result.usage.input_tokens == 120
    assert "temperature" not in api.requests[0]
    assert api.requests[0]["system"] == "sys"


async def test_anthropic_structured_forces_the_output_tool() -> None:
    llm, api = provider([response([tool_use({"categories": ["Conflict"], "confidence": 0.9})])])
    result = await llm.structured(
        system="s", messages=QUESTION, schema=Classification, model="claude-haiku-5-5"
    )
    assert result.value.categories == ["Conflict"]
    request = api.requests[0]
    assert request["tool_choice"] == {"type": "tool", "name": OUTPUT_TOOL}
    assert request["tools"][0]["input_schema"]["title"] == "Classification"


async def test_anthropic_structured_retries_with_validation_feedback() -> None:
    llm, api = provider(
        [
            response([tool_use({"categories": [], "confidence": 0.5})]),
            response([tool_use({"categories": ["Trade"], "confidence": 0.7})]),
        ]
    )
    result = await llm.structured(system="s", messages=QUESTION, schema=Classification, model="m")
    assert result.attempts == 2
    assert result.usage.input_tokens == 240  # both attempts are counted
    retry_messages = api.requests[1]["messages"]
    assert retry_messages[-1]["role"] == "user"
    assert "failed validation" in retry_messages[-1]["content"]


async def test_anthropic_structured_raises_after_second_failure() -> None:
    llm, _ = provider([response([SimpleNamespace(type="text", text="no")])] * 2)
    with pytest.raises(StructuredOutputError):
        await llm.structured(system="s", messages=QUESTION, schema=Classification, model="m")


async def test_anthropic_api_errors_become_llm_errors() -> None:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    llm, _ = provider([anthropic.APIConnectionError(request=request)])
    with pytest.raises(LLMError, match="APIConnectionError"):
        await llm.complete(system="s", messages=QUESTION, model="m")


def test_anthropic_requires_a_key() -> None:
    with pytest.raises(LLMError, match="not configured"):
        AnthropicProvider(api_key=None)


# ---------------------------------------------------------------- registry


def test_registry_builds_fake_in_tests() -> None:
    assert isinstance(build_llm_provider(get_settings()), FakeLLMProvider)


def test_registry_refuses_anthropic_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        build_llm_provider(get_settings())


def test_registry_refuses_when_ai_is_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "none")
    get_settings.cache_clear()
    with pytest.raises(LLMError, match="turned off"):
        build_llm_provider(get_settings())
