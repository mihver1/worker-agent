"""OpenAI Chat Completions API compatible provider.

Reusable for: OpenAI, Groq, Mistral, xAI, OpenRouter, Together, Cerebras, DeepSeek,
Azure OpenAI, and any other OpenAI-compatible endpoint.
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


# ── Helpers ───────────────────────────────────────────────────────


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


# ── Provider ──────────────────────────────────────────────────────


class OpenAICompatProvider(Provider):
    """OpenAI Chat Completions API (and compatible endpoints)."""

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
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._models = models or _OPENAI_MODELS
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
        thinking_level: str = "off",
    ) -> AsyncIterator[StreamEvent]:
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

    def list_models(self) -> list[ModelInfo]:
        return list(self._models)

    async def close(self) -> None:
        await self._client.aclose()
