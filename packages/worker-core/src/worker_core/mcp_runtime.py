"""Optional MCP runtime manager for Artel built on merged global/project stores."""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from contextlib import AsyncExitStack, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
from worker_ai.models import Message, Role, ToolCall, ToolDef, ToolResult
from worker_ai.tool_schema import normalize_json_schema

from worker_core.execution import get_current_tool_execution_context
from worker_core.extensions import ExtensionContext
from worker_core.mcp import LoadedMCPConfig, MCPRegistry, MCPServerConfig
from worker_core.mcp_formatting import (
    format_call_tool_result,
    format_prompt_result,
    format_prompts_listing,
    format_read_resource_result,
    format_resources_listing,
)
from worker_core.tools import Tool

logger = logging.getLogger("artel.mcp")

try:  # pragma: no cover - exercised only when dependency is installed
    from mcp import ClientSession, StdioServerParameters, types
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client
except Exception:  # pragma: no cover - graceful fallback when optional dep is absent
    ClientSession = None
    StdioServerParameters = None
    types = None
    sse_client = None
    stdio_client = None
    streamable_http_client = None


McpServerState = Literal[
    "connected",
    "disabled",
    "failed",
    "needs_auth",
    "timeout",
    "unavailable",
]


@dataclass(slots=True)
class McpServerRuntime:
    name: str
    config: MCPServerConfig
    exit_stack: AsyncExitStack
    session: Any
    source_label: str
    endpoint_label: str
    tools: list[Any] = field(default_factory=list)
    prompts: list[Any] = field(default_factory=list)
    resources: list[Any] = field(default_factory=list)
    resource_templates: list[Any] = field(default_factory=list)


@dataclass(slots=True)
class McpServerStatus:
    name: str
    state: McpServerState
    transport: str
    enabled: bool
    source: str
    endpoint: str
    tool_prefix: str
    include_tools: bool
    include_prompts: bool
    include_resources: bool
    tools: int = 0
    prompts: int = 0
    resources: int = 0
    templates: int = 0
    error: str = ""

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


