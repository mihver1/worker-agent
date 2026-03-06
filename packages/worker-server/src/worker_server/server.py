"""WebSocket server for remote Worker access."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection
from worker_ai.oauth import (
    OAuthToken,
    RemoteOAuthChallenge,
    TokenStore,
    complete_remote_oauth_challenge,
    start_remote_oauth_challenge,
)
from worker_core.agent import AgentEventType, AgentSession
from worker_core.bootstrap import (
    bootstrap_runtime,
    create_agent_session_from_bootstrap,
    provider_requires_api_key,
)
from worker_core.config import (
    ProviderConfig,
    WorkerConfig,
    load_config,
    persist_server_auth_token,
    resolve_model,
)
from worker_core.extensions import ExtensionContext, load_server_extensions_async
from worker_core.provider_resolver import (
    get_effective_model_info,
    get_effective_provider_catalog,
)
from worker_core.provider_setup import collect_provider_setup_entries

from worker_server.provider_overlay import (
    load_provider_overlay,
    merge_provider_overlay,
    save_provider_overlay,
    upsert_provider_overlay,
)

logger = logging.getLogger("worker.server")


@dataclass
class ServerState:
    config: WorkerConfig
    sessions: dict[str, AgentSession] = field(default_factory=dict)
    session_provider_models: dict[str, str] = field(default_factory=dict)
    provider_overlay: dict[str, ProviderConfig] = field(default_factory=dict)
    pending_oauth: dict[str, RemoteOAuthChallenge] = field(default_factory=dict)
    server_extensions: list[Any] = field(default_factory=list)


def _session_model_label(
    state: ServerState,
    session_id: str,
    session: AgentSession | None,
) -> str:
    if session_id in state.session_provider_models:
        return state.session_provider_models[session_id]
    if session is not None and session.model:
        return session.model
    return state.config.agent.model


def _serialize_session(state: ServerState, session_id: str) -> dict[str, Any]:
    session = state.sessions.get(session_id)
    return {
        "id": session_id,
        "model": _session_model_label(state, session_id, session),
        "messages": len(session.messages) if session is not None else 0,
        "exists": session is not None,
    }


async def _create_server_session(
    state: ServerState,
    session_id: str,
    *,
    provider_name: str | None = None,
    model_id: str | None = None,
    prior_messages: list[Any] | None = None,
) -> AgentSession:
    from worker_core.cli import _resolve_api_key

    resolved_provider, resolved_model = (
        (provider_name, model_id)
        if provider_name is not None and model_id is not None
        else resolve_model(state.config)
    )
    runtime = await bootstrap_runtime(
        state.config,
        resolved_provider,
        resolved_model,
        project_dir=os.getcwd(),
        resolve_api_key=_resolve_api_key,
        include_extensions=True,
        runtime="server",
    )
    session = create_agent_session_from_bootstrap(
        state.config,
        runtime,
        project_dir=os.getcwd(),
        session_id=session_id,
    )
    if prior_messages:
        session.messages.extend(prior_messages)
    state.sessions[session_id] = session
    state.session_provider_models[session_id] = f"{resolved_provider}/{resolved_model}"
    return session


async def _switch_server_session_model(
    state: ServerState,
    session_id: str,
    model_str: str,
) -> dict[str, Any]:
    if "/" not in model_str:
        raise RuntimeError("Format: provider/model-id")
    provider_name, model_id = model_str.split("/", 1)
    model_info = await get_effective_model_info(state.config, provider_name, model_id)
    if model_info is None:
        raise RuntimeError(
            f"Model '{model_id}' not found for provider '{provider_name}'."
        )

    previous = state.sessions.pop(session_id, None)
    prior_messages = previous.messages[1:] if previous is not None else []
    if previous is not None:
        with suppress(Exception):
            await previous.provider.close()

    await _create_server_session(
        state,
        session_id,
        provider_name=provider_name,
        model_id=model_id,
        prior_messages=prior_messages,
    )
    return _serialize_session(state, session_id)


async def _run_server_shell(command: str) -> tuple[str, int]:
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=os.getcwd(),
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode(errors="replace").rstrip()
    if len(output) > 5000:
        output = output[:5000] + f"\n... (truncated, {len(stdout)} bytes total)"
    return output, proc.returncode


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
        if len(state.sessions) >= state.config.server.max_sessions:
            await ws.send(
                json.dumps(
                    {
                        "type": "error",
                        "error": (
                            f"Maximum sessions reached "
                            f"({state.config.server.max_sessions})"
                        ),
                    }
                )
            )
            return
        try:
            session = await _create_server_session(state, session_id)
        except Exception as e:
            await ws.send(json.dumps({"type": "error", "error": str(e)}))
            return

    session = state.sessions[session_id]

    async for event in session.run(content):
        payload: dict[str, Any] = {"type": event.type.value}

        if event.type in {
            AgentEventType.TEXT_DELTA,
            AgentEventType.REASONING_DELTA,
        }:
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


# ── REST API (aiohttp) ────────────────────────────────────────────


def _create_rest_app(state: ServerState, token: str) -> Any:
    """Create aiohttp REST application for management endpoints."""
    from aiohttp import web
    @web.middleware
    async def auth_middleware(
        request: web.Request, handler: Any,
    ) -> web.StreamResponse:
        # Skip auth for health endpoint
        if request.path == "/api/health":
            return await handler(request)
        auth_header = request.headers.get("Authorization", "")
        if token and auth_header != f"Bearer {token}":
            return web.json_response({"error": "Unauthorized"}, status=401)
        return await handler(request)

    async def handle_health(request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "sessions": len(state.sessions),
            "max_sessions": state.config.server.max_sessions,
        })
    async def handle_providers(request: web.Request) -> web.Response:
        from worker_core.cli import _resolve_api_key

        entries = await collect_provider_setup_entries(state.config, _resolve_api_key)
        return web.json_response(
            {
                "providers": [
                    {
                        "id": entry.id,
                        "name": entry.name,
                        "status": entry.status,
                        "hint": entry.hint,
                    }
                    for entry in entries
                ]
            }
        )

    async def handle_models(request: web.Request) -> web.Response:
        from worker_core.cli import _resolve_api_key

        catalog = await get_effective_provider_catalog(state.config)
        providers_payload: list[dict[str, Any]] = []
        for provider_id, provider in catalog.items():
            requires_key = provider_requires_api_key(state.config, provider_id)
            api_key, _ = await _resolve_api_key(state.config, provider_id)
            if not api_key and requires_key:
                continue
            providers_payload.append(
                {
                    "id": provider_id,
                    "name": provider.name,
                    "models": [
                        {
                            "id": model.id,
                            "name": model.name,
                            "context_window": model.context_window,
                        }
                        for model in provider.models
                    ],
                }
            )
        return web.json_response({"providers": providers_payload})

    async def handle_sessions_list(request: web.Request) -> web.Response:
        sessions_info = [_serialize_session(state, sid) for sid in state.sessions]
        return web.json_response({"sessions": sessions_info})
    async def handle_session_get(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        return web.json_response({"session": _serialize_session(state, sid)})

    async def handle_session_model_put(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        model = str(payload.get("model", "")).strip()
        if not model:
            return web.json_response({"error": "Missing model"}, status=400)
        try:
            session = await _switch_server_session_model(state, sid, model)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"session": session})

    async def handle_session_bash(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        command = str(payload.get("command", "")).strip()
        if not command:
            return web.json_response({"error": "Missing command"}, status=400)
        output, exit_code = await _run_server_shell(command)
        return web.json_response(
            {
                "command": command,
                "output": output,
                "exit_code": exit_code,
            }
        )

    async def handle_credentials_import(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        providers = payload.get("providers", [])
        if not isinstance(providers, list):
            return web.json_response({"error": "Missing providers list"}, status=400)

        imported: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        overlay_changed = False
        token_store = TokenStore()

        for item in providers:
            if not isinstance(item, dict):
                skipped.append({"provider": "", "reason": "Invalid provider payload."})
                continue
            provider_id = str(item.get("provider", "")).strip()
            auth = item.get("auth", {})
            settings = item.get("settings", {})
            if not provider_id or not isinstance(auth, dict) or not isinstance(settings, dict):
                skipped.append(
                    {
                        "provider": provider_id,
                        "reason": "Missing provider/auth/settings fields.",
                    }
                )
                continue

            kind = str(auth.get("kind", "")).strip()
            overlay_update = dict(settings)
            if kind == "api_key":
                api_key = str(auth.get("api_key", "")).strip()
                if not api_key:
                    skipped.append({"provider": provider_id, "reason": "Missing api_key."})
                    continue
                overlay_update["api_key"] = api_key
            elif kind == "oauth_token":
                raw_token = auth.get("token")
                if not isinstance(raw_token, dict):
                    skipped.append(
                        {"provider": provider_id, "reason": "Missing OAuth token payload."}
                    )
                    continue
                token = OAuthToken(**raw_token)
                token.provider = provider_id
                token_store.save(token)
            else:
                skipped.append(
                    {
                        "provider": provider_id,
                        "reason": f"Unsupported auth kind: {kind or '(empty)'}",
                    }
                )
                continue

            if overlay_update:
                provider_config = upsert_provider_overlay(
                    state.provider_overlay,
                    provider_id,
                    overlay_update,
                )
                merge_provider_overlay(state.config, {provider_id: provider_config})
                overlay_changed = True
            imported.append({"provider": provider_id, "auth_kind": kind})

        if overlay_changed:
            save_provider_overlay(state.provider_overlay)
        return web.json_response({"imported": imported, "skipped": skipped})

    async def handle_oauth_start(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        provider = str(payload.get("provider", "")).strip()
        redirect_uri = str(payload.get("redirect_uri", "")).strip()
        if not provider:
            return web.json_response({"error": "Missing provider"}, status=400)
        try:
            challenge, descriptor = start_remote_oauth_challenge(
                provider,
                redirect_uri=redirect_uri,
            )
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

        login_id = secrets.token_hex(16)
        state.pending_oauth[login_id] = challenge
        descriptor["login_id"] = login_id
        return web.json_response(descriptor)

    async def handle_oauth_complete(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        login_id = str(payload.get("login_id", "")).strip()
        oauth_payload = payload.get("payload", {})
        if not login_id or not isinstance(oauth_payload, dict):
            return web.json_response({"error": "Missing login payload"}, status=400)

        challenge = state.pending_oauth.pop(login_id, None)
        if challenge is None:
            return web.json_response({"error": "Login session not found"}, status=404)

        try:
            token_value = await complete_remote_oauth_challenge(challenge, oauth_payload)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

        TokenStore().save(token_value)
        return web.json_response(
            {
                "provider": challenge.provider,
                "auth_kind": "oauth_token",
                "status": "ok",
            }
        )

    async def handle_session_delete(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        if sid in state.sessions:
            session = state.sessions.pop(sid)
            state.session_provider_models.pop(sid, None)
            with suppress(Exception):
                await session.provider.close()
            return web.json_response({"deleted": sid})
        return web.json_response({"error": "Session not found"}, status=404)

    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/providers", handle_providers)
    app.router.add_get("/api/models", handle_models)
    app.router.add_post("/api/credentials/import", handle_credentials_import)
    app.router.add_post("/api/oauth/start", handle_oauth_start)
    app.router.add_post("/api/oauth/complete", handle_oauth_complete)
    app.router.add_get("/api/sessions", handle_sessions_list)
    app.router.add_get("/api/sessions/{session_id}", handle_session_get)
    app.router.add_put("/api/sessions/{session_id}/model", handle_session_model_put)
    app.router.add_post("/api/sessions/{session_id}/bash", handle_session_bash)
    app.router.add_delete("/api/sessions/{session_id}", handle_session_delete)
    for ext in state.server_extensions:
        with suppress(Exception):
            ext.configure_rest_app(app)
    return app


# ── Server entrypoint ─────────────────────────────────────────────


async def run_server(
    host: str | None = None,
    port: int | None = None,
    auth_token: str = "",
    announce: Callable[[str], None] | None = None,
) -> None:
    """Start WebSocket + REST server.

    *host* and *port* override ``config.server.host`` / ``config.server.port``.
    If not provided, values from the loaded config are used.
    """
    from aiohttp import web

    project_dir = os.getcwd()
    config = load_config(project_dir)
    provider_overlay = load_provider_overlay()
    merge_provider_overlay(config, provider_overlay)
    context = ExtensionContext(project_dir=project_dir, runtime="server", config=config)
    server_extensions = await load_server_extensions_async(context=context)
    state = ServerState(
        config=config,
        provider_overlay=provider_overlay,
        server_extensions=server_extensions,
    )

    # Use explicit args → config → defaults
    host = host or config.server.host
    port = port if port is not None else config.server.port

    token = auth_token or config.server.auth_token
    generated_token_path = None
    if not token:
        token = f"wkr_{secrets.token_hex(16)}"
        generated_token_path = persist_server_auth_token(token, project_dir=project_dir)
        config.server.auth_token = token
        logger.info("Generated auth token: %s", token)
        logger.info("Saved generated auth token to %s", generated_token_path)

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
    if announce is not None:
        announce("Worker server starting")
        announce(f"  WebSocket: ws://{host}:{port}")
        announce(f"  REST API:  http://{host}:{rest_port}/api/")
        announce(f"  Auth token: {token}")
        if generated_token_path is not None:
            announce(f"  Saved auth token to: {generated_token_path}")

    async with websockets.serve(ws_handler, host, port):  # type: ignore[attr-defined]
        await asyncio.Future()  # Run forever
