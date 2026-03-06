"""OpenAI Chat Completions API compatible provider.

Reusable for: OpenAI, Groq, Mistral, xAI, OpenRouter, Together, Cerebras, DeepSeek,
Azure OpenAI, and any other OpenAI-compatible endpoint.

When ``auth_type="oauth"`` the provider switches to the ChatGPT Codex backend
(``chatgpt.com/backend-api/codex/responses``) using the Responses API format,
matching the behaviour of Codex CLI / opencode for ChatGPT Plus/Pro subscribers.
"""

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

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"

# ── Known models (OpenAI only — other providers override) ─────────

_OPENAI_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="gpt-4.1",
        provider="openai",
        name="GPT-4.1",
        context_window=1_047_576,
        max_output_tokens=32_768,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=False,
        input_price_per_m=2.0,
        output_price_per_m=8.0,
    ),
    ModelInfo(
        id="gpt-4.1-mini",
        provider="openai",
        name="GPT-4.1 Mini",
        context_window=1_047_576,
        max_output_tokens=32_768,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=False,
        input_price_per_m=0.40,
        output_price_per_m=1.60,
    ),
    ModelInfo(
        id="o3",
        provider="openai",
        name="o3",
        context_window=200_000,
        max_output_tokens=100_000,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=True,
        input_price_per_m=2.0,
        output_price_per_m=8.0,
    ),
    ModelInfo(
        id="o4-mini",
        provider="openai",
        name="o4-mini",
        context_window=200_000,
        max_output_tokens=100_000,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=True,
        input_price_per_m=1.10,
        output_price_per_m=4.40,
    ),
]


# ── Helpers — Chat Completions ────────────────────────────────────


def _build_tools(tools: list[ToolDef]) -> list[dict[str, Any]]:
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
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
        )
    return result


def _build_messages(messages: list[Message]) -> list[dict[str, Any]]:
    api_msgs: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == Role.TOOL:
            assert msg.tool_result is not None
            api_msgs.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.tool_result.tool_call_id,
                    "content": msg.tool_result.content,
                }
            )
            continue

        if msg.role == Role.ASSISTANT and msg.tool_calls:
            tc_list = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in msg.tool_calls
            ]
            api_msgs.append(
                {"role": "assistant", "content": msg.content or None, "tool_calls": tc_list}
            )
            continue

        api_msgs.append({"role": msg.role.value, "content": msg.content})
    return api_msgs


# ── Helpers — Responses API (Codex backend) ──────────────────────


def _build_responses_input(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    """Convert internal messages to Responses API input items.

    Returns (instructions, input_items).  System messages are extracted into
    ``instructions``; everything else becomes input items.
    """
    instructions: str | None = None
    items: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == Role.SYSTEM:
            instructions = msg.content
            continue

        if msg.role == Role.TOOL:
            assert msg.tool_result is not None
            items.append({
                "type": "function_call_output",
                "call_id": msg.tool_result.tool_call_id,
                "output": msg.tool_result.content,
            })
            continue

        if msg.role == Role.ASSISTANT and msg.tool_calls:
            # Emit text part first if present
            if msg.content:
                items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": msg.content,
                })
            for tc in msg.tool_calls:
                items.append({
                    "type": "function_call",
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments,
                    "call_id": tc.id,
                })
            continue

        items.append({
            "type": "message",
            "role": msg.role.value,
            "content": msg.content,
        })

    return instructions, items


def _build_responses_tools(tools: list[ToolDef]) -> list[dict[str, Any]]:
    """Convert ToolDef list to Responses API tool definitions."""
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
        result.append({
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        })
    return result


# ── Provider ──────────────────────────────────────────────────────