class McpCallableTool(Tool):
    """Simple callable-backed Artel tool that preserves raw JSON Schema."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[..., Any],
    ) -> None:
        self.name = name
        self.description = description
        self._input_schema = input_schema
        self._handler = handler

    async def execute(self, **kwargs: Any) -> str:
        result = self._handler(**kwargs)
        if hasattr(result, "__await__"):
            result = await result
        return str(result)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[],
            input_schema=self._input_schema,
        )


class McpRuntimeManager:
    """Connection and catalog manager for configured MCP servers."""

    def __init__(self) -> None:
        self.context: ExtensionContext | None = None
        self.config: LoadedMCPConfig = LoadedMCPConfig(servers={}, sources=[])
        self.servers: dict[str, McpServerRuntime] = {}
        self.tools: list[Tool] = []
        self.errors: dict[str, str] = {}
        self.server_statuses: dict[str, McpServerStatus] = {}
        self.available = ClientSession is not None

    async def load(self, context: ExtensionContext) -> None:
        self.context = context
        self.errors = {}
        self.config = MCPRegistry().load_merged_config(context.project_dir or os.getcwd())
        self.servers = {}
        self.tools = []
        self.server_statuses = {}
        if not self.available:
            if self.config.servers:
                message = "Python package 'mcp' is not installed."
                self.errors["runtime"] = message
                for server_name, server_config in self.config.servers.items():
                    self.server_statuses[server_name] = self._build_status(
                        server_name,
                        server_config,
                        state="unavailable",
                        error=message,
                    )
            return
        for server_name, server_config in self.config.servers.items():
            if not server_config.enabled:
                self.server_statuses[server_name] = self._build_status(
                    server_name,
                    server_config,
                    state="disabled",
                )
                continue
            try:
                runtime = await self._connect_server(server_name, server_config)
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                self.errors[server_name] = message
                self.server_statuses[server_name] = self._build_status(
                    server_name,
                    server_config,
                    state=_classify_error_state(exc),
                    error=message,
                )
                logger.exception("Failed to connect MCP server %s", server_name)
                continue
            self.servers[server_name] = runtime
            self.server_statuses[server_name] = self._build_status(
                server_name,
                server_config,
                state="connected",
                runtime=runtime,
            )
        self._rebuild_tools()

    async def reload(self) -> None:
        if self.context is None:
            raise RuntimeError("MCP runtime has no extension context")
        await self.close()
        await self.load(self.context)

    async def close(self) -> None:
        for runtime in self.servers.values():
            with suppress(Exception):
                await runtime.exit_stack.aclose()
        self.servers = {}
        self.tools = []

    def status_payload(self) -> dict[str, Any]:
        servers = [self.server_statuses[name].to_payload() for name in sorted(self.server_statuses)]
        summary = {
            "connected": sum(1 for item in servers if item["state"] == "connected"),
            "disabled": sum(1 for item in servers if item["state"] == "disabled"),
            "failed": sum(1 for item in servers if item["state"] == "failed"),
            "needs_auth": sum(1 for item in servers if item["state"] == "needs_auth"),
            "timeout": sum(1 for item in servers if item["state"] == "timeout"),
            "unavailable": sum(1 for item in servers if item["state"] == "unavailable"),
            "total": len(servers),
        }
        return {
            "available": self.available,
            "sources": [str(path) for path in self.config.sources],
            "servers": servers,
            "summary": summary,
        }

    def status_text(self) -> str:
        lines: list[str] = []
        if self.config.sources:
            lines.append("Config sources:")
            lines.extend(f"- {path}" for path in self.config.sources)
        else:
            lines.append("No MCP config found.")

        statuses = [self.server_statuses[name] for name in sorted(self.server_statuses)]
        connected = [status for status in statuses if status.state == "connected"]
        other = [status for status in statuses if status.state != "connected"]
        if connected:
            lines.append("")
            lines.append("Connected servers:")
            for status in connected:
                lines.append(self._format_status_line(status))
        if other:
            lines.append("")
            lines.append("Other servers:")
            for status in other:
                lines.append(self._format_status_line(status))
        return "\n".join(line for line in lines if line is not None).strip()

    def _format_status_line(self, status: McpServerStatus) -> str:
        line = (
            f"- {status.name} [{status.transport}] state={status.state} "
            f"tools={status.tools} prompts={status.prompts} "
            f"resources={status.resources} templates={status.templates}"
        )
        if status.error:
            line += f" error={status.error}"
        return line

    def _build_status(
        self,
        server_name: str,
        server_config: MCPServerConfig,
        *,
        state: McpServerState,
        runtime: McpServerRuntime | None = None,
        error: str = "",
    ) -> McpServerStatus:
        return McpServerStatus(
            name=server_name,
            state=state,
            transport=server_config.transport,
            enabled=server_config.enabled,
            source=runtime.source_label
            if runtime is not None
            else (server_config.command or server_config.url),
            endpoint=runtime.endpoint_label
            if runtime is not None
            else (server_config.url or server_config.command),
            tool_prefix=server_config.tool_prefix,
            include_tools=server_config.include_tools,
            include_prompts=server_config.include_prompts,
            include_resources=server_config.include_resources,
            tools=len(runtime.tools) if runtime is not None else 0,
            prompts=len(runtime.prompts) if runtime is not None else 0,
            resources=len(runtime.resources) if runtime is not None else 0,
            templates=len(runtime.resource_templates) if runtime is not None else 0,
            error=error,
        )

    async def _connect_server(
        self,
        server_name: str,
        server_config: MCPServerConfig,
    ) -> McpServerRuntime:
        assert StdioServerParameters is not None
        assert stdio_client is not None
        assert streamable_http_client is not None
        assert sse_client is not None
        assert ClientSession is not None

        stack = AsyncExitStack()
        endpoint_label = server_config.transport
        headers, auth = self._resolve_remote_auth(server_config)
        if server_config.transport == "stdio":
            env = dict(os.environ)
            env.update(server_config.env)
            params = StdioServerParameters(
                command=server_config.command,
                args=server_config.args,
                env=env,
                cwd=server_config.cwd,
                encoding=server_config.encoding,
                encoding_error_handler=server_config.encoding_error_handler,
            )
            read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
            source_label = server_config.command
        elif server_config.transport == "streamable_http":
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=headers or None,
                    timeout=httpx.Timeout(
                        server_config.timeout,
                        read=server_config.sse_read_timeout,
                    ),
                    auth=auth,
                )
            )
            read_stream, write_stream, get_endpoint = await stack.enter_async_context(
                streamable_http_client(server_config.url, http_client=http_client)
            )
            source_label = server_config.url
            endpoint_label = get_endpoint() or server_config.url
        else:
            read_stream, write_stream = await stack.enter_async_context(
                sse_client(
                    server_config.url,
                    headers=headers or None,
                    timeout=server_config.timeout,
                    sse_read_timeout=server_config.sse_read_timeout,
                    auth=auth,
                )
            )
            source_label = server_config.url
            endpoint_label = server_config.url

        session = await stack.enter_async_context(
            ClientSession(
                read_stream,
                write_stream,
                sampling_callback=self._sampling_callback,
                elicitation_callback=self._elicitation_callback,
                list_roots_callback=self._list_roots_callback,
            )
        )
        await session.initialize()

        runtime = McpServerRuntime(
            name=server_name,
            config=server_config,
            exit_stack=stack,
            session=session,
            source_label=source_label,
            endpoint_label=endpoint_label,
        )
        await self._refresh_catalog(runtime)
        return runtime

    async def _refresh_catalog(self, runtime: McpServerRuntime) -> None:
        runtime.tools = (
            await self._safe_collect_paginated(runtime.session.list_tools, "tools")
            if runtime.config.include_tools
            else []
        )
        runtime.prompts = (
            await self._safe_collect_paginated(runtime.session.list_prompts, "prompts")
            if runtime.config.include_prompts
            else []
        )
        runtime.resources = (
            await self._safe_collect_paginated(runtime.session.list_resources, "resources")
            if runtime.config.include_resources
            else []
        )
        runtime.resource_templates = (
            await self._safe_collect_paginated(
                runtime.session.list_resource_templates,
                "resourceTemplates",
            )
            if runtime.config.include_resources
            else []
        )

    async def _collect_paginated(self, fetch: Callable[..., Any], attribute: str) -> list[Any]:
        items: list[Any] = []
        cursor: str | None = None
        while True:
            result = await fetch(cursor=cursor)
            items.extend(getattr(result, attribute, []))
            cursor = getattr(result, "nextCursor", None)
            if not cursor:
                break
        return items

    async def _safe_collect_paginated(self, fetch: Callable[..., Any], attribute: str) -> list[Any]:
        try:
            return await self._collect_paginated(fetch, attribute)
        except Exception:
            return []

    def _rebuild_tools(self) -> None:
        tools: list[Tool] = []
        for runtime in self.servers.values():
            prefix = self._tool_prefix(runtime)
            if runtime.config.include_tools:
                for tool in runtime.tools:
                    tool_name = f"{prefix}{_sanitize_name(getattr(tool, 'name', 'tool'))}"
                    description = (
                        getattr(tool, "description", "")
                        or getattr(tool, "title", "")
                        or f"MCP tool {getattr(tool, 'name', 'tool')}"
                    )
                    input_schema = normalize_json_schema(
                        getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}
                    )

                    async def _handler(
                        _runtime: McpServerRuntime = runtime,
                        _tool_name: str = getattr(tool, "name", "tool"),
                        **kwargs: Any,
                    ) -> str:
                        result = await _runtime.session.call_tool(_tool_name, kwargs or None)
                        return format_call_tool_result(result)

                    tools.append(
                        McpCallableTool(
                            name=tool_name,
                            description=f"[{runtime.name}] {description}",
                            input_schema=input_schema,
                            handler=_handler,
                        )
                    )

            if runtime.config.include_prompts:
                tools.append(
                    McpCallableTool(
                        name=f"{prefix}prompt_list",
                        description=f"[{runtime.name}] List available MCP prompts",
                        input_schema={"type": "object", "properties": {}},
                        handler=lambda _runtime=runtime: format_prompts_listing(_runtime.prompts),
                    )
                )
                tools.append(
                    McpCallableTool(
                        name=f"{prefix}prompt_get",
                        description=f"[{runtime.name}] Resolve an MCP prompt by name",
                        input_schema=_prompt_schema(runtime.prompts),
                        handler=self._make_get_prompt_handler(runtime),
                    )
                )

            if runtime.config.include_resources:
                tools.append(
                    McpCallableTool(
                        name=f"{prefix}resource_list",
                        description=f"[{runtime.name}] List MCP resources and templates",
                        input_schema={"type": "object", "properties": {}},
                        handler=lambda _runtime=runtime: format_resources_listing(
                            _runtime.resources,
                            _runtime.resource_templates,
                        ),
                    )
                )
                tools.append(
                    McpCallableTool(
                        name=f"{prefix}resource_read",
                        description=f"[{runtime.name}] Read an MCP resource URI",
                        input_schema=_resource_schema(
                            runtime.resources,
                            runtime.resource_templates,
                        ),
                        handler=self._make_read_resource_handler(runtime),
                    )
                )

        self.tools = tools

    def _make_get_prompt_handler(self, runtime: McpServerRuntime) -> Callable[..., Any]:
        async def _handler(prompt: str, arguments: dict[str, str] | None = None) -> str:
            result = await runtime.session.get_prompt(prompt, arguments or None)
            return format_prompt_result(result)

        return _handler

    def _make_read_resource_handler(self, runtime: McpServerRuntime) -> Callable[..., Any]:
        async def _handler(uri: str) -> str:
            result = await runtime.session.read_resource(uri)
            return format_read_resource_result(result)

        return _handler

    async def _sampling_callback(self, _request_context: Any, params: Any) -> Any:
        if types is None:
            raise RuntimeError("mcp types unavailable")
        execution_context = get_current_tool_execution_context()
        if execution_context is None:
            return types.CreateMessageResult(
                role="assistant",
                content=types.TextContent(
                    type="text",
                    text="Sampling unavailable: no active Artel session.",
                ),
                model="artel-mcp",
                stopReason="endTurn",
            )

        session = execution_context.session
        messages = _sampling_messages_to_artel(params)
        text_chunks: list[str] = []
        tool_use_requested = False

        async for event in session.provider.stream_chat(
            session.model,
            messages,
            tools=_mcp_tools_to_artel_tool_defs(getattr(params, "tools", None)),
            temperature=(
                getattr(params, "temperature", None)
                if getattr(params, "temperature", None) is not None
                else session.temperature
            ),
            max_tokens=getattr(params, "maxTokens", None),
            thinking_level=session.thinking_level,
        ):
            if type(event).__name__ == "TextDelta":
                text_chunks.append(getattr(event, "content", ""))
            elif type(event).__name__ == "ToolCallDelta":
                tool_use_requested = True

        text = "".join(text_chunks).strip()
        if not text and tool_use_requested:
            text = "Model requested tool use during sampling."
        if not text:
            text = "(empty sampling response)"

        return types.CreateMessageResult(
            role="assistant",
            content=types.TextContent(type="text", text=text),
            model=session.model,
            stopReason="toolUse" if tool_use_requested else "endTurn",
        )

    async def _list_roots_callback(self, _request_context: Any) -> Any:
        if types is None:
            raise RuntimeError("mcp types unavailable")
        roots: list[Any] = []
        project_dir = self.context.project_dir if self.context is not None else os.getcwd()
        all_roots = [project_dir]
        for runtime in self.servers.values():
            all_roots.extend(runtime.config.roots)
        seen: set[str] = set()
        for root in all_roots:
            resolved = str(Path(root).resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            roots.append(types.Root(uri=Path(resolved).as_uri(), name=Path(resolved).name))
        return types.ListRootsResult(roots=roots)

    async def _elicitation_callback(self, _request_context: Any, _params: Any) -> Any:
        if types is None:
            raise RuntimeError("mcp types unavailable")
        logger.warning("MCP elicitation requested but interactive elicitation is not implemented.")
        return types.ElicitResult(action="cancel")

    def _resolve_remote_auth(
        self, server_config: MCPServerConfig
    ) -> tuple[dict[str, str], httpx.Auth | None]:
        headers = dict(server_config.headers)
        auth = dict(server_config.auth)
        auth_type = str(auth.get("type", "none") or "none")
        if auth_type == "bearer":
            token = str(auth.get("token", "") or "") or os.environ.get(
                str(auth.get("token_env", "") or ""), ""
            )
            if token:
                headers["Authorization"] = f"Bearer {token}"
            return headers, None
        if auth_type == "basic":
            username = str(auth.get("username", "") or "") or os.environ.get(
                str(auth.get("username_env", "") or ""), ""
            )
            password = str(auth.get("password", "") or "") or os.environ.get(
                str(auth.get("password_env", "") or ""), ""
            )
            return headers, httpx.BasicAuth(username, password)
        return headers, None

    def _tool_prefix(self, runtime: McpServerRuntime) -> str:
        configured = runtime.config.tool_prefix.strip()
        if configured:
            return configured
        return f"mcp__{_sanitize_name(runtime.name)}__"


def _prompt_schema(prompts: list[Any]) -> dict[str, Any]:
    names = [getattr(prompt, "name", "") for prompt in prompts if getattr(prompt, "name", "")]
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "MCP prompt name",
            },
            "arguments": {
                "type": "object",
                "description": "Prompt arguments as string key/value pairs",
                "additionalProperties": {"type": "string"},
            },
        },
        "required": ["prompt"],
    }
    if names:
        schema["properties"]["prompt"]["enum"] = names
    return schema


def _resource_schema(resources: list[Any], resource_templates: list[Any]) -> dict[str, Any]:
    uris = [
        str(getattr(resource, "uri", ""))
        for resource in resources
        if getattr(resource, "uri", None)
    ]
    template_descriptions = [
        str(getattr(template, "uriTemplate", ""))
        for template in resource_templates
        if getattr(template, "uriTemplate", None)
    ]
    description = "MCP resource URI to read"
    if template_descriptions:
        description += f"; templates: {', '.join(template_descriptions)}"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "uri": {
                "type": "string",
                "description": description,
            }
        },
        "required": ["uri"],
    }
    if uris:
        schema["properties"]["uri"]["enum"] = uris
    return schema


def _sanitize_name(value: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9_]+", "_", value).strip("_").lower()
    return sanitized or "server"


def _classify_error_state(exc: Exception) -> McpServerState:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    text = str(exc).strip().lower()
    if any(
        token in text
        for token in ("401", "403", "unauthorized", "forbidden", "auth", "credential", "token")
    ):
        return "needs_auth"
    if any(token in text for token in ("timed out", "timeout")):
        return "timeout"
    return "failed"


def _mcp_tools_to_artel_tool_defs(tools: list[Any] | None) -> list[ToolDef] | None:
    if not tools:
        return None
    return [
        ToolDef(
            name=getattr(tool, "name", "tool"),
            description=getattr(tool, "description", "")
            or getattr(tool, "title", "")
            or getattr(tool, "name", "tool"),
            parameters=[],
            input_schema=normalize_json_schema(
                getattr(tool, "inputSchema", None) or {"type": "object", "properties": {}}
            ),
        )
        for tool in tools
    ]


def _sampling_messages_to_artel(params: Any) -> list[Message]:
    messages: list[Message] = []
    system_prompt = getattr(params, "systemPrompt", "")
    if system_prompt:
        messages.append(Message(role=Role.SYSTEM, content=system_prompt))
    for message in getattr(params, "messages", []) or []:
        messages.extend(_sampling_message_to_artel(message))
    return messages


def _sampling_message_to_artel(message: Any) -> list[Message]:
    blocks = getattr(message, "content", [])
    if not isinstance(blocks, list):
        blocks = [blocks]
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    tool_messages: list[Message] = []

    for block in blocks:
        block_type = getattr(block, "type", "")
        if type(block).__name__ == "TextContent":
            text_parts.append(str(getattr(block, "text", "")))
        elif block_type == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=getattr(block, "id", ""),
                    name=getattr(block, "name", ""),
                    arguments=dict(getattr(block, "input", {}) or {}),
                )
            )
        elif block_type == "tool_result":
            tool_messages.append(
                Message(
                    role=Role.TOOL,
                    tool_result=ToolResult(
                        tool_call_id=getattr(block, "toolUseId", ""),
                        content=_format_tool_result_content(block),
                        is_error=bool(getattr(block, "isError", False)),
                    ),
                )
            )
        else:
            text_parts.append(str(block))

    converted: list[Message] = []
    text = "\n".join(part for part in text_parts if part).strip()
    if getattr(message, "role", "user") == "assistant":
        converted.append(Message(role=Role.ASSISTANT, content=text, tool_calls=tool_calls or None))
    else:
        if text:
            converted.append(Message(role=Role.USER, content=text))
        converted.extend(tool_messages)
        if not text and not tool_messages:
            converted.append(Message(role=Role.USER, content=""))
    return converted


def _format_tool_result_content(block: Any) -> str:
    rendered: list[str] = []
    for item in getattr(block, "content", []) or []:
        if type(item).__name__ == "TextContent":
            rendered.append(str(getattr(item, "text", "")))
        else:
            rendered.append(str(item))
    if getattr(block, "structuredContent", None):
        rendered.append(json.dumps(block.structuredContent, ensure_ascii=False, indent=2))
    return "\n".join(rendered).strip() or "(empty tool result)"


__all__ = [
    "McpCallableTool",
    "McpRuntimeManager",
    "McpServerRuntime",
    "McpServerStatus",
    "McpServerState",
]
