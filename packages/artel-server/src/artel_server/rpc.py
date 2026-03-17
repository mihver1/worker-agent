"""JSON-RPC 2.0 server over stdin/stdout.

Allows embedding Artel as a subprocess in editors, IDEs, and other tools.
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
from typing import Any

from artel_core.agent import AgentEventType, AgentSession
from artel_core.bootstrap import (
    bootstrap_runtime,
    create_agent_session_from_bootstrap,
)
from artel_core.config import load_config, resolve_model


def _jsonrpc_response(request_id: Any, result: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result})


def _jsonrpc_error(request_id: Any, code: int, message: str) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }
    )


def _jsonrpc_notification(method: str, params: dict[str, Any]) -> str:
    return json.dumps({"jsonrpc": "2.0", "method": method, "params": params})


class RpcServer:
    """JSON-RPC server running over stdin/stdout."""

    def __init__(self) -> None:
        self._session: AgentSession | None = None
        self._running = True

    async def _init_session(self) -> AgentSession:
        """Create an agent session from config."""
        from artel_core.cli import _resolve_api_key

        config = load_config(os.getcwd())
        provider_name, model_id = resolve_model(config)
        runtime = await bootstrap_runtime(
            config,
            provider_name,
            model_id,
            project_dir=os.getcwd(),
            resolve_api_key=_resolve_api_key,
            include_extensions=True,
            runtime="rpc",
        )
        session = create_agent_session_from_bootstrap(
            config,
            runtime,
            project_dir=os.getcwd(),
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

            if event.type in {
                AgentEventType.TEXT_DELTA,
                AgentEventType.REASONING_DELTA,
            }:
                evt["content"] = event.content
            elif event.type == AgentEventType.TOOL_CALL:
                evt["tool"] = event.tool_name
                evt["args"] = event.tool_args
                evt["call_id"] = event.tool_call_id
            elif event.type == AgentEventType.TOOL_RESULT:
                evt["call_id"] = event.tool_call_id
                evt["output"] = event.content
                if event.display is not None:
                    evt["display"] = event.display
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

    async def close(self) -> None:
        if self._session:
            await self._session.provider.close()
            mcp_runtime = getattr(self._session, "mcp_runtime", None)
            if mcp_runtime is not None:
                await mcp_runtime.close()
            lsp_runtime = getattr(self._session, "lsp_runtime", None)
            if lsp_runtime is not None:
                await lsp_runtime.close()
            self._session = None


async def run_rpc() -> None:
    """Entry point for the RPC server."""
    server = RpcServer()
    try:
        await server.run()
    finally:
        await server.close()
