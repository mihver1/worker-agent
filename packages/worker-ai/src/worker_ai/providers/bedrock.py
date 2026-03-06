"""Amazon Bedrock provider using the ConverseStream runtime API."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any

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

_DEFAULT_CONNECT_TIMEOUT = 10
_DEFAULT_READ_TIMEOUT = 300
_DEFAULT_REASONING_BUDGET = {
    "minimal": 1024,
    "low": 2048,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
}
_DEFAULT_MAX_TOKENS = 16_384
_KNOWN_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="anthropic.claude-3-7-sonnet-20250219-v1:0",
        provider="bedrock",
        name="Claude 3.7 Sonnet on Bedrock",
        context_window=200_000,
        max_output_tokens=65_536,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=True,
    ),
]


def _first_non_empty(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def _normalize_timeout_seconds(timeout: int | bool | None, *, default: int) -> int:
    if timeout is None or timeout is False or isinstance(timeout, bool):
        return default
    return max(int(timeout / 1000), 1)


def _build_tools(tools: list[ToolDef]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for tool in tools:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for param in tool.parameters:
            prop: dict[str, Any] = {"type": param.type, "description": param.description}
            if param.enum:
                prop["enum"] = param.enum
            properties[param.name] = prop
            if param.required:
                required.append(param.name)
        result.append(
            {
                "toolSpec": {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": properties,
                            "required": required,
                        }
                    },
                }
            }
        )
    return result


def _build_messages(messages: list[Message]) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    system_blocks: list[dict[str, str]] = []
    api_messages: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == Role.SYSTEM:
            if msg.content:
                system_blocks.append({"text": msg.content})
            continue

        if msg.role == Role.TOOL and msg.tool_result is not None:
            tool_result: dict[str, Any] = {
                "toolUseId": msg.tool_result.tool_call_id,
                "content": [{"text": msg.tool_result.content}],
            }
            if msg.tool_result.is_error:
                tool_result["status"] = "error"
            api_messages.append({"role": "user", "content": [{"toolResult": tool_result}]})
            continue

        content: list[dict[str, Any]] = []
        if msg.content:
            content.append({"text": msg.content})
        if msg.role == Role.ASSISTANT and msg.tool_calls:
            for tool_call in msg.tool_calls:
                content.append(
                    {
                        "toolUse": {
                            "toolUseId": tool_call.id,
                            "name": tool_call.name,
                            "input": tool_call.arguments,
                        }
                    }
                )
        if not content:
            continue
        role = "assistant" if msg.role == Role.ASSISTANT else "user"
        api_messages.append({"role": role, "content": content})

    return system_blocks, api_messages


def _parse_tool_input(raw_input: str) -> dict[str, Any]:
    try:
        return json.loads(raw_input) if raw_input else {}
    except json.JSONDecodeError:
        return {"_raw": raw_input}


class BedrockProvider(Provider):
    """Amazon Bedrock runtime provider built on top of boto3 ConverseStream."""

    name = "bedrock"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self._region = _first_non_empty(
            str(kwargs.get("region", "") or ""),
            os.environ.get("AWS_REGION", ""),
            os.environ.get("AWS_DEFAULT_REGION", ""),
        )
        self._profile = _first_non_empty(
            str(kwargs.get("profile", "") or ""),
            os.environ.get("AWS_PROFILE", ""),
        )
        self._access_key_id = _first_non_empty(
            str(kwargs.get("access_key_id", "") or ""),
            str(kwargs.get("aws_access_key_id", "") or ""),
            os.environ.get("AWS_ACCESS_KEY_ID", ""),
        )
        self._secret_access_key = _first_non_empty(
            str(kwargs.get("secret_access_key", "") or ""),
            str(kwargs.get("aws_secret_access_key", "") or ""),
            os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        )
        self._session_token = _first_non_empty(
            str(kwargs.get("session_token", "") or ""),
            str(kwargs.get("aws_session_token", "") or ""),
            os.environ.get("AWS_SESSION_TOKEN", ""),
        )
        self._base_url = (base_url or "").rstrip("/")
        self._client: Any | None = None

    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        try:
            import boto3
            from botocore.config import Config as BotocoreConfig
        except ImportError as exc:
            msg = "bedrock requires boto3. Sync dependencies before using Bedrock."
            raise RuntimeError(msg) from exc

        if self._access_key_id and not self._secret_access_key:
            msg = "Bedrock secret_access_key is required when access_key_id is provided."
            raise RuntimeError(msg)
        if self._secret_access_key and not self._access_key_id:
            msg = "Bedrock access_key_id is required when secret_access_key is provided."
            raise RuntimeError(msg)

        session_kwargs: dict[str, Any] = {}
        if self._profile:
            session_kwargs["profile_name"] = self._profile
        if self._region:
            session_kwargs["region_name"] = self._region
        if self._access_key_id and self._secret_access_key:
            session_kwargs["aws_access_key_id"] = self._access_key_id
            session_kwargs["aws_secret_access_key"] = self._secret_access_key
        if self._session_token:
            session_kwargs["aws_session_token"] = self._session_token

        config = BotocoreConfig(
            connect_timeout=_normalize_timeout_seconds(
                self.timeout,
                default=_DEFAULT_CONNECT_TIMEOUT,
            ),
            read_timeout=_normalize_timeout_seconds(
                self.timeout,
                default=_DEFAULT_READ_TIMEOUT,
            ),
        )
        session = boto3.Session(**session_kwargs)
        client_kwargs: dict[str, Any] = {"config": config}
        if self._region:
            client_kwargs["region_name"] = self._region
        if self._base_url:
            client_kwargs["endpoint_url"] = self._base_url
        try:
            self._client = session.client("bedrock-runtime", **client_kwargs)
        except Exception as exc:
            msg = "Failed to initialize Bedrock runtime client."
            raise RuntimeError(msg) from exc

    def _build_request(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None,
        temperature: float,
        max_tokens: int | None,
        thinking_level: str,
    ) -> dict[str, Any]:
        system_blocks, api_messages = _build_messages(messages)
        request: dict[str, Any] = {"modelId": model, "messages": api_messages}
        if system_blocks:
            request["system"] = system_blocks

        inference_config: dict[str, Any] = {"temperature": temperature}
        model_info = next((m for m in _KNOWN_MODELS if m.id == model), None)
        max_tokens_value = max_tokens or (
            model_info.max_output_tokens if model_info is not None else _DEFAULT_MAX_TOKENS
        )
        if thinking_level != "off" and "claude" in model:
            budget = _DEFAULT_REASONING_BUDGET.get(thinking_level, 4096)
            request["additionalModelRequestFields"] = {
                "thinking": {"type": "enabled", "budget_tokens": budget}
            }
            max_tokens_value = max(max_tokens_value, budget + 1)
        inference_config["maxTokens"] = max_tokens_value
        request["inferenceConfig"] = inference_config

        if tools:
            request["toolConfig"] = {"tools": _build_tools(tools)}
        return request

    async def _iter_response_stream(self, response_stream: Any) -> AsyncIterator[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()
        sentinel = object()
        errors: list[BaseException] = []

        def _pump() -> None:
            try:
                for event in response_stream:
                    loop.call_soon_threadsafe(queue.put_nowait, event)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                close = getattr(response_stream, "close", None)
                if callable(close):
                    with suppress(Exception):
                        close()
                loop.call_soon_threadsafe(queue.put_nowait, sentinel)

        thread = threading.Thread(target=_pump, daemon=True)
        thread.start()

        while True:
            item = await queue.get()
            if item is sentinel:
                break
            assert isinstance(item, dict)
            yield item

        if errors:
            raise RuntimeError(f"Bedrock streaming error: {errors[0]}") from errors[0]

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
        self._ensure_client()
        assert self._client is not None

        request = self._build_request(
            model,
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )
        response = await asyncio.to_thread(self._client.converse_stream, **request)
        response_stream = response.get("stream")
        if response_stream is None:
            raise RuntimeError("Bedrock response did not include a stream.")

        usage = Usage()
        pending_stop_reason = "end_turn"
        pending_tools: dict[int, dict[str, str]] = {}
        emitted_done = False

        async for event in self._iter_response_stream(response_stream):
            exception_key = next((key for key in event if key.endswith("Exception")), "")
            if exception_key:
                raise RuntimeError(f"Bedrock API error: {event[exception_key]}")

            if "contentBlockStart" in event:
                block = event["contentBlockStart"]
                start = block.get("start", {})
                tool_use = start.get("toolUse")
                if tool_use:
                    pending_tools[block.get("contentBlockIndex", 0)] = {
                        "id": tool_use.get("toolUseId", ""),
                        "name": tool_use.get("name", ""),
                        "input_json": "",
                    }
                continue

            if "contentBlockDelta" in event:
                block = event["contentBlockDelta"]
                delta = block.get("delta", {})
                if "text" in delta and delta["text"]:
                    yield TextDelta(content=delta["text"])
                reasoning_content = delta.get("reasoningContent", {})
                if reasoning_content.get("text"):
                    yield ReasoningDelta(content=reasoning_content["text"])
                tool_use = delta.get("toolUse")
                if tool_use and "input" in tool_use:
                    idx = block.get("contentBlockIndex", 0)
                    pending = pending_tools.get(idx)
                    if pending is not None:
                        pending["input_json"] += tool_use.get("input", "")
                continue

            if "contentBlockStop" in event:
                idx = event["contentBlockStop"].get("contentBlockIndex", 0)
                pending = pending_tools.pop(idx, None)
                if pending is not None:
                    yield ToolCallDelta(
                        id=pending["id"],
                        name=pending["name"],
                        arguments=_parse_tool_input(pending["input_json"]),
                    )
                continue

            if "messageStop" in event:
                pending_stop_reason = event["messageStop"].get("stopReason", "end_turn")
                continue

            if "metadata" in event:
                metadata = event["metadata"]
                usage_meta = metadata.get("usage", {})
                usage.input_tokens = usage_meta.get("inputTokens", usage.input_tokens)
                usage.output_tokens = usage_meta.get("outputTokens", usage.output_tokens)
                emitted_done = True
                yield Done(stop_reason=pending_stop_reason, usage=usage)

        if not emitted_done:
            yield Done(stop_reason=pending_stop_reason, usage=usage)

    def list_models(self) -> list[ModelInfo]:
        return [model.model_copy() for model in _KNOWN_MODELS]
