"""LLMProvider — provider-agnostic structured-output Protocol.

Concrete providers (OpenAI, Anthropic, ...) implement `extract()` to take a
system + user prompt and a Pydantic schema, returning a validated instance.
Errors at the API layer or schema validation level bubble up — callers
decide retry / fail-loud semantics.
"""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(Protocol):
    name: str
    model: str

    async def extract(self, *, system: str, user: str, schema: type[T]) -> T: ...
