"""Provider-agnostic LLM interface.

Every model call in WorldScope goes through `LLMProvider`. Adapters translate it to a
vendor SDK; the rest of the code never imports a vendor SDK directly, so switching
provider is a configuration change.

Inputs built from news articles are untrusted text. Callers pass them inside
`Message.content` as quoted data; providers never execute tools on the model's behalf.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, ValidationError


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class Completion(BaseModel):
    text: str
    model: str
    usage: Usage


class StructuredResult[T: BaseModel](BaseModel):
    value: T
    model: str
    usage: Usage
    attempts: int


class LLMError(RuntimeError):
    """The provider failed or returned something unusable."""


class StructuredOutputError(LLMError):
    """The model's output did not match the requested schema, even after a retry."""

    def __init__(self, message: str, errors: ValidationError | None = None) -> None:
        super().__init__(message)
        self.errors = errors


class LLMProvider(ABC):
    name: str

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        model: str,
        max_tokens: int = 1024,
        temperature: float | None = None,
    ) -> Completion:
        """Free-text completion. `temperature=None` keeps the provider's default."""

    @abstractmethod
    async def structured[T: BaseModel](
        self,
        *,
        system: str,
        messages: list[Message],
        schema: type[T],
        model: str,
        max_tokens: int = 1024,
    ) -> StructuredResult[T]:
        """Output validated against `schema`. Raises StructuredOutputError if it never fits."""
