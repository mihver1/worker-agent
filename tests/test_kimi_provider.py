"""Tests for Kimi provider behavior on the Anthropic-compatible runtime."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from artel_ai.models import Done, Message, Role, TextDelta
from artel_ai.providers import create_default_registry
from artel_ai.providers.kimi import KimiProvider


def _sse_lines(events: list[dict], *, spaced: bool = True) -> list[str]:
    prefix = "data: " if spaced else "data:"
    return [f"{prefix}{json.dumps(event)}" for event in events] + [f"{prefix}[DONE]"]


class TestKimiProviderRegistry:
    @pytest.mark.asyncio
    async def test_registry_exposes_dedicated_kimi_provider(self):
        registry = create_default_registry()

        provider = registry.create("kimi", api_key="moonshot-token")

        assert isinstance(provider, KimiProvider)
        assert provider._base_url == "https://api.kimi.com/coding/v1"
        assert provider.list_models()[0].provider == "kimi"
        assert provider.list_models()[0].id == "kimi-k2.5"

        await provider.close()


class TestKimiProviderRuntime:
    @pytest.mark.asyncio
    async def test_stream_chat_uses_anthropic_messages_endpoint(self):
        provider = KimiProvider(api_key="moonshot-token")

        events = [
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 5}},
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hello"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 2},
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
                "kimi-k2.5",
                [Message(role=Role.USER, content="Hi")],
                thinking_level="high",
            ):
                collected.append(event)

        assert isinstance(collected[0], TextDelta)
        assert collected[0].content == "Hello"
        assert isinstance(collected[1], Done)
        assert collected[1].usage.input_tokens == 5
        assert collected[1].usage.output_tokens == 2
        assert collected[1].stop_reason == "end_turn"

        assert mock_stream.call_args.args == ("POST", "/messages")
        body = mock_stream.call_args.kwargs["json"]
        headers = mock_stream.call_args.kwargs["headers"]
        assert body["model"] == "kimi-k2.5"
        assert body["messages"] == [{"role": "user", "content": "Hi"}]
        assert body["stream"] is True
        assert body["thinking"] == {"type": "enabled", "budget_tokens": 8192}
        assert "stream_options" not in body
        assert "reasoning_effort" not in body
        assert headers["x-api-key"] == "moonshot-token"
        assert headers["anthropic-version"] == "2023-06-01"

        await provider.close()

    @pytest.mark.asyncio
    async def test_stream_chat_accepts_moonshot_sse_without_space_after_data_prefix(self):
        provider = KimiProvider(api_key="moonshot-token")

        events = [
            {
                "type": "message_start",
                "message": {"usage": {"input_tokens": 3}},
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hi"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 1},
            },
        ]

        mock_response = AsyncMock()
        mock_response.status_code = 200

        async def async_lines():
            for line in _sse_lines(events, spaced=False):
                yield line

        mock_response.aiter_lines = async_lines
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_cm):
            collected = []
            async for event in provider.stream_chat(
                "kimi-k2.5",
                [Message(role=Role.USER, content="Hi")],
            ):
                collected.append(event)

        assert isinstance(collected[0], TextDelta)
        assert collected[0].content == "Hi"
        assert isinstance(collected[1], Done)
        assert collected[1].usage.input_tokens == 3
        assert collected[1].usage.output_tokens == 1

        await provider.close()
