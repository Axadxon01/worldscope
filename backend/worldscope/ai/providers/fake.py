"""Deterministic provider for tests: scripted replies, no network, no cost.

Tests queue the replies they expect; an unexpected extra call fails loudly instead of
silently returning something plausible.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ValidationError

from worldscope.ai.providers.base import (
    Completion,
    LLMError,
    LLMProvider,
    Message,
    StructuredOutputError,
    StructuredResult,
    Usage,
)


class FakeLLMProvider(LLMProvider):
    name = "fake"

    def __init__(self, replies: list[str | dict[str, Any]] | None = None) -> None:
        self._replies: list[str | dict[str, Any]] = list(replies or [])
        self.calls: list[dict[str, Any]] = []

    def queue(self, *replies: str | dict[str, Any]) -> None:
        self._replies.extend(replies)

    def _next(self, call: dict[str, Any]) -> str | dict[str, Any]:
        self.calls.append(call)
        if not self._replies:
            raise LLMError("FakeLLMProvider: no scripted reply left for this call")
        return self._replies.pop(0)

    @staticmethod
    def _usage(messages: list[Message], text: str) -> Usage:
        # Rough, deterministic token counts so cost code paths are exercised.
        words_in = sum(len(m.content.split()) for m in messages)
        return Usage(input_tokens=words_in, output_tokens=len(text.split()))

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float | None = None,
    ) -> Completion:
        reply = self._next({"kind": "complete", "system": system, "messages": messages})
        text = reply if isinstance(reply, str) else json.dumps(reply)
        return Completion(text=text, model=model, usage=self._usage(messages, text))

    async def structured[T: BaseModel](
        self,
        *,
        system: str,
        messages: list[Message],
        schema: type[T],
        model: str,
        max_tokens: int = 1024,
    ) -> StructuredResult[T]:
        last_error: ValidationError | None = None
        for attempt in (1, 2):
            reply = self._next(
                {"kind": "structured", "schema": schema.__name__, "attempt": attempt}
            )
            try:
                value = (
                    schema.model_validate_json(reply)
                    if isinstance(reply, str)
                    else schema.model_validate(reply)
                )
            except ValidationError as exc:
                last_error = exc
                continue
            text = reply if isinstance(reply, str) else json.dumps(reply)
            return StructuredResult(
                value=value, model=model, usage=self._usage(messages, text), attempts=attempt
            )
        raise StructuredOutputError(f"Output did not match {schema.__name__}", last_error)
