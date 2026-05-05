"""LLMProvider — provider-agnostic structured-output Protocol.

Concrete providers (OpenAI, Anthropic, ...) implement `extract()` to take a
system + user prompt and a Pydantic schema, returning a validated instance.
Errors at the API layer or schema validation level bubble up — callers
decide retry / fail-loud semantics.

`reasoning_effort` is the optional knob for OpenAI's GPT-5 family of reasoning
models (`none` / `minimal` / `low` / `medium` / `high` / `xhigh` — set
model-dependent). Non-reasoning models and providers that don't surface this
concept ignore it.
"""

from __future__ import annotations

from typing import Literal, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


class LLMProvider(Protocol):
    name: str
    model: str

    async def extract(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        reasoning_effort: ReasoningEffort | None = None,
    ) -> T: ...
