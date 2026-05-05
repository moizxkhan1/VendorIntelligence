"""Anthropic provider — structured output via tool use.

We register a single tool whose input_schema is the Pydantic schema's
JSON Schema, then force the model to call it via `tool_choice`. The
tool's `input` is the structured payload we hand back as a validated
Pydantic instance.
"""

from __future__ import annotations

from typing import TypeVar

from anthropic import AsyncAnthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_TOOL_NAME = "submit_extraction"


class AnthropicProvider:
    name: str = "anthropic"
    model: str

    def __init__(self, *, api_key: str, model: str, max_tokens: int = 4096) -> None:
        if not api_key:
            raise ValueError("LLM_API_KEY is empty — set it in .env")
        self._client = AsyncAnthropic(api_key=api_key)
        self.model = model
        self._max_tokens = max_tokens

    async def extract(self, *, system: str, user: str, schema: type[T]) -> T:
        response = await self._client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[
                {
                    "name": _TOOL_NAME,
                    "description": f"Submit a {schema.__name__}.",
                    "input_schema": schema.model_json_schema(),
                }
            ],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
        )
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
                return schema.model_validate(block.input)
        raise RuntimeError(
            f"Anthropic returned no {_TOOL_NAME!r} tool_use block "
            f"(stop_reason={response.stop_reason})"
        )
