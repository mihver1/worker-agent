"""Tests for Azure OpenAI provider behavior."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from worker_ai.models import Done, Message, Role, TextDelta
from worker_ai.providers import create_default_registry
from worker_ai.providers.azure_openai import AzureOpenAIProvider
from worker_ai.providers.openai_compat import OpenAIProvider


def _sse_lines(events: list[dict]) -> list[str]:
    return [f"data: {json.dumps(event)}" for event in events] + ["data: [DONE]"]


class TestAzureOpenAIProviderRegistry:
    @pytest.mark.asyncio
    async def test_registry_separates_openai_and_azure_openai(self):
        registry = create_default_registry()

        openai_provider = registry.create("openai", api_key="sk-openai")
        azure_provider = registry.create(
            "azure_openai",
            api_key="azure-key",
            base_url="https://demo.openai.azure.com",
        )

        assert isinstance(openai_provider, OpenAIProvider)
        assert isinstance(azure_provider, AzureOpenAIProvider)
        assert openai_provider.list_models()[0].provider == "openai"
        assert azure_provider.list_models()[0].provider == "azure_openai"

        await openai_provider.close()
        await azure_provider.close()


class TestAzureOpenAIProviderRuntime:
    @pytest.mark.asyncio
    async def test_stream_chat_uses_azure_deployment_endpoint_and_api_key_header(self):
        provider = AzureOpenAIProvider(
            api_key="azure-key",
            base_url="https://demo.openai.azure.com",
            api_version="2024-10-21",
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
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
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
                "gpt-4.1",
                [Message(role=Role.USER, content="Hi")],
            ):
                collected.append(event)

        assert isinstance(collected[0], TextDelta)
        assert collected[0].content == "Hello"
        assert isinstance(collected[1], Done)
        assert collected[1].usage.input_tokens == 3
        assert collected[1].usage.output_tokens == 1

        assert mock_stream.call_args.args == (
            "POST",
            "/openai/deployments/gpt-4.1/chat/completions?api-version=2024-10-21",
        )
        body = mock_stream.call_args.kwargs["json"]
        headers = mock_stream.call_args.kwargs["headers"]
        assert "model" not in body
        assert body["messages"] == [{"role": "user", "content": "Hi"}]
        assert body["stream_options"] == {"include_usage": True}
        assert headers["api-key"] == "azure-key"
        assert "authorization" not in headers

        await provider.close()

    @pytest.mark.asyncio
    async def test_responses_mode_uses_v1_responses_endpoint(self):
        provider = AzureOpenAIProvider(
            api_key="azure-key",
            base_url="https://demo.openai.azure.com",
            api_type="responses",
        )

        events = [
            {"type": "response.output_text.delta", "delta": "Hello"},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                },
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
                "gpt-4.1",
                [Message(role=Role.USER, content="Hi")],
            ):
                collected.append(event)

        assert provider._base_url == "https://demo.openai.azure.com/openai/v1"
        assert isinstance(collected[0], TextDelta)
        assert collected[0].content == "Hello"
        assert isinstance(collected[1], Done)
        assert collected[1].usage.input_tokens == 4
        assert collected[1].usage.output_tokens == 2

        assert mock_stream.call_args.args == ("POST", "/responses")
        body = mock_stream.call_args.kwargs["json"]
        headers = mock_stream.call_args.kwargs["headers"]
        assert body["model"] == "gpt-4.1"
        assert body["input"] == [{"type": "message", "role": "user", "content": "Hi"}]
        assert headers["api-key"] == "azure-key"
        assert "authorization" not in headers

        await provider.close()
