"""Anthropic Messages API provider with SSE streaming."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

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
from worker_ai.provider import Provider

_DEFAULT_BASE_URL = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"
_OAUTH_TOOL_PREFIX = "mcp_"
_OAUTH_BETAS = "oauth-2025-04-20,interleaved-thinking-2025-05-14"
_OAUTH_USER_AGENT = "claude-cli/2.1.2 (external, cli)"
_CLAUDE_CODE_SYSTEM_PREFIX = (
    "You are Claude Code, Anthropic's official CLI for Claude."
)

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
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in t.parameters:
            prop: dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            properties[p.name] = prop
            if p.required:
                required.append(p.name)
        result.append(
            {
                "name": t.name,
                "description": t.description,
                "input_schema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }
        )
    return result


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
            if msg.content:
                content.append({"type": "text", "text": msg.content})
            for tc in msg.tool_calls:
                content.append(
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                )
            api_msgs.append({"role": "assistant", "content": content})
            continue

        api_msgs.append({"role": msg.role.value, "content": msg.content})

    return system, api_msgs


# ── Provider ──────────────────────────────────────────────────────


class AnthropicProvider(Provider):
    """Anthropic Messages API with SSE streaming."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._auth_type: str = kwargs.get("auth_type", "api")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
        )

    async def stream_chat(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        system, api_msgs = _build_messages(messages)

        body: dict[str, Any] = {
            "model": model,
            "messages": api_msgs,
            "max_tokens": max_tokens or 16_384,
            "temperature": temperature,
            "stream": True,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = _build_tools(tools)

        headers: dict[str, str] = {
            "anthropic-version": _API_VERSION,
            "content-type": "application/json",
            "accept": "text/event-stream",
        }

        # OAuth mode: transform request to match Claude Code CLI format
        url = "/v1/messages"
        if self._auth_type == "oauth":
            headers["authorization"] = f"Bearer {self.api_key or ''}"
            headers["anthropic-beta"] = _OAUTH_BETAS
            headers["user-agent"] = _OAUTH_USER_AGENT
            url = "/v1/messages?beta=true"
            # Prepend Claude Code identity to system prompt
            if body.get("system"):
                body["system"] = f"{_CLAUDE_CODE_SYSTEM_PREFIX}\n\n{body['system']}"
            else:
                body["system"] = _CLAUDE_CODE_SYSTEM_PREFIX
            # Prefix tool names with mcp_ (Anthropic validates this for OAuth)
            if body.get("tools"):
                for tool in body["tools"]:
                    tool["name"] = f"{_OAUTH_TOOL_PREFIX}{tool['name']}"
            # Also prefix tool_use/tool_result names in messages
            for msg in body.get("messages", []):
                if isinstance(msg.get("content"), list):
                    for block in msg["content"]:
                        if block.get("type") == "tool_use" and block.get("name"):
                            block["name"] = f"{_OAUTH_TOOL_PREFIX}{block['name']}"
        else:
            headers["x-api-key"] = self.api_key or ""

        async with self._client.stream(
            "POST", url, json=body, headers=headers
        ) as response:
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
                if not raw_line.startswith("data: "):
                    continue
                data_str = raw_line[6:]
                if data_str.strip() == "[DONE]":
                    break

                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = event.get("type", "")

                if event_type == "message_start":
                    msg_usage = event.get("message", {}).get("usage", {})
                    usage.input_tokens = msg_usage.get("input_tokens", 0)
                    usage.cache_read_tokens = msg_usage.get(
                        "cache_read_input_tokens", 0
                    )
                    usage.cache_write_tokens = msg_usage.get(
                        "cache_creation_input_tokens", 0
                    )

                elif event_type == "content_block_start":
                    block = event.get("content_block", {})
                    if block.get("type") == "tool_use":
                        current_tool_id = block.get("id", "")
                        name = block.get("name", "")
                        # Strip mcp_ prefix added for OAuth
                        if self._auth_type == "oauth" and name.startswith(_OAUTH_TOOL_PREFIX):
                            name = name[len(_OAUTH_TOOL_PREFIX):]
                        current_tool_name = name
                        tool_input_json = ""
                    elif block.get("type") == "thinking":
                        text = block.get("thinking", "")
                        if text:
                            yield ReasoningDelta(content=text)

                elif event_type == "content_block_delta":
                    delta = event.get("delta", {})
                    delta_type = delta.get("type", "")

                    if delta_type == "text_delta":
                        yield TextDelta(content=delta.get("text", ""))

                    elif delta_type == "thinking_delta":
                        yield ReasoningDelta(content=delta.get("thinking", ""))

                    elif delta_type == "input_json_delta":
                        tool_input_json += delta.get("partial_json", "")

                elif event_type == "content_block_stop":
                    if current_tool_id and current_tool_name:
                        try:
                            args = json.loads(tool_input_json) if tool_input_json else {}
                        except json.JSONDecodeError:
                            args = {"_raw": tool_input_json}
                        yield ToolCallDelta(
                            id=current_tool_id,
                            name=current_tool_name,
                            arguments=args,
                        )
                        current_tool_id = None
                        current_tool_name = None
                        tool_input_json = ""

                elif event_type == "message_delta":
                    delta = event.get("delta", {})
                    stop_reason = delta.get("stop_reason", "end_turn")
                    out_usage = event.get("usage", {})
                    usage.output_tokens = out_usage.get("output_tokens", 0)
                    yield Done(stop_reason=stop_reason, usage=usage)

    def list_models(self) -> list[ModelInfo]:
        return list(_MODELS)

    async def close(self) -> None:
        await self._client.aclose()
