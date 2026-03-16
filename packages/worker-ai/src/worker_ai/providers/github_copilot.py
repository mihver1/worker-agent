"""GitHub Copilot provider implementation."""

from __future__ import annotations

from typing import Any

from worker_ai.models import ModelInfo
from worker_ai.providers.openai_compat import OpenAICompatibleProvider

_DEFAULT_BASE_URL = "https://api.githubcopilot.com"

_GITHUB_COPILOT_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="gpt-4.1",
        provider="github_copilot",
        name="GPT-4.1",
        context_window=1_047_576,
        max_output_tokens=32_768,
        supports_tools=True,
        supports_vision=False,
        supports_reasoning=False,
        input_price_per_m=0.0,
        output_price_per_m=0.0,
    ),
    ModelInfo(
        id="o3-mini",
        provider="github_copilot",
        name="o3-mini",
        context_window=200_000,
        max_output_tokens=100_000,
        supports_tools=True,
        supports_vision=False,
        supports_reasoning=True,
        input_price_per_m=0.0,
        output_price_per_m=0.0,
    ),
    ModelInfo(
        id="claude-3.7-sonnet",
        provider="github_copilot",
        name="Claude 3.7 Sonnet",
        context_window=200_000,
        max_output_tokens=64_000,
        supports_tools=True,
        supports_vision=False,
        supports_reasoning=True,
        input_price_per_m=0.0,
        output_price_per_m=0.0,
    ),
    ModelInfo(
        id="claude-sonnet-4",
        provider="github_copilot",
        name="Claude Sonnet 4",
        context_window=200_000,
        max_output_tokens=64_000,
        supports_tools=True,
        supports_vision=False,
        supports_reasoning=True,
        input_price_per_m=0.0,
        output_price_per_m=0.0,
    ),
    ModelInfo(
        id="gemini-2.5-pro",
        provider="github_copilot",
        name="Gemini 2.5 Pro",
        context_window=1_048_576,
        max_output_tokens=65_536,
        supports_tools=True,
        supports_vision=False,
        supports_reasoning=True,
        input_price_per_m=0.0,
        output_price_per_m=0.0,
    ),
]


def _clone_models(provider_name: str) -> list[ModelInfo]:
    return [
        model.model_copy(update={"provider": provider_name}) for model in _GITHUB_COPILOT_MODELS
    ]


class GitHubCopilotProvider(OpenAICompatibleProvider):
    """GitHub Copilot Chat Completions-compatible provider."""

    name = "github_copilot"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        models: list[ModelInfo] | None = None,
        **kwargs: Any,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            models=models if models is not None else _clone_models(self.name),
            **kwargs,
        )

    def _default_base_url(self) -> str:
        return _DEFAULT_BASE_URL
