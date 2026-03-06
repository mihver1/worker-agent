"""LM Studio provider — local OpenAI-compatible runtime with direct model discovery."""

from __future__ import annotations

from typing import Any

from worker_ai.models import ModelInfo
from worker_ai.provider import merge_headers
from worker_ai.providers.openai_compat import (
    OpenAICompatibleProvider,
    _bool_or_default,
    _int_or_default,
)

_DEFAULT_BASE_URL = "http://127.0.0.1:1234/v1"


class LMStudioProvider(OpenAICompatibleProvider):
    """LM Studio runtime with native direct model discovery."""

    name = "lmstudio"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(
            api_key=api_key,
            base_url=base_url or _DEFAULT_BASE_URL,
            models=[],
            **kwargs,
        )

    def _native_api_base_url(self) -> str:
        if self._base_url.endswith("/api/v1"):
            return self._base_url
        if self._base_url.endswith("/v1"):
            return f"{self._base_url[:-3]}/api/v1"
        if self._base_url.endswith("/api"):
            return f"{self._base_url}/v1"
        return f"{self._base_url}/api/v1"

    async def list_models_direct(self) -> list[ModelInfo]:
        headers = merge_headers(
            {"authorization": f"Bearer {self.api_key}"} if self.api_key else None,
            self.headers,
        )
        try:
            response = await self._client.get(
                f"{self._native_api_base_url()}/models",
                headers=headers,
                timeout=5.0,
            )
            if response.status_code == 200:
                payload = response.json()
                raw_models = payload.get("data") or payload.get("models") or []
                if isinstance(raw_models, list):
                    models: list[ModelInfo] = []
                    for raw_model in raw_models:
                        if not isinstance(raw_model, dict):
                            continue
                        model_type = str(raw_model.get("type", "")).strip().lower()
                        if model_type and model_type != "llm":
                            continue
                        model_id = str(
                            raw_model.get("id")
                            or raw_model.get("key")
                            or raw_model.get("model_key")
                            or ""
                        ).strip()
                        if not model_id:
                            continue
                        models.append(
                            ModelInfo(
                                id=model_id,
                                provider=self.name,
                                name=str(
                                    raw_model.get("display_name")
                                    or raw_model.get("name")
                                    or model_id
                                ),
                                context_window=_int_or_default(
                                    raw_model.get("max_context_length")
                                    or raw_model.get("context_window"),
                                    128_000,
                                ),
                                max_output_tokens=_int_or_default(
                                    raw_model.get("max_output_tokens"),
                                    8_192,
                                ),
                                supports_tools=_bool_or_default(
                                    raw_model.get("supports_tools")
                                    or raw_model.get("trained_for_tool_use"),
                                    True,
                                ),
                                supports_vision=_bool_or_default(
                                    raw_model.get("supports_vision") or raw_model.get("vision"),
                                    False,
                                ),
                                supports_reasoning=_bool_or_default(
                                    raw_model.get("supports_reasoning")
                                    or raw_model.get("reasoning"),
                                    False,
                                ),
                            )
                        )
                    if models:
                        return models
        except Exception:
            pass

        return await super().list_models_direct()
