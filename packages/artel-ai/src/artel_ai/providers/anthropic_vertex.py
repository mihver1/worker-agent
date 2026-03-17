"""Anthropic Claude on Vertex AI."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from artel_ai.models import Message, ModelInfo, StreamEvent, ToolDef, Usage
from artel_ai.provider import merge_headers
from artel_ai.providers.anthropic import (
    _FINE_GRAINED_TOOL_STREAMING_BETA,
    _INTERLEAVED_THINKING_BETA,
    _build_request_body,
    _consume_stream_event,
    _merge_beta_headers,
    _normalize_beta_headers,
)
from artel_ai.providers.google import GoogleVertexProvider, _iter_vertex_stream_objects

_VERTEX_ANTHROPIC_API_VERSION = "vertex-2023-10-16"
_VERTEX_ANTHROPIC_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="claude-sonnet-4@20250514",
        provider="vertex_anthropic",
        name="Claude Sonnet 4 on Vertex AI",
        context_window=200_000,
        max_output_tokens=16_384,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=True,
    ),
    ModelInfo(
        id="claude-opus-4@20250514",
        provider="vertex_anthropic",
        name="Claude Opus 4 on Vertex AI",
        context_window=200_000,
        max_output_tokens=32_000,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=True,
    ),
    ModelInfo(
        id="claude-3-5-haiku@20241022",
        provider="vertex_anthropic",
        name="Claude 3.5 Haiku on Vertex AI",
        context_window=200_000,
        max_output_tokens=8_192,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=False,
    ),
]


class AnthropicVertexProvider(GoogleVertexProvider):
    """Anthropic Claude models served through Vertex AI rawPredict."""

    name = "vertex_anthropic"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self._beta_headers = _normalize_beta_headers(kwargs.get("beta_headers"))
        self._interleaved_thinking = bool(kwargs.get("interleaved_thinking", False))
        self._fine_grained_tool_streaming = bool(kwargs.get("fine_grained_tool_streaming", False))

    async def stream_chat(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking_level: str = "off",
    ) -> AsyncIterator[StreamEvent]:
        access_token, project = self._resolve_access_token()
        body = _build_request_body(
            messages,
            model=None,
            anthropic_version=_VERTEX_ANTHROPIC_API_VERSION,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )
        requested_betas: list[str] = list(self._beta_headers)
        if thinking_level != "off" and self._interleaved_thinking:
            requested_betas.append(_INTERLEAVED_THINKING_BETA)
        if tools and self._fine_grained_tool_streaming:
            requested_betas.append(_FINE_GRAINED_TOOL_STREAMING_BETA)

        headers = merge_headers(
            {
                "authorization": f"Bearer {access_token}",
                "content-type": "application/json",
            },
            self.headers,
        )
        merged_betas = _merge_beta_headers(headers.get("anthropic-beta"), requested_betas)
        if merged_betas:
            headers["anthropic-beta"] = merged_betas

        url = (
            f"{self._base_url}/v1/projects/{project}/locations/{self._location}"
            f"/publishers/anthropic/models/{model}:streamRawPredict"
        )
        async with self._client.stream("POST", url, json=body, headers=headers) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                raise RuntimeError(
                    f"Vertex Anthropic API error {response.status_code}: {error_body.decode()}"
                )

            current_tool_id: str | None = None
            current_tool_name: str | None = None
            tool_input_json = ""
            usage = Usage()

            async for event in _iter_vertex_stream_objects(response):
                emitted, current_tool_id, current_tool_name, tool_input_json = (
                    _consume_stream_event(
                        event,
                        usage,
                        current_tool_id,
                        current_tool_name,
                        tool_input_json,
                    )
                )
                for stream_event in emitted:
                    yield stream_event

    def list_models(self) -> list[ModelInfo]:
        return [model.model_copy() for model in _VERTEX_ANTHROPIC_MODELS]
