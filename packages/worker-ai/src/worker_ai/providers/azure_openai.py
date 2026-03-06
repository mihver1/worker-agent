"""Azure OpenAI provider with Azure-specific routing and auth semantics."""

from __future__ import annotations

from typing import Any

from worker_ai.models import Message, ModelInfo, ToolDef
from worker_ai.provider import merge_headers
from worker_ai.providers.openai_compat import (
    _OPENAI_MODELS,
    OpenAIProvider,
    _build_chat_completions_body,
    _build_responses_body,
    _clone_models,
)

_DEFAULT_AZURE_API_VERSION = "2024-10-21"


def _looks_like_v1_base_url(base_url: str | None) -> bool:
    return (base_url or "").rstrip("/").endswith("/openai/v1")


def _normalize_azure_base_url(base_url: str | None, *, use_v1: bool) -> str:
    raw_base_url = (base_url or "").rstrip("/")
    if not raw_base_url:
        return ""
    if use_v1:
        if raw_base_url.endswith("/openai/v1"):
            return raw_base_url
        if raw_base_url.endswith("/openai"):
            return f"{raw_base_url}/v1"
        return f"{raw_base_url}/openai/v1"
    if raw_base_url.endswith("/openai/v1"):
        return raw_base_url[: -len("/openai/v1")]
    if raw_base_url.endswith("/openai"):
        return raw_base_url[: -len("/openai")]
    return raw_base_url


class AzureOpenAIProvider(OpenAIProvider):
    """Azure OpenAI provider supporting both deployment and v1-style endpoints."""

    name = "azure_openai"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        models: list[ModelInfo] | None = None,
        **kwargs: Any,
    ):
        api_type = str(kwargs.get("api_type", "chat") or "chat")
        self._use_v1_api = api_type == "responses" or _looks_like_v1_base_url(base_url)
        self._api_version = str(
            kwargs.get("api_version", _DEFAULT_AZURE_API_VERSION)
            or _DEFAULT_AZURE_API_VERSION
        )
        normalized_base_url = _normalize_azure_base_url(base_url, use_v1=self._use_v1_api)
        super().__init__(
            api_key=api_key,
            base_url=normalized_base_url,
            models=models if models is not None else _clone_models(self.name, _OPENAI_MODELS),
            **kwargs,
        )

    def _default_base_url(self) -> str:
        return ""

    def _azure_headers(self) -> dict[str, str]:
        return merge_headers(
            {"content-type": "application/json"},
            {"api-key": self.api_key} if self.api_key else None,
            self.headers,
        )

    def _build_chat_completions_request(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking_level: str = "off",
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        body = _build_chat_completions_body(
            model,
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )
        if not self._use_v1_api:
            body.pop("model", None)
            path = f"/openai/deployments/{model}/chat/completions?api-version={self._api_version}"
        else:
            path = "/chat/completions"
        return path, body, self._azure_headers()

    def _build_responses_request(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking_level: str = "off",
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        body = _build_responses_body(
            model,
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )
        return "/responses", body, self._azure_headers()
