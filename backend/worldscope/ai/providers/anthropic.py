"""Claude adapter.

Structured output uses forced tool use: the model must call one tool whose input
schema is the Pydantic schema, and the result is validated again on our side. One
corrective retry is allowed; after that the call fails rather than guessing.
"""

from __future__ import annotations

import json
from typing import Any

import anthropic
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

OUTPUT_TOOL = "record_output"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        client: Any | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
    ) -> None:
        if client is None:
            if not api_key:
                raise LLMError("Anthropic API key is not configured")
            client = anthropic.AsyncAnthropic(
                api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
            )
        self._client = client

    async def _create(self, **kwargs: Any) -> Any:
        try:
            return await self._client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            raise LLMError(f"Anthropic request failed: {type(exc).__name__}") from exc

    @staticmethod
    def _usage(response: Any) -> Usage:
        return Usage(
            input_tokens=int(response.usage.input_tokens),
            output_tokens=int(response.usage.output_tokens),
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float | None = None,
    ) -> Completion:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [m.model_dump() for m in messages],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        response = await self._create(**kwargs)
        text = "".join(block.text for block in response.content if block.type == "text")
        return Completion(text=text, model=str(response.model), usage=self._usage(response))

    async def structured[T: BaseModel](
        self,
        *,
        system: str,
        messages: list[Message],
        schema: type[T],
        model: str,
        max_tokens: int = 1024,
    ) -> StructuredResult[T]:
        tool = {
            "name": OUTPUT_TOOL,
            "description": f"Record the result as a {schema.__name__} object.",
            "input_schema": schema.model_json_schema(),
        }
        conversation = list(messages)
        total = Usage()
        last_error: ValidationError | None = None

        for attempt in (1, 2):
            response = await self._create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[m.model_dump() for m in conversation],
                tools=[tool],
                tool_choice={"type": "tool", "name": OUTPUT_TOOL},
            )
            usage = self._usage(response)
            total = Usage(
                input_tokens=total.input_tokens + usage.input_tokens,
                output_tokens=total.output_tokens + usage.output_tokens,
            )
            call = next(
                (b for b in response.content if b.type == "tool_use" and b.name == OUTPUT_TOOL),
                None,
            )
            if call is None:
                feedback = f"Respond only by calling the {OUTPUT_TOOL} tool."
                previous = "(no tool call)"
            else:
                try:
                    value = schema.model_validate(call.input)
                except ValidationError as exc:
                    last_error = exc
                    feedback = (
                        "That output failed validation: "
                        f"{json.dumps(exc.errors(include_url=False, include_input=False))}. "
                        f"Call {OUTPUT_TOOL} again with corrected values."
                    )
                    previous = json.dumps(call.input)[:4000]
                else:
                    return StructuredResult(
                        value=value, model=str(response.model), usage=total, attempts=attempt
                    )
            conversation = [
                *messages,
                Message(role="assistant", content=f"Previous output: {previous}"),
                Message(role="user", content=feedback),
            ]

        raise StructuredOutputError(f"Output did not match {schema.__name__}", last_error)
