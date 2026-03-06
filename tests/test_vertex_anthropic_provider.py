"""Tests for Anthropic on Vertex AI provider behavior."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from worker_ai.models import Done, Message, Role, ToolCallDelta, ToolDef, ToolParam
from worker_ai.providers import create_default_registry
from worker_ai.providers.anthropic import AnthropicProvider
from worker_ai.providers.anthropic_vertex import AnthropicVertexProvider


def _test_tool() -> ToolDef:
    return ToolDef(
        name="read_file",
        description="Read a file",
        parameters=[
            ToolParam(name="path", type="string", description="Path"),
        ],
    )


class TestAnthropicVertexProviderRegistry:
    @pytest.mark.asyncio
    async def test_registry_separates_anthropic_and_vertex_anthropic(self):
        registry = create_default_registry()

        anthropic_provider = registry.create("anthropic", api_key="sk-ant-test")
        vertex_provider = registry.create("vertex_anthropic", project="demo-project")

        assert isinstance(anthropic_provider, AnthropicProvider)
        assert isinstance(vertex_provider, AnthropicVertexProvider)
        assert anthropic_provider.list_models()[0].provider == "anthropic"
        assert vertex_provider.list_models()[0].provider == "vertex_anthropic"

        await anthropic_provider.close()
        await vertex_provider.close()


class TestAnthropicVertexProviderRuntime:
    @pytest.mark.asyncio
    async def test_stream_chat_uses_vertex_raw_predict_endpoint_and_anthropic_body(self):
        provider = AnthropicVertexProvider(
            project="demo-project",
            location="us-east5",
            beta_headers=["custom-beta-2025-01-01"],
            interleaved_thinking=True,
            fine_grained_tool_streaming=True,
            headers={"anthropic-beta": "manual-beta-2024-10-01"},
        )
        stream_payload = json.dumps(
            [
                {"type": "message_start", "message": {"usage": {"input_tokens": 3}}},
                {
                    "type": "content_block_start",
                    "content_block": {"type": "tool_use", "id": "tool-1", "name": "read_file"},
                },
                {
                    "type": "content_block_delta",
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"path":"/tmp/notes.txt"}',
                    },
                },
                {"type": "content_block_stop"},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use"},
                    "usage": {"output_tokens": 2},
                },
            ]
        )

        mock_response = AsyncMock()
        mock_response.status_code = 200

        async def async_text():
            midpoint = len(stream_payload) // 2
            yield stream_payload[:midpoint]
            yield stream_payload[midpoint:]

        mock_response.aiter_text = async_text
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                provider,
                "_resolve_access_token",
                return_value=("vertex-token", "demo-project"),
            ),
            patch.object(provider._client, "stream", return_value=mock_cm) as mock_stream,
        ):
            collected = []
            async for event in provider.stream_chat(
                "claude-sonnet-4@20250514",
                [Message(role=Role.USER, content="Read this file")],
                tools=[_test_tool()],
                temperature=0.25,
                max_tokens=2048,
                thinking_level="medium",
            ):
                collected.append(event)

        assert isinstance(collected[0], ToolCallDelta)
        assert collected[0].id == "tool-1"
        assert collected[0].name == "read_file"
        assert collected[0].arguments == {"path": "/tmp/notes.txt"}
        assert isinstance(collected[1], Done)
        assert collected[1].stop_reason == "tool_use"
        assert collected[1].usage.input_tokens == 3
        assert collected[1].usage.output_tokens == 2

        assert mock_stream.call_args.args == (
            "POST",
            "https://us-east5-aiplatform.googleapis.com/v1/projects/demo-project/"
            "locations/us-east5/publishers/anthropic/models/claude-sonnet-4@20250514:"
            "streamRawPredict",
        )
        headers = mock_stream.call_args.kwargs["headers"]
        body = mock_stream.call_args.kwargs["json"]
        assert headers["authorization"] == "Bearer vertex-token"
        assert headers["content-type"] == "application/json"
        assert set(headers["anthropic-beta"].split(",")) == {
            "manual-beta-2024-10-01",
            "custom-beta-2025-01-01",
            "interleaved-thinking-2025-05-14",
            "fine-grained-tool-streaming-2025-05-14",
        }
        assert body == {
            "messages": [{"role": "user", "content": "Read this file"}],
            "max_tokens": 2048,
            "temperature": 0.25,
            "stream": True,
            "anthropic_version": "vertex-2023-10-16",
            "thinking": {"type": "enabled", "budget_tokens": 4096},
            "tools": [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Path"}
                        },
                        "required": ["path"],
                    },
                }
            ],
        }

        await provider.close()

    @pytest.mark.asyncio
    async def test_stream_chat_parses_tool_use_from_final_vertex_message_object(self):
        provider = AnthropicVertexProvider(
            project="demo-project",
            location="us-east5",
        )
        stream_payload = json.dumps(
            [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-final",
                            "tool_name": "read_file",
                            "tool_input": {"path": "/tmp/final.txt"},
                        }
                    ],
                    "stop_reason": "tool_use",
                    "usage": {
                        "input_tokens": 5,
                        "output_tokens": 7,
                    },
                }
            ]
        )

        mock_response = AsyncMock()
        mock_response.status_code = 200

        async def async_text():
            yield stream_payload

        mock_response.aiter_text = async_text
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                provider,
                "_resolve_access_token",
                return_value=("vertex-token", "demo-project"),
            ),
            patch.object(provider._client, "stream", return_value=mock_cm),
        ):
            collected = []
            async for event in provider.stream_chat(
                "claude-sonnet-4@20250514",
                [Message(role=Role.USER, content="Use a tool")],
            ):
                collected.append(event)

        assert isinstance(collected[0], ToolCallDelta)
        assert collected[0].id == "tool-final"
        assert collected[0].name == "read_file"
        assert collected[0].arguments == {"path": "/tmp/final.txt"}
        assert isinstance(collected[1], Done)
        assert collected[1].stop_reason == "tool_use"
        assert collected[1].usage.input_tokens == 5
        assert collected[1].usage.output_tokens == 7

        await provider.close()
