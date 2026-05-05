"""OpenAI provider — also covers any OpenAI-compatible endpoint.

OpenRouter, Groq, Together, Azure OpenAI, and local vLLM all expose the same
chat-completions surface and accept the same JSON Schema response_format —
just point `LLM_BASE_URL` at them and pick a model name. We use the SDK's
`.parse()` helper which sets `response_format` from the Pydantic schema and
parses the response back into a validated instance.
"""

from __future__ import annotations

from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.llm.base import ReasoningEffort

T = TypeVar("T", bound=BaseModel)


class OpenAIProvider:
    name: str
    model: str

    def __init__(self, *, api_key: str, model: str, base_url: str | None = None) -> None:
        if not api_key:
            raise ValueError("LLM_API_KEY is empty — set it in .env")
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.name = "openai-compat" if base_url else "openai"

    async def extract(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        reasoning_effort: ReasoningEffort | None = None,
    ) -> T:
        # Forward reasoning_effort only when set — non-reasoning models reject the parameter.
        extra: dict[str, str] = {}
        if reasoning_effort is not None:
            extra["reasoning_effort"] = reasoning_effort

        completion = await self._client.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format=schema,
            **extra,
        )
        msg = completion.choices[0].message
        if msg.refusal:
            raise RuntimeError(f"OpenAI refusal: {msg.refusal}")
        if msg.parsed is None:
            raise RuntimeError("OpenAI returned no parsed payload")
        return msg.parsed
