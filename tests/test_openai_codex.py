"""Tests for OpenAI Codex / Responses API support (OAuth / ChatGPT Plus)."""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from worker_ai.models import (
    Done,
    Message,
    ReasoningDelta,
    Role,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolDef,
    ToolParam,
    ToolResult,
)
from worker_ai.oauth import extract_openai_account_id, parse_jwt_claims
from worker_ai.providers import create_default_registry
from worker_ai.providers.openai_compat import (
    OpenAICompatibleProvider,
    OpenAIProvider,
    _build_responses_input,
    _build_responses_tools,
)

# ── JWT helpers ──────────────────────────────────────────────────


def _make_jwt(payload: dict[str, Any]) -> str:
    """Build a fake (unsigned) JWT with the given payload."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


class TestParseJwtClaims:
    def test_valid_jwt(self):
        token = _make_jwt({"sub": "user-123", "chatgpt_account_id": "acct-abc"})
        claims = parse_jwt_claims(token)
        assert claims["sub"] == "user-123"
        assert claims["chatgpt_account_id"] == "acct-abc"

    def test_invalid_jwt_not_three_parts(self):
        assert parse_jwt_claims("not.a.valid.jwt.token") == {}
        assert parse_jwt_claims("onlyone") == {}
        assert parse_jwt_claims("") == {}

    def test_invalid_jwt_bad_base64(self):
        assert parse_jwt_claims("a.!!!.b") == {}


class TestExtractOpenaiAccountId:
    def test_direct_field(self):
        assert extract_openai_account_id({"chatgpt_account_id": "acct-1"}) == "acct-1"

    def test_nested_auth_field(self):
        claims = {
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct-2"},
        }
        assert extract_openai_account_id(claims) == "acct-2"

    def test_organizations_fallback(self):
        claims = {"organizations": [{"id": "org-3"}]}
        assert extract_openai_account_id(claims) == "org-3"

    def test_empty_claims(self):
        assert extract_openai_account_id({}) == ""

    def test_priority_order(self):
        """Direct field takes precedence over nested or organizations."""
        claims = {
            "chatgpt_account_id": "acct-direct",
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct-nested"},
            "organizations": [{"id": "org-fallback"}],
        }
        assert extract_openai_account_id(claims) == "acct-direct"


# ── Responses API builders ────────────────────────────────────────


class TestBuildResponsesInput:
    def test_system_extracted_as_instructions(self):
        messages = [
            Message(role=Role.SYSTEM, content="You are helpful."),
            Message(role=Role.USER, content="Hi"),
        ]
        instructions, items = _build_responses_input(messages)
        assert instructions == "You are helpful."
        assert len(items) == 1
        assert items[0] == {"type": "message", "role": "user", "content": "Hi"}

    def test_user_and_assistant_messages(self):
        messages = [
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi there!"),
        ]
        instructions, items = _build_responses_input(messages)
        assert instructions is None
        assert len(items) == 2
        assert items[0] == {"type": "message", "role": "user", "content": "Hello"}
        assert items[1] == {"type": "message", "role": "assistant", "content": "Hi there!"}

    def test_tool_calls_and_results(self):
        messages = [
            Message(role=Role.USER, content="Read file"),
            Message(
                role=Role.ASSISTANT,
                content="",
                tool_calls=[ToolCall(id="call-1", name="read_file", arguments={"path": "/foo"})],
            ),
            Message(
                role=Role.TOOL,
                tool_result=ToolResult(tool_call_id="call-1", content="file contents"),
            ),
        ]
        instructions, items = _build_responses_input(messages)
        assert instructions is None
        assert len(items) == 3
        assert items[0] == {"type": "message", "role": "user", "content": "Read file"}
        assert items[1] == {
            "type": "function_call",
            "name": "read_file",
            "arguments": '{"path": "/foo"}',
            "call_id": "call-1",
        }
        assert items[2] == {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": "file contents",
        }

    def test_assistant_with_text_and_tool_calls(self):
        messages = [
            Message(
                role=Role.ASSISTANT,
                content="Let me check...",
                tool_calls=[ToolCall(id="c1", name="ls", arguments={"dir": "."})],
            ),
        ]
        _, items = _build_responses_input(messages)
        # Should emit text message + function_call
        assert len(items) == 2
        assert items[0]["type"] == "message"
        assert items[0]["content"] == "Let me check..."
        assert items[1]["type"] == "function_call"

    def test_empty_messages(self):
        instructions, items = _build_responses_input([])
        assert instructions is None
        assert items == []


class TestBuildResponsesTools:
    def test_basic_tool(self):
        tools = [
            ToolDef(
                name="read_file",
                description="Read a file",
                parameters=[
                    ToolParam(name="path", type="string", description="File path", required=True),
                ],
            ),
        ]
        result = _build_responses_tools(tools)
        assert len(result) == 1
        assert result[0] == {
            "type": "function",
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                },
                "required": ["path"],
            },
        }

    def test_multiple_params_with_enum(self):
        tools = [
            ToolDef(
                name="write_file",
                description="Write",
                parameters=[
                    ToolParam(name="path", type="string", description="Path"),
                    ToolParam(
                        name="mode",
                        type="string",
                        description="Mode",
                        required=False,
                        enum=["overwrite", "append"],
                    ),
                ],
            ),
        ]
        result = _build_responses_tools(tools)
        assert result[0]["parameters"]["properties"]["mode"]["enum"] == ["overwrite", "append"]
        assert result[0]["parameters"]["required"] == ["path"]

    def test_empty_tools(self):
        assert _build_responses_tools([]) == []


# ── OpenAICompatProvider OAuth init ───────────────────────────────


class TestOpenAICompatProviderOAuth:
    def test_oauth_sets_codex_base_url(self):
        token = _make_jwt({"chatgpt_account_id": "acct-test"})
        provider = OpenAIProvider(api_key=token, auth_type="oauth")
        assert provider._auth_type == "oauth"
        assert "chatgpt.com" in provider._base_url
        assert provider._account_id == "acct-test"

    def test_api_key_mode_default(self):
        provider = OpenAIProvider(api_key="sk-test")
        assert provider._auth_type == "api"
        assert "api.openai.com" in provider._base_url
        assert provider._account_id == ""


class TestOpenAIProviderSplit:
    @pytest.mark.asyncio
    async def test_registry_separates_openai_and_openai_compat(self):
        registry = create_default_registry()

        openai_provider = registry.create("openai", api_key="sk-openai")
        compat_provider = registry.create(
            "openai_compat",
            api_key="sk-compatible",
            base_url="https://proxy.example/v1",
        )

        assert isinstance(openai_provider, OpenAIProvider)
        assert isinstance(compat_provider, OpenAICompatibleProvider)
        assert type(openai_provider) is OpenAIProvider
        assert type(compat_provider) is OpenAICompatibleProvider
        assert openai_provider.list_models()
        assert compat_provider.list_models() == []

        await openai_provider.close()
        await compat_provider.close()

    @pytest.mark.asyncio
    async def test_api_responses_mode_uses_responses_endpoint(self):
        provider = OpenAIProvider(api_key="sk-test", api_type="responses")

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
            async for ev in provider.stream_chat(
                "gpt-4.1",
                [Message(role=Role.USER, content="Hi")],
            ):
                collected.append(ev)

        assert len(collected) == 2
        assert isinstance(collected[0], TextDelta)
        assert isinstance(collected[1], Done)

        assert mock_stream.call_args.args == ("POST", "/responses")
        body = mock_stream.call_args.kwargs["json"]
        assert body["model"] == "gpt-4.1"
        assert body["input"] == [{"type": "message", "role": "user", "content": "Hi"}]
        assert body["reasoning"] == {"effort": "none", "summary": "auto"}

        await provider.close()


# ── _stream_codex SSE parsing ─────────────────────────────────────


def _sse_lines(events: list[dict[str, Any]]) -> list[str]:
    """Convert a list of event dicts to SSE-formatted lines."""
    lines = []
    for ev in events:
        lines.append(f"data: {json.dumps(ev)}")
    lines.append("data: [DONE]")
    return lines


class TestStreamCodex:
    @pytest.mark.asyncio
    async def test_text_and_done(self):

        events = [
            {"type": "response.output_text.delta", "delta": "Hello "},
            {"type": "response.output_text.delta", "delta": "world"},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
        ]

        token = _make_jwt({"chatgpt_account_id": "acct-1"})
        provider = OpenAIProvider(api_key=token, auth_type="oauth")

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = AsyncMock(return_value=_sse_lines(events).__iter__())

        # Make aiter_lines an async generator
        async def async_lines():
            for line in _sse_lines(events):
                yield line

        mock_response.aiter_lines = async_lines

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        with patch.object(provider._client, "stream", return_value=mock_cm):
            collected = []
            async for ev in provider._stream_codex(
                "gpt-5.3-codex",
                [Message(role=Role.USER, content="Hi")],
            ):
                collected.append(ev)

        assert len(collected) == 3
        assert isinstance(collected[0], TextDelta)
        assert collected[0].content == "Hello "
        assert isinstance(collected[1], TextDelta)
        assert collected[1].content == "world"
        assert isinstance(collected[2], Done)
        assert collected[2].usage.input_tokens == 10
        assert collected[2].usage.output_tokens == 5

    @pytest.mark.asyncio
    async def test_tool_call_flow(self):

        events = [
            {
                "type": "response.output_item.added",
                "output_index": 0,
                "item": {"type": "function_call", "call_id": "fc-1", "name": "read_file"},
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "delta": '{"path":',
            },
            {
                "type": "response.function_call_arguments.delta",
                "output_index": 0,
                "delta": '"/foo"}',
            },
            {
                "type": "response.function_call_arguments.done",
                "output_index": 0,
                "arguments": '{"path":"/foo"}',
            },
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 20, "output_tokens": 10},
                },
            },
        ]

        token = _make_jwt({"chatgpt_account_id": "acct-1"})
        provider = OpenAIProvider(api_key=token, auth_type="oauth")

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
            async for ev in provider._stream_codex(
                "gpt-5.3-codex",
                [Message(role=Role.USER, content="Read /foo")],
            ):
                collected.append(ev)

        assert len(collected) == 2
        assert isinstance(collected[0], ToolCallDelta)
        assert collected[0].name == "read_file"
        assert collected[0].id == "fc-1"
        assert collected[0].arguments == {"path": "/foo"}
        assert isinstance(collected[1], Done)

    @pytest.mark.asyncio
    async def test_reasoning_delta(self):

        events = [
            {"type": "response.reasoning_summary_text.delta", "delta": "Thinking..."},
            {"type": "response.output_text.delta", "delta": "Answer"},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 5, "output_tokens": 3},
                },
            },
        ]

        token = _make_jwt({"chatgpt_account_id": "acct-1"})
        provider = OpenAIProvider(api_key=token, auth_type="oauth")

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
            async for ev in provider._stream_codex(
                "gpt-5.3-codex",
                [Message(role=Role.USER, content="test")],
            ):
                collected.append(ev)

        assert len(collected) == 3
        assert isinstance(collected[0], ReasoningDelta)
        assert collected[0].content == "Thinking..."
        assert isinstance(collected[1], TextDelta)
        assert isinstance(collected[2], Done)