class OpenAICompatProvider(Provider):
    """OpenAI Chat Completions API (and compatible endpoints).

    When ``auth_type="oauth"`` is passed (ChatGPT Plus/Pro subscription),
    the provider transparently routes to the Codex backend using the
    Responses API.
    """

    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        models: list[ModelInfo] | None = None,
        **kwargs: Any,
    ):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self._auth_type: str = kwargs.get("auth_type", "api")
        self._models = models or _OPENAI_MODELS

        if self._auth_type == "oauth":
            self._base_url = _CODEX_BASE_URL
            # Extract chatgpt_account_id from the JWT access token
            self._account_id = self._extract_account_id(api_key or "")
        else:
            self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
            self._account_id = ""

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
        )

    @staticmethod
    def _extract_account_id(access_token: str) -> str:
        """Extract chatgpt_account_id from the JWT access token."""
        from worker_ai.oauth import extract_openai_account_id, parse_jwt_claims

        claims = parse_jwt_claims(access_token)
        return extract_openai_account_id(claims)

    # ── Chat Completions (API key flow) ──────────────────────────

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
        if self._auth_type == "oauth":
            async for event in self._stream_codex(
                model, messages, tools=tools,
                temperature=temperature, max_tokens=max_tokens,
                thinking_level=thinking_level,
            ):
                yield event
            return

        api_msgs = _build_messages(messages)

        body: dict[str, Any] = {
            "model": model,
            "messages": api_msgs,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # OpenAI reasoning models: reasoning_effort
        if thinking_level != "off":
            effort_map = {
                "minimal": "low", "low": "low", "medium": "medium",
                "high": "high", "xhigh": "high",
            }
            body["reasoning_effort"] = effort_map.get(thinking_level, "medium")
        if max_tokens:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = _build_tools(tools)

        headers: dict[str, str] = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"

        async with self._client.stream(
            "POST", "/chat/completions", json=body, headers=headers
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                msg = f"OpenAI API error {response.status_code}: {error_body.decode()}"
                raise RuntimeError(msg)

            # Accumulate tool call arguments across chunks
            pending_tools: dict[int, dict[str, Any]] = {}  # index → {id, name, args_json}
            usage = Usage()

            async for raw_line in response.aiter_lines():
                if not raw_line.startswith("data: "):
                    continue
                data_str = raw_line[6:]
                if data_str.strip() == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                # Usage info (sent at the end with stream_options)
                if "usage" in chunk and chunk["usage"]:
                    u = chunk["usage"]
                    usage.input_tokens = u.get("prompt_tokens", 0)
                    usage.output_tokens = u.get("completion_tokens", 0)
                    usage.reasoning_tokens = (
                        u.get("completion_tokens_details", {}).get("reasoning_tokens", 0)
                    )

                choices = chunk.get("choices", [])
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason")

                # Text content
                content = delta.get("content")
                if content:
                    yield TextDelta(content=content)

                # Reasoning (OpenAI o-series)
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    yield ReasoningDelta(content=reasoning)

                # Tool calls
                for tc_delta in delta.get("tool_calls", []):
                    idx = tc_delta.get("index", 0)
                    if idx not in pending_tools:
                        pending_tools[idx] = {
                            "id": tc_delta.get("id", ""),
                            "name": tc_delta.get("function", {}).get("name", ""),
                            "args_json": "",
                        }
                    else:
                        if tc_delta.get("id"):
                            pending_tools[idx]["id"] = tc_delta["id"]
                        fn_name = tc_delta.get("function", {}).get("name")
                        if fn_name:
                            pending_tools[idx]["name"] = fn_name

                    args_chunk = tc_delta.get("function", {}).get("arguments", "")
                    if args_chunk:
                        pending_tools[idx]["args_json"] += args_chunk

                # Finish
                if finish_reason:
                    # Emit accumulated tool calls
                    for _idx in sorted(pending_tools):
                        tc_info = pending_tools[_idx]
                        try:
                            args = (
                                json.loads(tc_info["args_json"])
                                if tc_info["args_json"]
                                else {}
                            )
                        except json.JSONDecodeError:
                            args = {"_raw": tc_info["args_json"]}
                        yield ToolCallDelta(
                            id=tc_info["id"],
                            name=tc_info["name"],
                            arguments=args,
                        )
                    pending_tools.clear()
                    yield Done(stop_reason=finish_reason, usage=usage)

    # ── Codex Responses API (OAuth / ChatGPT subscription) ───────

    async def _stream_codex(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking_level: str = "off",
    ) -> AsyncIterator[StreamEvent]:
        """Stream via the ChatGPT Codex backend (Responses API)."""
        instructions, input_items = _build_responses_input(messages)

        body: dict[str, Any] = {
            "model": model,
            "input": input_items,
            "stream": True,
        }
        if instructions:
            body["instructions"] = instructions
        if tools:
            body["tools"] = _build_responses_tools(tools)
        if max_tokens:
            body["max_output_tokens"] = max_tokens

        # Reasoning effort
        effort_map = {
            "off": "none", "minimal": "low", "low": "low",
            "medium": "medium", "high": "high", "xhigh": "high",
        }
        effort = effort_map.get(thinking_level, "medium")
        body["reasoning"] = {"effort": effort, "summary": "auto"}

        headers: dict[str, str] = {
            "content-type": "application/json",
            "authorization": f"Bearer {self.api_key or ''}",
        }
        if self._account_id:
            headers["openai-organization"] = self._account_id

        async with self._client.stream(
            "POST", "/responses", json=body, headers=headers,
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                msg = f"Codex API error {response.status_code}: {error_body.decode()}"
                raise RuntimeError(msg)

            # Track pending function call for argument accumulation
            pending_fc: dict[int, dict[str, Any]] = {}  # output_index → {call_id, name, args}
            usage = Usage()

            async for raw_line in response.aiter_lines():
                if not raw_line.startswith("data: "):
                    continue
                data_str = raw_line[6:]
                if data_str.strip() == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                event_type = chunk.get("type", "")

                # ── Text output ──
                if event_type == "response.output_text.delta":
                    delta = chunk.get("delta", "")
                    if delta:
                        yield TextDelta(content=delta)

                # ── Reasoning summary ──
                elif event_type == "response.reasoning_summary_text.delta":
                    delta = chunk.get("delta", "")
                    if delta:
                        yield ReasoningDelta(content=delta)

                # ── Function call start ──
                elif event_type == "response.output_item.added":
                    item = chunk.get("item", {})
                    if item.get("type") == "function_call":
                        idx = chunk.get("output_index", 0)
                        pending_fc[idx] = {
                            "call_id": item.get("call_id", ""),
                            "name": item.get("name", ""),
                            "args": "",
                        }

                # ── Function call arguments (streaming) ──
                elif event_type == "response.function_call_arguments.delta":
                    idx = chunk.get("output_index", 0)
                    if idx in pending_fc:
                        pending_fc[idx]["args"] += chunk.get("delta", "")

                # ── Function call done ──
                elif event_type == "response.function_call_arguments.done":
                    idx = chunk.get("output_index", 0)
                    fc = pending_fc.pop(idx, None)
                    if fc:
                        args_str = chunk.get("arguments", fc["args"])
                        try:
                            args = json.loads(args_str) if args_str else {}
                        except json.JSONDecodeError:
                            args = {"_raw": args_str}
                        yield ToolCallDelta(
                            id=fc["call_id"],
                            name=fc["name"],
                            arguments=args,
                        )

                # ── Response completed ──
                elif event_type == "response.completed":
                    resp = chunk.get("response", {})
                    u = resp.get("usage", {})
                    usage.input_tokens = u.get("input_tokens", 0)
                    usage.output_tokens = u.get("output_tokens", 0)
                    usage.reasoning_tokens = u.get(
                        "output_tokens_details", {}
                    ).get("reasoning_tokens", 0)
                    stop_reason = resp.get("status", "completed")
                    yield Done(stop_reason=stop_reason, usage=usage)

    # ── Common ────────────────────────────────────────────────────

    def list_models(self) -> list[ModelInfo]:
        return list(self._models)

    async def close(self) -> None:
        await self._client.aclose()
