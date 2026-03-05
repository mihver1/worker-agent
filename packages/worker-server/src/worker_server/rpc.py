"""JSON-RPC 2.0 server over stdin/stdout.

Allows embedding Worker as a subprocess in editors, IDEs, and other tools.
Protocol: one JSON object per line on stdin, one JSON object per line on stdout.

Supported methods:
    message     — send a user message, streams back events
    cancel      — abort the current run
    compact     — compact session history
    list_tools  — list available tools
    ping        — health check

Events are sent as JSON-RPC notifications (no id) with method="event".
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import Any

from worker_ai.providers import create_default_registry
from worker_core.agent import AgentEventType, AgentSession
from worker_core.config import load_config, resolve_model
from worker_core.extensions import load_extensions
from worker_core.tools.builtins import create_builtin_tools


def _jsonrpc_response(id: Any, result: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id, "result": result})


def _jsonrpc_error(id: Any, code: int, message: str) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})


def _jsonrpc_notification(method: str, params: dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "method": method, "params": params})


class RpcServer:
    """JSON-RPC server running over stdin/stdout."""

    def __init__(self) -> None:
        self._session: AgentSession | None = None
        self._running = True

    async def _init_session(self) -> AgentSession:
        """Create an agent session from config."""
        from worker_core.cli import _resolve_api_key

        config = load_config(os.getcwd())
        provider_name, model_id = resolve_model(config)
        registry = create_default_registry()
        api_key, auth_type = _resolve_api_key(config, provider_name)

        prov_cfg = config.providers.get(provider_name)
        kwargs: dict[str, Any] = {}
        if prov_cfg and prov_cfg.base_url:
            kwargs["base_url"] = prov_cfg.base_url
        if auth_type == "oauth":
            kwargs["auth_type"] = "oauth"

        provider = registry.create(provider_name, api_key=api_key, **kwargs)
        tools = create_builtin_tools(os.getcwd())

        extensions, hooks = load_extensions()
        for ext in extensions:
            tools.extend(ext.get_tools())

        session = AgentSession(
            provider=provider,
            model=model_id,
            tools=tools,
            system_prompt=config.agent.system_prompt,
            project_dir=os.getcwd(),
            temperature=config.agent.temperature,
            max_turns=config.agent.max_turns,
            hooks=hooks,
        )
        return session

    def _write(self, line: str) -> None:
        """Write a JSON line to stdout."""
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    async def handle_request(self, data: dict[str, Any]) -> None:
        """Dispatch a single JSON-RPC request."""
        req_id = data.get("id")
        method = data.get("method", "")
        params = data.get("params", {})

        if method == "ping":
            self._write(_jsonrpc_response(req_id, {"status": "ok"}))

        elif method == "message":
            await self._handle_message(req_id, params)

        elif method == "cancel":
            if self._session:
                self._session.abort()
            self._write(_jsonrpc_response(req_id, {"cancelled": True}))

        elif method == "compact":
            if self._session:
                summary = await self._session.compact(params.get("prompt", ""))
                self._write(_jsonrpc_response(req_id, {"summary": summary}))
            else:
                self._write(_jsonrpc_error(req_id, -32000, "No session"))

        elif method == "list_tools":
            if not self._session:
                self._session = await self._init_session()
            tool_names = list(self._session.tools.keys())
            self._write(_jsonrpc_response(req_id, {"tools": tool_names}))

        elif method == "shutdown":
            self._write(_jsonrpc_response(req_id, {"shutdown": True}))
            self._running = False

        else:
            self._write(_jsonrpc_error(req_id, -32601, f"Method not found: {method}"))

    async def _handle_message(self, req_id: Any, params: dict[str, Any]) -> None:
        """Process a user message and stream events as notifications."""
        content = params.get("content", "")
        if not content:
            self._write(_jsonrpc_error(req_id, -32602, "Missing 'content' param"))
            return

        if not self._session:
            self._session = await self._init_session()

        async for event in self._session.run(content):
            evt: dict[str, Any] = {"type": event.type.value}

            if event.type == AgentEventType.TEXT_DELTA:
                evt["content"] = event.content
            elif event.type == AgentEventType.REASONING_DELTA:
                evt["content"] = event.content
            elif event.type == AgentEventType.TOOL_CALL:
                evt["tool"] = event.tool_name
                evt["args"] = event.tool_args
                evt["call_id"] = event.tool_call_id
            elif event.type == AgentEventType.TOOL_RESULT:
                evt["call_id"] = event.tool_call_id
                evt["output"] = event.content
            elif event.type == AgentEventType.DONE:
                if event.usage:
                    evt["usage"] = {
                        "input_tokens": event.usage.input_tokens,
                        "output_tokens": event.usage.output_tokens,
                    }
            elif event.type == AgentEventType.ERROR:
                evt["error"] = event.error
            elif event.type == AgentEventType.COMPACT:
                evt["content"] = event.content

            self._write(_jsonrpc_notification("event", evt))

        # Final response acknowledging the message was processed
        self._write(_jsonrpc_response(req_id, {"done": True}))

    async def run(self) -> None:
        """Main loop: read JSON lines from stdin, dispatch."""
        loop = asyncio.get_event_loop()

        while self._running:
            # Read a line from stdin (blocking, via executor)
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break  # EOF

            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                self._write(_jsonrpc_error(None, -32700, "Parse error"))
                continue

            await self.handle_request(data)


async def run_rpc() -> None:
    """Entry point for the RPC server."""
    server = RpcServer()
    await server.run()
