"""Tests for Azure OpenAI provider behavior."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock, patch

import pytest
from worker_ai.models import Done, ImageAttachment, Message, ReasoningDelta, Role, TextDelta
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
    async def test_services_ai_base_url_uses_v1_and_strips_models_suffix(self):
        provider = AzureOpenAIProvider(
            api_key="azure-key",
            base_url="https://demo.services.ai.azure.com/models",
        )

        assert provider._base_url == "https://demo.services.ai.azure.com/openai/v1"

        await provider.close()

    @pytest.mark.asyncio
    async def test_list_models_direct_uses_v1_models_endpoint_and_api_key_header(self):
        provider = AzureOpenAIProvider(
            api_key="azure-key",
            base_url="https://demo.services.ai.azure.com/models",
        )

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = Mock(
            return_value={
                "data": [
                    {
                        "id": "Kimi-K2.5",
                        "name": "Kimi K2.5",
                        "capabilities": {"chat_completion": True},
                    }
                ]
            }
        )

        with patch.object(provider._client, "get", return_value=mock_response) as mock_get:
            models = await provider.list_models_direct()

        assert [model.id for model in models] == ["Kimi-K2.5"]
        assert mock_get.call_args.args == ("/models",)
        headers = mock_get.call_args.kwargs["headers"]
        assert headers["api-key"] == "azure-key"
        assert "authorization" not in headers

        await provider.close()

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
    async def test_stream_chat_includes_image_parts_for_vision_input(self, tmp_path):
        provider = AzureOpenAIProvider(
            api_key="azure-key",
            base_url="https://demo.openai.azure.com",
            api_version="2024-10-21",
        )

        image_path = tmp_path / "shot.png"
        image_path.write_bytes(b"png-data")
        _path, body, _headers = provider._build_chat_completions_request(
            "gpt-4.1",
            [
                Message(
                    role=Role.USER,
                    content="Look",
                    attachments=[
                        ImageAttachment(
                            path=str(image_path), mime_type="image/png", name="shot.png"
                        )
                    ],
                )
            ],
        )

        assert body["messages"][0]["content"][0] == {"type": "text", "text": "Look"}
        assert body["messages"][0]["content"][1]["type"] == "image_url"
        assert body["messages"][0]["content"][1]["image_url"]["url"].startswith(
            "data:image/png;base64,"
        )

        await provider.close()

    @pytest.mark.asyncio
    async def test_stream_chat_ignores_null_tool_calls_in_stream_chunks(self):
        provider = AzureOpenAIProvider(
            api_key="azure-key",
            base_url="https://demo.openai.azure.com",
            api_version="2024-10-21",
        )

        events = [
            {
                "choices": [
                    {
                        "delta": {"role": "assistant", "content": None, "tool_calls": None},
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {"content": "Hello", "tool_calls": None},
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

        with patch.object(provider._client, "stream", return_value=mock_cm):
            collected = []
            async for event in provider.stream_chat(
                "gpt-4.1",
                [Message(role=Role.USER, content="Hi")],
            ):
                collected.append(event)

        assert isinstance(collected[0], TextDelta)
        assert collected[0].content == "Hello"
        assert isinstance(collected[1], Done)

        await provider.close()

    @pytest.mark.asyncio
    async def test_stream_chat_falls_back_to_non_stream_when_foundry_stream_is_empty(self):
        provider = AzureOpenAIProvider(
            api_key="azure-key",
            base_url="https://demo.services.ai.azure.com",
        )

        stream_events = [
            {
                "choices": [],
                "prompt_filter_results": [
                    {
                        "prompt_index": 0,
                        "content_filter_results": {"hate": {"filtered": False, "severity": "safe"}},
                    }
                ],
            }
        ]

        mock_stream_response = AsyncMock()
        mock_stream_response.status_code = 200

        async def async_lines():
            for line in _sse_lines(stream_events):
                yield line

        mock_stream_response.aiter_lines = async_lines
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_stream_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        mock_post_response = AsyncMock()
        mock_post_response.status_code = 200
        mock_post_response.json = Mock(
            return_value={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "reasoning_content": "Reasoning",
                            "content": "Answer",
                            "tool_calls": None,
                        },
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            }
        )

        with (
            patch.object(provider._client, "stream", return_value=mock_cm) as mock_stream,
            patch.object(provider._client, "post", return_value=mock_post_response) as mock_post,
        ):
            collected = []
            async for event in provider.stream_chat(
                "Kimi-K2.5",
                [Message(role=Role.USER, content="Hi")],
            ):
                collected.append(event)

        assert [type(event) for event in collected] == [ReasoningDelta, TextDelta, Done]
        assert collected[0].content == "Reasoning"
        assert collected[1].content == "Answer"
        assert collected[2].usage.input_tokens == 5
        assert collected[2].usage.output_tokens == 2

        assert mock_stream.call_args.args == ("POST", "/chat/completions")
        assert mock_post.call_args.args == ("/chat/completions",)
        fallback_body = mock_post.call_args.kwargs["json"]
        fallback_headers = mock_post.call_args.kwargs["headers"]
        assert fallback_body["stream"] is False
        assert "stream_options" not in fallback_body
        assert fallback_body["model"] == "Kimi-K2.5"
        assert fallback_headers["api-key"] == "azure-key"

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
