"""WebSocket server for remote Worker access."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection

from worker_ai.providers import create_default_registry
from worker_core.agent import AgentEventType, AgentSession
from worker_core.config import WorkerConfig, load_config, resolve_model
from worker_core.tools.builtins import create_builtin_tools

logger = logging.getLogger("worker.server")


@dataclass
class ServerState:
    config: WorkerConfig
    sessions: dict[str, AgentSession] = field(default_factory=dict)


async def handle_client(ws: ServerConnection, state: ServerState) -> None:
    """Handle a single WebSocket client connection."""
    logger.info("Client connected: %s", ws.remote_address)

    try:
        async for raw_msg in ws:
            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                await ws.send(json.dumps({"type": "error", "error": "Invalid JSON"}))
                continue

            msg_type = msg.get("type", "")

            if msg_type == "message":
                await _handle_message(ws, msg, state)
            elif msg_type == "cancel":
                # TODO: implement cancellation
                pass
            elif msg_type == "approve_tool":
                # TODO: implement permission approval
                pass
            else:
                await ws.send(json.dumps({"type": "error", "error": f"Unknown type: {msg_type}"}))

    except websockets.exceptions.ConnectionClosed:
        logger.info("Client disconnected: %s", ws.remote_address)


async def _handle_message(ws: ServerConnection, msg: dict[str, Any], state: ServerState) -> None:
    """Process a user message through the agent loop and stream results."""
    session_id = msg.get("session_id", "default")
    content = msg.get("content", "")

    if not content:
        await ws.send(json.dumps({"type": "error", "error": "Empty message"}))
        return

    # Get or create session
    if session_id not in state.sessions:
        provider_name, model_id = resolve_model(state.config)
        registry = create_default_registry()

        api_key = _resolve_api_key(state.config, provider_name)
        prov_cfg = state.config.providers.get(provider_name)
        kwargs: dict[str, str] = {}
        if prov_cfg and prov_cfg.base_url:
            kwargs["base_url"] = prov_cfg.base_url

        provider = registry.create(provider_name, api_key=api_key, **kwargs)
        tools = create_builtin_tools(os.getcwd())
        session = AgentSession(provider=provider, model=model_id, tools=tools)
        state.sessions[session_id] = session

    session = state.sessions[session_id]

    async for event in session.run(content):
        payload: dict[str, Any] = {"type": event.type.value}

        if event.type == AgentEventType.TEXT_DELTA:
            payload["content"] = event.content
        elif event.type == AgentEventType.REASONING_DELTA:
            payload["content"] = event.content
        elif event.type == AgentEventType.TOOL_CALL:
            payload["tool"] = event.tool_name
            payload["args"] = event.tool_args
            payload["call_id"] = event.tool_call_id
        elif event.type == AgentEventType.TOOL_RESULT:
            payload["call_id"] = event.tool_call_id
            payload["output"] = event.content
        elif event.type == AgentEventType.DONE:
            if event.usage:
                payload["usage"] = {
                    "input": event.usage.input_tokens,
                    "output": event.usage.output_tokens,
                }
        elif event.type == AgentEventType.ERROR:
            payload["error"] = event.error

        await ws.send(json.dumps(payload))


def _resolve_api_key(config: WorkerConfig, provider_name: str) -> str | None:
    from worker_core.cli import _ENV_KEY_MAP

    prov_cfg = config.providers.get(provider_name)
    if prov_cfg and prov_cfg.api_key:
        return prov_cfg.api_key
    env_var = _ENV_KEY_MAP.get(provider_name)
    if env_var:
        return os.environ.get(env_var)
    return None


# ── REST API (aiohttp) ────────────────────────────────────────────


def _create_rest_app(state: ServerState, token: str) -> Any:
    """Create aiohttp REST application for management endpoints."""
    from aiohttp import web

    async def auth_middleware(app: web.Application, handler: Any) -> Any:
        async def middleware_handler(request: web.Request) -> web.StreamResponse:
            # Skip auth for health endpoint
            if request.path == "/api/health":
                return await handler(request)
            auth_header = request.headers.get("Authorization", "")
            if auth_header != f"Bearer {token}":
                return web.json_response({"error": "Unauthorized"}, status=401)
            return await handler(request)
        return middleware_handler

    async def handle_health(request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "sessions": len(state.sessions),
            "max_sessions": state.config.server.max_sessions,
        })

    async def handle_sessions_list(request: web.Request) -> web.Response:
        sessions_info = [
            {
                "id": sid,
                "model": s.model,
                "messages": len(s.messages),
            }
            for sid, s in state.sessions.items()
        ]
        return web.json_response({"sessions": sessions_info})

    async def handle_session_delete(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        if sid in state.sessions:
            del state.sessions[sid]
            return web.json_response({"deleted": sid})
        return web.json_response({"error": "Session not found"}, status=404)

    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/sessions", handle_sessions_list)
    app.router.add_delete("/api/sessions/{session_id}", handle_session_delete)
    return app


# ── Server entrypoint ─────────────────────────────────────────────


async def run_server(host: str = "0.0.0.0", port: int = 7432, auth_token: str = "") -> None:
    """Start WebSocket + REST server."""
    from aiohttp import web

    config = load_config(os.getcwd())
    state = ServerState(config=config)

    token = auth_token or config.server.auth_token
    if not token:
        token = f"wkr_{secrets.token_hex(16)}"
        logger.info("Generated auth token: %s", token)

    # Bearer auth for WebSocket
    async def ws_handler(ws: ServerConnection) -> None:
        # Check origin header for bearer token
        req_headers = ws.request.headers if ws.request else {}  # type: ignore[union-attr]
        auth_header = ""
        if hasattr(req_headers, "get"):
            auth_header = req_headers.get("Authorization", "")  # type: ignore[arg-type]
        if token and auth_header != f"Bearer {token}":
            await ws.close(4001, "Unauthorized")
            return
        await handle_client(ws, state)

    # Start REST API on port+1
    rest_port = port + 1
    rest_app = _create_rest_app(state, token)
    rest_runner = web.AppRunner(rest_app)
    await rest_runner.setup()
    rest_site = web.TCPSite(rest_runner, host, rest_port)
    await rest_site.start()

    logger.info("Worker server starting")
    logger.info("  WebSocket: ws://%s:%d", host, port)
    logger.info("  REST API:  http://%s:%d/api/", host, rest_port)
    logger.info("  Auth token: %s", token)

    async with websockets.serve(ws_handler, host, port) as server:  # type: ignore[attr-defined]
        await asyncio.Future()  # Run forever
