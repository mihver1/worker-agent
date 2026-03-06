"""OpenAI-compatible provider implementations.

`OpenAICompatibleProvider` implements generic Chat Completions-compatible
endpoints. `OpenAIProvider` extends it with first-party OpenAI defaults and
Responses API / Codex support.
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
from worker_ai.provider import Provider, build_httpx_timeout, merge_headers

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


def _clone_models(provider_name: str, models: list[ModelInfo]) -> list[ModelInfo]:
    return [model.model_copy(update={"provider": provider_name}) for model in models]


def _int_or_default(value: Any, default: int) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _bool_or_default(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    return default


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


def _build_chat_completions_body(
    model: str,
    messages: list[Message],
    *,
    tools: list[ToolDef] | None,
    temperature: float,
    max_tokens: int | None,
    thinking_level: str,
) -> dict[str, Any]:
    api_msgs = _build_messages(messages)

    body: dict[str, Any] = {
        "model": model,
        "messages": api_msgs,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if thinking_level != "off":
        effort_map = {
            "minimal": "low",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "high",
        }
        body["reasoning_effort"] = effort_map.get(thinking_level, "medium")
    if max_tokens:
        body["max_tokens"] = max_tokens
    if tools:
        body["tools"] = _build_tools(tools)
    return body


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
                arguments = (
                    json.dumps(tc.arguments)
                    if isinstance(tc.arguments, dict)
                    else tc.arguments
                )
                items.append({
                    "type": "function_call",
                    "name": tc.name,
                    "arguments": arguments,
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


def _build_responses_body(
    model: str,
    messages: list[Message],
    *,
    tools: list[ToolDef] | None,
    temperature: float,
    max_tokens: int | None,
    thinking_level: str,
) -> dict[str, Any]:
    instructions, input_items = _build_responses_input(messages)

    body: dict[str, Any] = {
        "model": model,
        "input": input_items,
        "stream": True,
        "store": False,
    }
    if instructions:
        body["instructions"] = instructions
    if tools:
        body["tools"] = _build_responses_tools(tools)
    if max_tokens:
        body["max_output_tokens"] = max_tokens
    if temperature:
        body["temperature"] = temperature

    effort_map = {
        "off": "none",
        "minimal": "low",
        "low": "low",
        "medium": "medium",
        "high": "high",
        "xhigh": "high",
    }
    effort = effort_map.get(thinking_level, "medium")
    body["reasoning"] = {"effort": effort, "summary": "auto"}
    return body


# ── Providers ─────────────────────────────────────────────────────


class OpenAICompatibleProvider(Provider):
    """Generic OpenAI Chat Completions-compatible provider."""

    name = "openai_compat"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        models: list[ModelInfo] | None = None,
        **kwargs: Any,
    ):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self._models = models if models is not None else []
        self._base_url = (base_url or self._default_base_url()).rstrip("/")
        default_timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
        client_kwargs: dict[str, Any] = {
            "timeout": build_httpx_timeout(self.timeout, default=default_timeout),
        }
        if self._base_url:
            client_kwargs["base_url"] = self._base_url
        self._client = httpx.AsyncClient(**client_kwargs)

    def _default_base_url(self) -> str:
        return _DEFAULT_BASE_URL

    def _build_chat_completions_request(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking_level: str = "off",
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        body = _build_chat_completions_body(
            model,
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )
        headers = merge_headers(
            {"content-type": "application/json"},
            {"authorization": f"Bearer {self.api_key}"} if self.api_key else None,
            self.headers,
        )
        return "/chat/completions", body, headers

    async def _stream_chat_completions(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking_level: str = "off",
    ) -> AsyncIterator[StreamEvent]:
        path, body, headers = self._build_chat_completions_request(
            model,
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )

        async with self._client.stream(
            "POST", path, json=body, headers=headers
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                msg = f"OpenAI API error {response.status_code}: {error_body.decode()}"
                raise RuntimeError(msg)

            pending_tools: dict[int, dict[str, Any]] = {}
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

                content = delta.get("content")
                if content:
                    yield TextDelta(content=content)

                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if reasoning:
                    yield ReasoningDelta(content=reasoning)

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

                if finish_reason:
                    for idx in sorted(pending_tools):
                        tc_info = pending_tools[idx]
                        try:
                            args = json.loads(tc_info["args_json"]) if tc_info["args_json"] else {}
                        except json.JSONDecodeError:
                            args = {"_raw": tc_info["args_json"]}
                        yield ToolCallDelta(
                            id=tc_info["id"],
                            name=tc_info["name"],
                            arguments=args,
                        )
                    pending_tools.clear()
                    yield Done(stop_reason=finish_reason, usage=usage)

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
        async for event in self._stream_chat_completions(
            model,
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        ):
            yield event

    def list_models(self) -> list[ModelInfo]:
        return list(self._models)

    async def list_models_direct(self) -> list[ModelInfo]:
        headers = merge_headers(
            {"authorization": f"Bearer {self.api_key}"} if self.api_key else None,
            self.headers,
        )
        try:
            response = await self._client.get("/models", headers=headers, timeout=5.0)
            if response.status_code != 200:
                return self.list_models()
            payload = response.json()
        except Exception:
            return self.list_models()

        raw_models = payload.get("data", [])
        if not isinstance(raw_models, list):
            return self.list_models()

        models: list[ModelInfo] = []
        for raw_model in raw_models:
            if not isinstance(raw_model, dict):
                continue
            model_id = str(raw_model.get("id", "")).strip()
            if not model_id:
                continue
            models.append(
                ModelInfo(
                    id=model_id,
                    provider=self.name,
                    name=str(raw_model.get("name") or raw_model.get("display_name") or model_id),
                    context_window=_int_or_default(
                        raw_model.get("context_window")
                        or raw_model.get("max_context_length")
                        or raw_model.get("max_context_tokens")
                        or raw_model.get("max_model_len"),
                        128_000,
                    ),
                    max_output_tokens=_int_or_default(
                        raw_model.get("max_output_tokens")
                        or raw_model.get("max_completion_tokens"),
                        8_192,
                    ),
                    supports_tools=_bool_or_default(raw_model.get("supports_tools"), True),
                    supports_vision=_bool_or_default(
                        raw_model.get("supports_vision") or raw_model.get("vision"),
                        False,
                    ),
                    supports_reasoning=_bool_or_default(
                        raw_model.get("supports_reasoning") or raw_model.get("reasoning"),
                        False,
                    ),
                )
            )

        return models or self.list_models()

    async def close(self) -> None:
        await self._client.aclose()


class OpenAIProvider(OpenAICompatibleProvider):
    """First-party OpenAI provider with Chat Completions and Responses API support."""

    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        models: list[ModelInfo] | None = None,
        **kwargs: Any,
    ):
        auth_type = str(kwargs.get("auth_type", "api") or "api")
        runtime_base_url = _CODEX_BASE_URL if auth_type == "oauth" else base_url
        super().__init__(
            api_key=api_key,
            base_url=runtime_base_url,
            models=models if models is not None else _clone_models(self.name, _OPENAI_MODELS),
            **kwargs,
        )
        self._auth_type = auth_type
        self._api_type = str(kwargs.get("api_type", "chat") or "chat")
        if self._auth_type == "oauth":
            self._account_id = self._extract_account_id(api_key or "")
        else:
            self._account_id = ""

    @staticmethod
    def _extract_account_id(access_token: str) -> str:
        """Extract chatgpt_account_id from the JWT access token."""
        from worker_ai.oauth import extract_openai_account_id, parse_jwt_claims

        claims = parse_jwt_claims(access_token)
        return extract_openai_account_id(claims)

    def _uses_responses_api(self) -> bool:
        return self._auth_type == "oauth" or self._api_type == "responses"

    def _build_responses_request(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking_level: str = "off",
    ) -> tuple[str, dict[str, Any], dict[str, str]]:
        body = _build_responses_body(
            model,
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )
        headers = merge_headers(
            {"content-type": "application/json"},
            {"authorization": f"Bearer {self.api_key}"} if self.api_key else None,
            self.headers,
        )
        if self._account_id:
            headers["openai-organization"] = self._account_id
        return "/responses", body, headers

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
        if self._uses_responses_api():
            async for event in self._stream_responses_api(
                model,
                messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking_level=thinking_level,
            ):
                yield event
            return

        async for event in self._stream_chat_completions(
            model,
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        ):
            yield event

    async def _stream_responses_api(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking_level: str = "off",
    ) -> AsyncIterator[StreamEvent]:
        """Stream via the OpenAI/Codex Responses API."""
        path, body, headers = self._build_responses_request(
            model,
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )

        async with self._client.stream(
            "POST",
            path,
            json=body,
            headers=headers,
        ) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                msg = f"Codex API error {response.status_code}: {error_body.decode()}"
                raise RuntimeError(msg)

            pending_fc: dict[int, dict[str, Any]] = {}
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

                if event_type == "response.output_text.delta":
                    delta = chunk.get("delta", "")
                    if delta:
                        yield TextDelta(content=delta)
                elif event_type == "response.reasoning_summary_text.delta":
                    delta = chunk.get("delta", "")
                    if delta:
                        yield ReasoningDelta(content=delta)
                elif event_type == "response.output_item.added":
                    item = chunk.get("item", {})
                    if item.get("type") == "function_call":
                        idx = chunk.get("output_index", 0)
                        pending_fc[idx] = {
                            "call_id": item.get("call_id", ""),
                            "name": item.get("name", ""),
                            "args": "",
                        }
                elif event_type == "response.function_call_arguments.delta":
                    idx = chunk.get("output_index", 0)
                    if idx in pending_fc:
                        pending_fc[idx]["args"] += chunk.get("delta", "")
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
                elif event_type == "response.completed":
                    resp = chunk.get("response", {})
                    u = resp.get("usage", {})
                    usage.input_tokens = u.get("input_tokens", 0)
                    usage.output_tokens = u.get("output_tokens", 0)
                    usage.reasoning_tokens = u.get("output_tokens_details", {}).get(
                        "reasoning_tokens",
                        0,
                    )
                    stop_reason = resp.get("status", "completed")
                    yield Done(stop_reason=stop_reason, usage=usage)

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
        """Backward-compatible alias for the ChatGPT Codex Responses flow."""
        async for event in self._stream_responses_api(
            model,
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        ):
            yield event


OpenAICompatProvider = OpenAIProvider
