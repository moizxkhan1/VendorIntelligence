"""LLM provider adapter — env-driven, provider-agnostic.

Reads LLM_PROVIDER / LLM_MODEL / LLM_API_KEY / LLM_BASE_URL from the env.
Concrete providers are lazy-imported so we don't pay the OpenAI SDK import
cost when running with Anthropic, and vice versa.
"""

from __future__ import annotations

from app.config import settings
from app.llm.base import LLMProvider


def get_llm() -> LLMProvider:
    p = settings.llm_provider.lower()

    if p in ("openai", "openai-compat"):
        from app.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url or None,
        )
    if p == "anthropic":
        from app.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")


__all__ = ["LLMProvider", "get_llm"]
