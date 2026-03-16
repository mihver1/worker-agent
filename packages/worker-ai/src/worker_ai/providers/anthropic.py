"""Anthropic Messages API provider with SSE streaming."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from worker_ai.attachments import attachment_data_base64
from worker_ai.models import (
    Done,
    Message,
    ModelInfo,
    ReasoningDelta,
    Role,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
    ToolDef,
    Usage,
)
from worker_ai.provider import Provider, build_httpx_timeout, merge_headers
from worker_ai.tool_schema import tool_input_schema

_DEFAULT_BASE_URL = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"
_OAUTH_TOOL_PREFIX = "mcp_"
_OAUTH_REQUIRED_BETAS = (
    "oauth-2025-04-20",
    "interleaved-thinking-2025-05-14",
)
_INTERLEAVED_THINKING_BETA = "interleaved-thinking-2025-05-14"
_FINE_GRAINED_TOOL_STREAMING_BETA = "fine-grained-tool-streaming-2025-05-14"
_OAUTH_USER_AGENT = "claude-cli/2.1.2 (external, cli)"
_CLAUDE_CODE_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."

# ── Known models ──────────────────────────────────────────────────

_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="claude-sonnet-4-20250514",
        provider="anthropic",
        name="Claude Sonnet 4",
        context_window=200_000,
        max_output_tokens=16_384,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=True,
        input_price_per_m=3.0,
        output_price_per_m=15.0,
    ),
    ModelInfo(
        id="claude-opus-4-20250514",
        provider="anthropic",
        name="Claude Opus 4",
        context_window=200_000,
        max_output_tokens=32_000,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=True,
        input_price_per_m=15.0,
        output_price_per_m=75.0,
    ),
    ModelInfo(
        id="claude-haiku-3-5-20241022",
        provider="anthropic",
        name="Claude 3.5 Haiku",
        context_window=200_000,
        max_output_tokens=8_192,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=False,
        input_price_per_m=0.80,
        output_price_per_m=4.0,
    ),
]


# ── Helpers ───────────────────────────────────────────────────────


def _build_tools(tools: list[ToolDef]) -> list[dict[str, Any]]:
    """Convert ToolDef list to Anthropic tool format."""
    result = []
    for t in tools:
        result.append(
            {
                "name": t.name,
                "description": t.description,
                "input_schema": tool_input_schema(t),
            }
        )
    return result


def _message_content_blocks(msg: Message) -> str | list[dict[str, Any]]:
    if not msg.attachments:
        return msg.content
    content: list[dict[str, Any]] = []
    if msg.content:
        content.append({"type": "text", "text": msg.content})
    for attachment in msg.attachments:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": attachment.mime_type,
                    "data": attachment_data_base64(attachment),
                },
            }
        )
    return content


def _build_messages(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    """Split system prompt and convert messages to Anthropic format."""
    system: str | None = None
    api_msgs: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == Role.SYSTEM:
            system = msg.content
            continue

        if msg.role == Role.TOOL:
            assert msg.tool_result is not None
            api_msgs.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_result.tool_call_id,
                            "content": msg.tool_result.content,
                            **({"is_error": True} if msg.tool_result.is_error else {}),
                        }
                    ],
                }
            )
            continue

        if msg.role == Role.ASSISTANT and msg.tool_calls:
            content: list[dict[str, Any]] = []
            blocks = _message_content_blocks(msg)
            if isinstance(blocks, str):
                if blocks:
                    content.append({"type": "text", "text": blocks})
            else:
                content.extend(blocks)
            for tc in msg.tool_calls:
                content.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
            api_msgs.append({"role": "assistant", "content": content})
            continue

        api_msgs.append({"role": msg.role.value, "content": _message_content_blocks(msg)})

    return system, api_msgs


def _build_request_body(
    messages: list[Message],
    *,
    model: str | None,
    anthropic_version: str | None,
    tools: list[ToolDef] | None,
    temperature: float,
    max_tokens: int | None,
    thinking_level: str,
) -> dict[str, Any]:
    system, api_msgs = _build_messages(messages)

    body: dict[str, Any] = {
        "messages": api_msgs,
        "max_tokens": max_tokens or 16_384,
        "temperature": temperature,
        "stream": True,
    }
    if model is not None:
        body["model"] = model
    if anthropic_version is not None:
        body["anthropic_version"] = anthropic_version

    if thinking_level != "off":
        budget_map = {
            "minimal": 1024,
            "low": 2048,
            "medium": 4096,
            "high": 8192,
            "xhigh": 16384,
        }
        budget = budget_map.get(thinking_level, 4096)
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}
    if system:
        body["system"] = system
    if tools:
        body["tools"] = _build_tools(tools)
    return body


def _normalize_beta_headers(
    value: str | list[str] | tuple[str, ...] | None,
) -> list[str]:
    if value is None:
        return []
    values = [value] if isinstance(value, str) else list(value)
    result: list[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        result.extend(part.strip() for part in item.split(",") if part.strip())
    return result


def _merge_beta_headers(
    *values: str | list[str] | tuple[str, ...] | None,
) -> str | None:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in _normalize_beta_headers(value):
            if item in seen:
                continue
            seen.add(item)
            result.append(item)
    return ",".join(result) or None


def _prefix_tool_name(name: str) -> str:
    if name.startswith(_OAUTH_TOOL_PREFIX):
        return name
    return f"{_OAUTH_TOOL_PREFIX}{name}"


def _strip_oauth_tool_prefix(name: str) -> str:
    if name.startswith(_OAUTH_TOOL_PREFIX):
        return name[len(_OAUTH_TOOL_PREFIX) :]
    return name


def _emit_tool_call_delta(
    tool_id: str,
    tool_name: str,
    tool_input_json: str,
) -> ToolCallDelta:
    try:
        arguments = json.loads(tool_input_json) if tool_input_json else {}
    except json.JSONDecodeError:
        arguments = {"_raw": tool_input_json}
    return ToolCallDelta(
        id=tool_id,
        name=tool_name,
        arguments=arguments,
    )


def _consume_message_event(event: dict[str, Any], usage: Usage) -> list[StreamEvent]:
    emitted: list[StreamEvent] = []
    msg_usage = event.get("usage", {})
    if msg_usage:
        usage.input_tokens = msg_usage.get("input_tokens", usage.input_tokens)
        usage.output_tokens = msg_usage.get("output_tokens", usage.output_tokens)
        usage.cache_read_tokens = msg_usage.get(
            "cache_read_input_tokens",
            usage.cache_read_tokens,
        )
        usage.cache_write_tokens = msg_usage.get(
            "cache_creation_input_tokens",
            usage.cache_write_tokens,
        )

    for block in event.get("content", []):
        block_type = block.get("type", "")
        if block_type == "text":
            text = block.get("text", "")
            if text:
                emitted.append(TextDelta(content=text))
        elif block_type == "thinking":
            text = block.get("thinking") or block.get("text", "")
            if text:
                emitted.append(ReasoningDelta(content=text))
        elif block_type == "tool_use":
            tool_name = _strip_oauth_tool_prefix(block.get("name") or block.get("tool_name", ""))
            arguments = block.get("input")
            if arguments is None:
                arguments = block.get("tool_input", {})
            if not isinstance(arguments, dict):
                arguments = {"_raw": json.dumps(arguments)}
            emitted.append(
                ToolCallDelta(
                    id=block.get("id", ""),
                    name=tool_name,
                    arguments=arguments,
                )
            )

    emitted.append(Done(stop_reason=event.get("stop_reason", "end_turn"), usage=usage))
    return emitted


def _consume_stream_event(
    event: dict[str, Any],
    usage: Usage,
    current_tool_id: str | None,
    current_tool_name: str | None,
    tool_input_json: str,
) -> tuple[list[StreamEvent], str | None, str | None, str]:
    emitted: list[StreamEvent] = []
    event_type = event.get("type", "")

    if event_type == "message":
        emitted.extend(_consume_message_event(event, usage))
        return emitted, None, None, ""

    if event_type == "message_start":
        msg_usage = event.get("message", {}).get("usage", {})
        usage.input_tokens = msg_usage.get("input_tokens", 0)
        usage.cache_read_tokens = msg_usage.get("cache_read_input_tokens", 0)
        usage.cache_write_tokens = msg_usage.get("cache_creation_input_tokens", 0)

    elif event_type == "content_block_start":
        block = event.get("content_block", {})
        if block.get("type") == "tool_use":
            current_tool_id = block.get("id", "")
            current_tool_name = _strip_oauth_tool_prefix(
                block.get("name") or block.get("tool_name", "")
            )
            tool_input_json = ""
        elif block.get("type") == "thinking":
            text = block.get("thinking", "")
            if text:
                emitted.append(ReasoningDelta(content=text))

    elif event_type == "content_block_delta":
        delta = event.get("delta", {})
        delta_type = delta.get("type", "")

        if delta_type == "text_delta":
            emitted.append(TextDelta(content=delta.get("text", "")))
        elif delta_type == "thinking_delta":
            emitted.append(ReasoningDelta(content=delta.get("thinking", "")))
        elif delta_type == "input_json_delta":
            tool_input_json += delta.get("partial_json", "")

    elif event_type == "content_block_stop":
        if current_tool_id and current_tool_name:
            emitted.append(
                _emit_tool_call_delta(
                    current_tool_id,
                    current_tool_name,
                    tool_input_json,
                )
            )
            current_tool_id = None
            current_tool_name = None
            tool_input_json = ""

    elif event_type == "message_delta":
        if current_tool_id and current_tool_name:
            emitted.append(
                _emit_tool_call_delta(
                    current_tool_id,
                    current_tool_name,
                    tool_input_json,
                )
            )
            current_tool_id = None
            current_tool_name = None
            tool_input_json = ""
        delta = event.get("delta", {})
        stop_reason = delta.get("stop_reason", "end_turn")
        out_usage = event.get("usage", {})
        usage.output_tokens = out_usage.get("output_tokens", 0)
        emitted.append(Done(stop_reason=stop_reason, usage=usage))

    return emitted, current_tool_id, current_tool_name, tool_input_json


def _apply_claude_code_oauth_transform(body: dict[str, Any]) -> None:
    if body.get("system"):
        body["system"] = f"{_CLAUDE_CODE_SYSTEM_PREFIX}\n\n{body['system']}"
    else:
        body["system"] = _CLAUDE_CODE_SYSTEM_PREFIX

    if body.get("tools"):
        for tool in body["tools"]:
            tool["name"] = _prefix_tool_name(tool["name"])

    for msg in body.get("messages", []):
        if isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if block.get("type") == "tool_use" and block.get("name"):
                    block["name"] = _prefix_tool_name(block["name"])


# ── Provider ──────────────────────────────────────────────────────


class AnthropicProvider(Provider):
    """Anthropic Messages API with SSE streaming."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._auth_type: str = kwargs.get("auth_type", "api")
        self._beta_headers = _normalize_beta_headers(kwargs.get("beta_headers"))
        self._interleaved_thinking = bool(kwargs.get("interleaved_thinking", False))
        self._fine_grained_tool_streaming = bool(kwargs.get("fine_grained_tool_streaming", False))
        default_timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=build_httpx_timeout(self.timeout, default=default_timeout),
        )

    def _messages_endpoint(self) -> str:
        if self._base_url.endswith("/v1"):
            return "/messages"
        return "/v1/messages"

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
        body = _build_request_body(
            messages,
            model=model,
            anthropic_version=None,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )

        headers = merge_headers(
            {
                "anthropic-version": _API_VERSION,
                "content-type": "application/json",
                "accept": "text/event-stream",
            },
            self.headers,
        )
        requested_betas: list[str] = list(self._beta_headers)
        if thinking_level != "off" and self._interleaved_thinking:
            requested_betas.append(_INTERLEAVED_THINKING_BETA)
        if tools and self._fine_grained_tool_streaming:
            requested_betas.append(_FINE_GRAINED_TOOL_STREAMING_BETA)

        # OAuth mode: transform request to match Claude Code CLI format
        url = self._messages_endpoint()
        if self._auth_type == "oauth":
            headers["authorization"] = f"Bearer {self.api_key or ''}"
            headers["user-agent"] = _OAUTH_USER_AGENT
            url = f"{url}?beta=true"
            _apply_claude_code_oauth_transform(body)
        else:
            headers["x-api-key"] = self.api_key or ""
        merged_betas = _merge_beta_headers(
            headers.get("anthropic-beta"),
            requested_betas,
            _OAUTH_REQUIRED_BETAS if self._auth_type == "oauth" else None,
        )
        if merged_betas:
            headers["anthropic-beta"] = merged_betas

        async with self._client.stream("POST", url, json=body, headers=headers) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                msg = f"Anthropic API error {response.status_code}: {error_body.decode()}"
                raise RuntimeError(msg)

            # Track tool calls being assembled across chunks
            current_tool_id: str | None = None
            current_tool_name: str | None = None
            tool_input_json = ""
            usage = Usage()

            async for raw_line in response.aiter_lines():
                if not raw_line.startswith("data:"):
                    continue
                data_str = raw_line[5:].lstrip()
                if data_str.strip() == "[DONE]":
                    break

                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

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
        return list(_MODELS)

    async def close(self) -> None:
        await self._client.aclose()
