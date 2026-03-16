"""Ollama provider — local or hosted Ollama endpoints via OpenAI-compatible API."""

from __future__ import annotations

from typing import Any

from worker_ai.models import ModelInfo
from worker_ai.provider import merge_headers
from worker_ai.providers.openai_compat import OpenAICompatibleProvider

_DEFAULT_BASE_URL = "http://localhost:11434/v1"


class OllamaProvider(OpenAICompatibleProvider):
    """Ollama — local by default, but also usable against hosted Ollama endpoints."""

    name = "ollama"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(
            api_key=api_key or "ollama",  # Ollama accepts any key
            base_url=base_url or _DEFAULT_BASE_URL,
            models=[],
            **kwargs,
        )

    def _api_base_url(self) -> str:
        if self._base_url.endswith("/api"):
            return self._base_url
        if self._base_url.endswith("/v1"):
            return f"{self._base_url[:-3]}/api"
        return f"{self._base_url}/api"

    def list_models(self) -> list[ModelInfo]:
        # Ollama models are dynamic; return empty list.
        # Users specify model id directly (e.g. "qwen3:32b").
        return []

    async def list_models_direct(self) -> list[ModelInfo]:
        headers = merge_headers(
            {"authorization": f"Bearer {self.api_key}"} if self.api_key else None,
            self.headers,
        )
        try:
            response = await self._client.get(
                f"{self._api_base_url()}/tags",
                headers=headers,
                timeout=5.0,
            )
            if response.status_code != 200:
                return self.list_models()
            payload = response.json()
        except Exception:
            return self.list_models()

        raw_models = payload.get("models", [])
        if not isinstance(raw_models, list):
            return self.list_models()

        models: list[ModelInfo] = []
        for raw_model in raw_models:
            if not isinstance(raw_model, dict):
                continue
            model_id = str(raw_model.get("model") or raw_model.get("name") or "").strip()
            if not model_id:
                continue
            details = raw_model.get("details", {})
            context_window = 128_000
            if isinstance(details, dict):
                try:
                    context_window = int(details.get("context_length") or context_window)
                except (TypeError, ValueError):
                    context_window = 128_000
            models.append(
                ModelInfo(
                    id=model_id,
                    provider=self.name,
                    name=str(raw_model.get("name") or model_id),
                    context_window=context_window,
                )
            )
        return models
