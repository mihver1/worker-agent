"""Kimi For Coding provider — Anthropic-compatible Moonshot endpoint."""

from __future__ import annotations

from typing import Any

from artel_ai.models import ModelInfo
from artel_ai.providers.anthropic import AnthropicProvider

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


class KimiProvider(AnthropicProvider):
    """Kimi For Coding — Moonshot endpoint exposed via the Anthropic message shape."""

    name = "kimi"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(
            api_key=api_key,
            base_url=base_url or _DEFAULT_BASE_URL,
            **kwargs,
        )

    def list_models(self) -> list[ModelInfo]:
        return list(_MODELS)
