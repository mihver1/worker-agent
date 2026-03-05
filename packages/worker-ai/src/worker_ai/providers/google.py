"""Google Gemini API provider with streaming."""

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

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"

_MODELS: list[ModelInfo] = [
    ModelInfo(
        id="gemini-2.5-pro",
        provider="google",
        name="Gemini 2.5 Pro",
        context_window=1_048_576,
        max_output_tokens=65_536,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=True,
        input_price_per_m=1.25,
        output_price_per_m=10.0,
    ),
    ModelInfo(
        id="gemini-2.5-flash",
        provider="google",
        name="Gemini 2.5 Flash",
        context_window=1_048_576,
        max_output_tokens=65_536,
        supports_tools=True,
        supports_vision=True,
        supports_reasoning=True,
        input_price_per_m=0.15,
        output_price_per_m=0.60,
    ),
]


def _build_contents(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    system: str | None = None
    contents: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == Role.SYSTEM:
            system = msg.content
            continue
        role = "model" if msg.role == Role.ASSISTANT else "user"
        if msg.role == Role.TOOL and msg.tool_result:
            contents.append(
                {
                    "role": "function",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": msg.tool_result.tool_call_id,
                                "response": {"result": msg.tool_result.content},
                            }
                        }
                    ],
                }
            )
            continue
        parts: list[dict[str, Any]] = [{"text": msg.content}]
        if msg.tool_calls:
            for tc in msg.tool_calls:
                parts.append(
                    {"functionCall": {"name": tc.name, "args": tc.arguments}}
                )
        contents.append({"role": role, "parts": parts})
    return system, contents


def _build_tools(tools: list[ToolDef]) -> list[dict[str, Any]]:
    declarations = []
    for t in tools:
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in t.parameters:
            properties[p.name] = {"type": p.type.upper(), "description": p.description}
            if p.required:
                required.append(p.name)
        declarations.append(
            {
                "name": t.name,
                "description": t.description,
                "parameters": {"type": "OBJECT", "properties": properties, "required": required},
            }
        )
    return [{"functionDeclarations": declarations}]


class GoogleProvider(Provider):
    """Google Gemini API with streaming."""

    name = "google"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        self._client = httpx.AsyncClient(
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
        system, contents = _build_contents(messages)

        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {"temperature": temperature},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if max_tokens:
            body["generationConfig"]["maxOutputTokens"] = max_tokens
        if tools:
            body["tools"] = _build_tools(tools)

        url = (
            f"{self._base_url}/v1beta/models/{model}:streamGenerateContent"
            f"?key={self.api_key or ''}&alt=sse"
        )

        async with self._client.stream("POST", url, json=body) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                raise RuntimeError(
                    f"Gemini API error {response.status_code}: {error_body.decode()}"
                )

            usage = Usage()
            async for raw_line in response.aiter_lines():
                if not raw_line.startswith("data: "):
                    continue
                try:
                    chunk = json.loads(raw_line[6:])
                except json.JSONDecodeError:
                    continue

                # Usage
                usage_meta = chunk.get("usageMetadata", {})
                if usage_meta:
                    usage.input_tokens = usage_meta.get("promptTokenCount", 0)
                    usage.output_tokens = usage_meta.get("candidatesTokenCount", 0)
                    usage.reasoning_tokens = usage_meta.get("thoughtsTokenCount", 0)

                candidates = chunk.get("candidates", [])
                if not candidates:
                    continue
                candidate = candidates[0]
                parts = candidate.get("content", {}).get("parts", [])

                for part in parts:
                    if "text" in part:
                        if part.get("thought"):
                            yield ReasoningDelta(content=part["text"])
                        else:
                            yield TextDelta(content=part["text"])
                    elif "functionCall" in part:
                        fc = part["functionCall"]
                        yield ToolCallDelta(
                            id=fc.get("name", ""),
                            name=fc.get("name", ""),
                            arguments=fc.get("args", {}),
                        )

                finish_reason = candidate.get("finishReason", "")
                if finish_reason:
                    yield Done(stop_reason=finish_reason, usage=usage)

    def list_models(self) -> list[ModelInfo]:
        return list(_MODELS)

    async def close(self) -> None:
        await self._client.aclose()
