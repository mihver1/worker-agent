"""Kimi (Moonshot AI) provider — OpenAI-compatible with Kimi-specific models."""

from __future__ import annotations

from typing import Any

from worker_ai.models import ModelInfo
from worker_ai.providers.openai_compat import OpenAICompatProvider

_DEFAULT_BASE_URL = "https://api.kimi.com/coding/v1"

_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="kimi-k2.5",
        provider="kimi",
        name="Kimi K2.5",
        context_window=262_144,
        max_output_tokens=16_384,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=True,
        input_price_per_m=0.50,
        output_price_per_m=2.80,
    ),
    ModelInfo(
        id="kimi-k2-thinking-turbo",
        provider="kimi",
        name="Kimi K2 Thinking Turbo",
        context_window=131_072,
        max_output_tokens=16_384,
        supports_tools=True,
        supports_vision=False,
        supports_reasoning=True,
        input_price_per_m=0.40,
        output_price_per_m=2.0,
    ),
]


class KimiProvider(OpenAICompatProvider):
    """Kimi (Moonshot AI) — extends OpenAI-compatible provider."""

    name = "kimi"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(
            api_key=api_key,
            base_url=base_url or _DEFAULT_BASE_URL,
            models=_MODELS,
            **kwargs,
        )
