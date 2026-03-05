"""Ollama provider — local models via OpenAI-compatible API."""

from __future__ import annotations

from typing import Any

from worker_ai.models import ModelInfo
from worker_ai.providers.openai_compat import OpenAICompatProvider

_DEFAULT_BASE_URL = "http://localhost:11434/v1"


class OllamaProvider(OpenAICompatProvider):
    """Ollama — local models, no authentication needed."""

    name = "ollama"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(
            api_key=api_key or "ollama",  # Ollama accepts any key
            base_url=base_url or _DEFAULT_BASE_URL,
            models=[],
            **kwargs,
        )

    def list_models(self) -> list[ModelInfo]:
        # Ollama models are dynamic; return empty list.
        # Users specify model id directly (e.g. "qwen3:32b").
        return []
