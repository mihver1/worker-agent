"""Tests for Ollama provider behavior across local and hosted endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from worker_ai.models import Done, Message, Role, TextDelta
from worker_ai.providers import create_default_registry
from worker_ai.providers.ollama import OllamaProvider


def _sse_lines(events: list[dict]) -> list[str]:
    return [f"data: {json.dumps(event)}" for event in events] + ["data: [DONE]"]


class TestOllamaProviderRegistry:
    @pytest.mark.asyncio
    async def test_registry_exposes_dedicated_ollama_provider(self):
        registry = create_default_registry()

        provider = registry.create("ollama")

        assert isinstance(provider, OllamaProvider)
        assert provider._base_url == "http://localhost:11434/v1"

        await provider.close()


class TestOllamaProviderRuntime:
    @pytest.mark.asyncio
    async def test_list_models_direct_uses_ollama_tags_endpoint(self):
        provider = OllamaProvider(base_url="https://ollama.com/v1", api_key="ollama_cloud_token")

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [
                {
                    "name": "gpt-oss:20b",
                    "model": "gpt-oss:20b",
                    "details": {"context_length": 200000},
                }
            ]
        }

        with patch.object(
            provider._client,
            "get",
            new=AsyncMock(return_value=mock_response),
        ) as mock_get:
            models = await provider.list_models_direct()

        assert models[0].id == "gpt-oss:20b"
        assert models[0].name == "gpt-oss:20b"
        assert models[0].context_window == 200000
        assert mock_get.call_args.args == ("https://ollama.com/api/tags",)
        assert mock_get.call_args.kwargs["headers"]["authorization"] == "Bearer ollama_cloud_token"

        await provider.close()
    @pytest.mark.asyncio
    async def test_stream_chat_supports_hosted_ollama_base_url_and_api_key(self):
        provider = OllamaProvider(
            api_key="ollama_cloud_token",
            base_url="https://ollama.com/v1",
        )

        events = [
            {
                "choices": [
                    {
                        "delta": {"content": "Hello"},
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            },
        ]

        mock_response = AsyncMock()
        mock_response.status_code = 200

        async def async_lines():
            for line in _sse_lines(events):
                yield line

        mock_response.aiter_lines = async_lines
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_cm) as mock_stream:
            collected = []
            async for event in provider.stream_chat(
                "gpt-oss:20b",
                [Message(role=Role.USER, content="Hi")],
            ):
                collected.append(event)

        assert provider._base_url == "https://ollama.com/v1"
        assert isinstance(collected[0], TextDelta)
        assert collected[0].content == "Hello"
        assert isinstance(collected[1], Done)
        assert collected[1].usage.input_tokens == 4
        assert collected[1].usage.output_tokens == 2

        assert mock_stream.call_args.args == ("POST", "/chat/completions")
        body = mock_stream.call_args.kwargs["json"]
        headers = mock_stream.call_args.kwargs["headers"]
        assert body["model"] == "gpt-oss:20b"
        assert body["messages"] == [{"role": "user", "content": "Hi"}]
        assert headers["authorization"] == "Bearer ollama_cloud_token"

        await provider.close()
