"""Tests for Anthropic provider runtime behavior and Anthropic-specific options."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from artel_ai.models import Done, Message, Role, ToolCall, ToolCallDelta, ToolDef, ToolParam
from artel_ai.providers.anthropic import AnthropicProvider


def _sse_lines(events: list[dict]) -> list[str]:
    return [f"data: {json.dumps(event)}" for event in events] + ["data: [DONE]"]


def _test_tool() -> ToolDef:
    return ToolDef(
        name="read_file",
        description="Read a file",
        parameters=[
            ToolParam(name="path", type="string", description="Path"),
        ],
    )


class TestAnthropicProviderHeaders:
    @pytest.mark.asyncio
    async def test_api_mode_merges_anthropic_beta_headers(self):
        provider = AnthropicProvider(
            api_key="sk-ant-test",
            beta_headers=["custom-beta-2025-01-01"],
            interleaved_thinking=True,
            fine_grained_tool_streaming=True,
            headers={"anthropic-beta": "manual-beta-2024-10-01"},
        )

        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 1},
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
                "claude-sonnet-4-20250514",
                [Message(role=Role.USER, content="Hi")],
                tools=[_test_tool()],
                thinking_level="medium",
            ):
                collected.append(event)

        assert isinstance(collected[-1], Done)
        headers = mock_stream.call_args.kwargs["headers"]
        assert headers["x-api-key"] == "sk-ant-test"
        beta_headers = set(headers["anthropic-beta"].split(","))
        assert beta_headers == {
            "manual-beta-2024-10-01",
            "custom-beta-2025-01-01",
            "interleaved-thinking-2025-05-14",
            "fine-grained-tool-streaming-2025-05-14",
        }

        await provider.close()

    @pytest.mark.asyncio
    async def test_oauth_mode_applies_claude_code_transforms(self):
        provider = AnthropicProvider(
            api_key="oauth-token",
            auth_type="oauth",
            beta_headers=["fine-grained-tool-streaming-2025-05-14"],
        )

        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 1}}},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 1},
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

        messages = [
            Message(role=Role.SYSTEM, content="System prompt"),
            Message(
                role=Role.ASSISTANT,
                tool_calls=[ToolCall(id="call-1", name="read_file", arguments={"path": "/foo"})],
            ),
        ]

        with patch.object(provider._client, "stream", return_value=mock_cm) as mock_stream:
            async for _ in provider.stream_chat(
                "claude-sonnet-4-20250514",
                messages,
                tools=[_test_tool()],
            ):
                pass

        assert mock_stream.call_args.args == ("POST", "/v1/messages?beta=true")
        headers = mock_stream.call_args.kwargs["headers"]
        body = mock_stream.call_args.kwargs["json"]

        assert headers["authorization"] == "Bearer oauth-token"
        assert headers["user-agent"] == "claude-cli/2.1.2 (external, cli)"
        beta_headers = set(headers["anthropic-beta"].split(","))
        assert beta_headers == {
            "oauth-2025-04-20",
            "interleaved-thinking-2025-05-14",
            "fine-grained-tool-streaming-2025-05-14",
        }
        assert body["system"].startswith(
            "You are Claude Code, Anthropic's official CLI for Claude."
        )
        assert body["tools"][0]["name"] == "mcp_read_file"
        assert body["messages"][0]["content"][0]["name"] == "mcp_read_file"

        await provider.close()


class TestAnthropicProviderStreaming:
    @pytest.mark.asyncio
    async def test_fine_grained_tool_streaming_emits_partial_tool_call_before_done(self):
        provider = AnthropicProvider(
            api_key="sk-ant-test",
            fine_grained_tool_streaming=True,
        )

        events = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 11}}},
            {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "id": "tool-1", "name": "read_file"},
            },
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": '{"path":"/tmp'},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "max_tokens"},
                "usage": {"output_tokens": 7},
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
                "claude-sonnet-4-20250514",
                [Message(role=Role.USER, content="Read file")],
                tools=[_test_tool()],
            ):
                collected.append(event)

        assert isinstance(collected[0], ToolCallDelta)
        assert collected[0].id == "tool-1"
        assert collected[0].name == "read_file"
        assert collected[0].arguments == {"_raw": '{"path":"/tmp'}
        assert isinstance(collected[1], Done)
        assert collected[1].stop_reason == "max_tokens"
        assert collected[1].usage.output_tokens == 7

        await provider.close()
