"""Google Gemini providers with streaming for Gemini API and Vertex AI."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx

from artel_ai.attachments import attachment_data_base64
from artel_ai.models import (
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
from artel_ai.provider import Provider, build_httpx_timeout, merge_headers
from artel_ai.tool_schema import json_schema_to_gemini_schema, tool_input_schema

_DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"
_VERTEX_BASE_URL_TEMPLATE = "https://{location}-aiplatform.googleapis.com"
_DEFAULT_VERTEX_LOCATION = "global"
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

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


def _build_parts(msg: Message) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if msg.content:
        parts.append({"text": msg.content})
    for attachment in msg.attachments or []:
        parts.append(
            {
                "inlineData": {
                    "mimeType": attachment.mime_type,
                    "data": attachment_data_base64(attachment),
                }
            }
        )
    return parts or [{"text": ""}]


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
        parts = _build_parts(msg)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                parts.append({"functionCall": {"name": tc.name, "args": tc.arguments}})
        contents.append({"role": role, "parts": parts})
    return system, contents


def _build_tools(tools: list[ToolDef]) -> list[dict[str, Any]]:
    declarations = []
    for t in tools:
        declarations.append(
            {
                "name": t.name,
                "description": t.description,
                "parameters": json_schema_to_gemini_schema(tool_input_schema(t)),
            }
        )
    return [{"functionDeclarations": declarations}]


def _build_body(
    messages: list[Message],
    *,
    tools: list[ToolDef] | None,
    temperature: float,
    max_tokens: int | None,
    thinking_level: str,
) -> dict[str, Any]:
    system, contents = _build_contents(messages)
    body: dict[str, Any] = {
        "contents": contents,
        "generationConfig": {"temperature": temperature},
    }
    if thinking_level != "off":
        budget_map = {
            "minimal": 1024,
            "low": 2048,
            "medium": 4096,
            "high": 8192,
            "xhigh": 16384,
        }
        body["generationConfig"]["thinkingConfig"] = {
            "thinkingBudget": budget_map.get(thinking_level, 4096)
        }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if max_tokens:
        body["generationConfig"]["maxOutputTokens"] = max_tokens
    if tools:
        body["tools"] = _build_tools(tools)
    return body


def _iter_chunk_events(chunk: dict[str, Any], usage: Usage) -> Iterator[StreamEvent]:
    usage_meta = chunk.get("usageMetadata", {})
    if usage_meta:
        usage.input_tokens = usage_meta.get("promptTokenCount", 0)
        usage.output_tokens = usage_meta.get("candidatesTokenCount", 0)
        usage.reasoning_tokens = usage_meta.get("thoughtsTokenCount", 0)

    candidates = chunk.get("candidates", [])
    if not candidates:
        return

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts", [])
    for part in parts:
        if "text" in part:
            if part.get("thought"):
                yield ReasoningDelta(content=part["text"])
            else:
                yield TextDelta(content=part["text"])
        elif "functionCall" in part:
            function_call = part["functionCall"]
            tool_name = function_call.get("name", "")
            yield ToolCallDelta(
                id=tool_name,
                name=tool_name,
                arguments=function_call.get("args", {}),
            )

    finish_reason = candidate.get("finishReason", "")
    if finish_reason:
        yield Done(stop_reason=finish_reason, usage=usage)


def _clone_models(provider_name: str) -> list[ModelInfo]:
    return [model.model_copy(update={"provider": provider_name}) for model in _MODELS]


def _first_non_empty(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def _normalize_scopes(value: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if value is None:
        return (_CLOUD_PLATFORM_SCOPE,)
    if isinstance(value, str):
        scopes = tuple(part.strip() for part in value.split(",") if part.strip())
    else:
        scopes = tuple(part.strip() for part in value if isinstance(part, str) and part.strip())
    return scopes or (_CLOUD_PLATFORM_SCOPE,)


def _resolve_vertex_base_url(base_url: str | None, location: str) -> str:
    raw_base_url = (base_url or _VERTEX_BASE_URL_TEMPLATE).rstrip("/")
    if "{location}" in raw_base_url or "{region}" in raw_base_url:
        return raw_base_url.format(location=location, region=location)
    return raw_base_url


async def _iter_vertex_stream_objects(
    response: httpx.Response,
) -> AsyncIterator[dict[str, Any]]:
    decoder = json.JSONDecoder()
    buffer = ""

    async for chunk in response.aiter_text():
        buffer += chunk
        while True:
            buffer = buffer.lstrip()
            if not buffer:
                break

            if buffer.startswith("data:"):
                line, separator, rest = buffer.partition("\n")
                if not separator:
                    break
                buffer = rest
                data_str = line[5:].strip()
                if data_str == "[DONE]":
                    return
                try:
                    payload = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
            else:
                if buffer[0] in "[,":
                    buffer = buffer[1:]
                    continue
                if buffer[0] == "]":
                    buffer = buffer[1:]
                    continue
                try:
                    payload, end = decoder.raw_decode(buffer)
                except json.JSONDecodeError:
                    break
                buffer = buffer[end:]

            if isinstance(payload, dict):
                yield payload
            elif isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        yield item


class GoogleProvider(Provider):
    """Google Gemini API with streaming."""

    name = "google"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        self._base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
        default_timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
        self._client = httpx.AsyncClient(
            timeout=build_httpx_timeout(self.timeout, default=default_timeout),
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
        body = _build_body(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )
        url = (
            f"{self._base_url}/v1beta/models/{model}:streamGenerateContent"
            f"?key={self.api_key or ''}&alt=sse"
        )
        request_headers = merge_headers({"content-type": "application/json"}, self.headers)

        async with self._client.stream("POST", url, json=body, headers=request_headers) as response:
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

                for event in _iter_chunk_events(chunk, usage):
                    yield event

    def list_models(self) -> list[ModelInfo]:
        return _clone_models(self.name)

    async def close(self) -> None:
        await self._client.aclose()


class GoogleVertexProvider(Provider):
    """Google Vertex AI Gemini provider using ADC or service-account auth."""

    name = "google_vertex"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
        project = kwargs.get("project")
        location = kwargs.get("location") or kwargs.get("region")
        credentials_path = kwargs.get("credentials_path")

        self._project = _first_non_empty(
            project if isinstance(project, str) else "",
            os.environ.get("GOOGLE_VERTEX_PROJECT", ""),
            os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        )
        self._location = _first_non_empty(
            location if isinstance(location, str) else "",
            os.environ.get("GOOGLE_VERTEX_LOCATION", ""),
            os.environ.get("GOOGLE_CLOUD_LOCATION", ""),
            _DEFAULT_VERTEX_LOCATION,
        )
        self._credentials_path = _first_non_empty(
            credentials_path if isinstance(credentials_path, str) else "",
            os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""),
        )
        self._scopes = _normalize_scopes(kwargs.get("scopes"))
        self._base_url = _resolve_vertex_base_url(base_url, self._location)
        self._credentials: Any | None = None
        self._auth_request: Any | None = None
        self._resolved_project = self._project

        default_timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
        self._client = httpx.AsyncClient(
            timeout=build_httpx_timeout(self.timeout, default=default_timeout),
        )

    def _ensure_credentials(self) -> None:
        if self.api_key or self._credentials is not None:
            return
        try:
            import google.auth
            import urllib3
            from google.auth import credentials as google_auth_credentials
            from google.auth.transport.urllib3 import Request as GoogleAuthRequest
            from google.oauth2 import service_account
        except ImportError as exc:
            msg = (
                "google_vertex requires google-auth and urllib3. "
                "Sync dependencies before using Vertex AI."
            )
            raise RuntimeError(msg) from exc

        if self._credentials_path:
            credentials = service_account.Credentials.from_service_account_file(
                self._credentials_path,
                scopes=list(self._scopes),
            )
            resolved_project = self._project
        else:
            credentials, detected_project = google.auth.default(scopes=list(self._scopes))
            credentials = google_auth_credentials.with_scopes_if_required(
                credentials,
                list(self._scopes),
            )
            resolved_project = self._project or detected_project or ""

        self._credentials = credentials
        self._resolved_project = self._project or resolved_project
        self._auth_request = GoogleAuthRequest(urllib3.PoolManager())

    def _resolve_access_token(self) -> tuple[str, str]:
        if self.api_key:
            if not self._project:
                msg = (
                    "Google Vertex requires a project when using an explicit access token. "
                    "Set providers.google_vertex.project or GOOGLE_VERTEX_PROJECT."
                )
                raise RuntimeError(msg)
            return self.api_key, self._project

        self._ensure_credentials()
        if self._credentials is None or self._auth_request is None:
            raise RuntimeError("Google Vertex credentials could not be initialized.")
        if not self._resolved_project:
            msg = (
                "Google Vertex project is required. Set providers.google_vertex.project "
                "or GOOGLE_VERTEX_PROJECT/GOOGLE_CLOUD_PROJECT."
            )
            raise RuntimeError(msg)

        token = getattr(self._credentials, "token", "")
        is_valid = bool(getattr(self._credentials, "valid", False))
        is_expired = bool(getattr(self._credentials, "expired", False))
        if not token or not is_valid or is_expired:
            self._credentials.refresh(self._auth_request)
            token = getattr(self._credentials, "token", "")
        if not token:
            raise RuntimeError("Google Vertex credentials did not produce an access token.")
        return token, self._resolved_project

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
        access_token, project = self._resolve_access_token()
        body = _build_body(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )
        url = (
            f"{self._base_url}/v1/projects/{project}/locations/{self._location}"
            f"/publishers/google/models/{model}:streamGenerateContent"
        )
        request_headers = merge_headers(
            {
                "authorization": f"Bearer {access_token}",
                "content-type": "application/json",
            },
            self.headers,
        )

        async with self._client.stream("POST", url, json=body, headers=request_headers) as response:
            if response.status_code != 200:
                error_body = await response.aread()
                raise RuntimeError(
                    f"Vertex AI API error {response.status_code}: {error_body.decode()}"
                )

            usage = Usage()
            async for chunk in _iter_vertex_stream_objects(response):
                for event in _iter_chunk_events(chunk, usage):
                    yield event

    def list_models(self) -> list[ModelInfo]:
        return _clone_models(self.name)

    async def close(self) -> None:
        await self._client.aclose()
