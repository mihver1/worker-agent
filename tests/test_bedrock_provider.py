"""Tests for Amazon Bedrock provider behavior."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from artel_ai.models import (
    Done,
    ImageAttachment,
    Message,
    ReasoningDelta,
    Role,
    TextDelta,
    ToolCallDelta,
    ToolDef,
    ToolParam,
)
from artel_ai.providers import create_default_registry
from artel_ai.providers.bedrock import BedrockProvider, _build_messages
from artel_ai.providers.openai_compat import OpenAIProvider


def _test_tool() -> ToolDef:
    return ToolDef(
        name="read_file",
        description="Read a file",
        parameters=[
            ToolParam(name="path", type="string", description="Path"),
        ],
    )


class TestBedrockProviderRegistry:
    @pytest.mark.asyncio
    async def test_registry_separates_openai_and_bedrock(self):
        registry = create_default_registry()

        openai_provider = registry.create("openai", api_key="sk-openai")
        bedrock_provider = registry.create("bedrock", region="us-east-1")

        assert isinstance(openai_provider, OpenAIProvider)
        assert isinstance(bedrock_provider, BedrockProvider)
        assert openai_provider.list_models()[0].provider == "openai"
        assert bedrock_provider.list_models()[0].provider == "bedrock"

        await openai_provider.close()


class TestBedrockProviderRuntime:
    def test_build_messages_includes_image_content_blocks(self, tmp_path):
        image_path = tmp_path / "shot.png"
        image_path.write_bytes(b"png-data")

        system, messages = _build_messages(
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
            ]
        )

        assert system == []
        assert messages[0]["role"] == "user"
        assert messages[0]["content"][0] == {"text": "Look"}
        assert messages[0]["content"][1]["image"]["format"] == "png"
        assert messages[0]["content"][1]["image"]["source"]["bytes"]

    @pytest.mark.asyncio
    async def test_stream_chat_uses_converse_stream_request_and_parses_events(self):
        provider = BedrockProvider(region="us-east-1")
        mock_client = Mock()
        mock_client.converse_stream = Mock(
            return_value={
                "stream": [
                    {
                        "contentBlockDelta": {
                            "contentBlockIndex": 0,
                            "delta": {"reasoningContent": {"text": "Thinking..."}},
                        }
                    },
                    {
                        "contentBlockDelta": {
                            "contentBlockIndex": 0,
                            "delta": {"text": "Hello"},
                        }
                    },
                    {
                        "contentBlockStart": {
                            "contentBlockIndex": 1,
                            "start": {
                                "toolUse": {
                                    "toolUseId": "tool-1",
                                    "name": "read_file",
                                }
                            },
                        }
                    },
                    {
                        "contentBlockDelta": {
                            "contentBlockIndex": 1,
                            "delta": {"toolUse": {"input": '{"path":"/tmp/notes.txt"}'}},
                        }
                    },
                    {"contentBlockStop": {"contentBlockIndex": 1}},
                    {"messageStop": {"stopReason": "tool_use"}},
                    {"metadata": {"usage": {"inputTokens": 3, "outputTokens": 2}}},
                ]
            }
        )
        provider._client = mock_client

        collected = []
        async for event in provider.stream_chat(
            "anthropic.claude-3-7-sonnet-20250219-v1:0",
            [
                Message(role=Role.SYSTEM, content="System prompt"),
                Message(role=Role.USER, content="Read this file"),
            ],
            tools=[_test_tool()],
            temperature=0.25,
            max_tokens=8192,
            thinking_level="medium",
        ):
            collected.append(event)

        assert isinstance(collected[0], ReasoningDelta)
        assert collected[0].content == "Thinking..."
        assert isinstance(collected[1], TextDelta)
        assert collected[1].content == "Hello"
        assert isinstance(collected[2], ToolCallDelta)
        assert collected[2].id == "tool-1"
        assert collected[2].name == "read_file"
        assert collected[2].arguments == {"path": "/tmp/notes.txt"}
        assert isinstance(collected[3], Done)
        assert collected[3].stop_reason == "tool_use"
        assert collected[3].usage.input_tokens == 3
        assert collected[3].usage.output_tokens == 2

        assert mock_client.converse_stream.call_args.kwargs == {
            "modelId": "anthropic.claude-3-7-sonnet-20250219-v1:0",
            "messages": [
                {"role": "user", "content": [{"text": "Read this file"}]},
            ],
            "system": [{"text": "System prompt"}],
            "inferenceConfig": {"temperature": 0.25, "maxTokens": 8192},
            "additionalModelRequestFields": {
                "thinking": {"type": "enabled", "budget_tokens": 4096}
            },
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": "read_file",
                            "description": "Read a file",
                            "inputSchema": {
                                "json": {
                                    "type": "object",
                                    "properties": {
                                        "path": {
                                            "type": "string",
                                            "description": "Path",
                                        }
                                    },
                                    "required": ["path"],
                                }
                            },
                        }
                    }
                ]
            },
        }
