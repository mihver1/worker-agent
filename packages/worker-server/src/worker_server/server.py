"""WebSocket server for remote Worker access."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shlex
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.server import ServerConnection
from worker_ai.models import Message
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
from worker_core.sessions import SessionInfo, SessionStore

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
    session_projects: dict[str, str] = field(default_factory=dict)
    session_thinking_levels: dict[str, str] = field(default_factory=dict)
    provider_overlay: dict[str, ProviderConfig] = field(default_factory=dict)
    pending_oauth: dict[str, RemoteOAuthChallenge] = field(default_factory=dict)
    permission_callbacks: dict[
        str,
        Callable[[str, dict[str, Any]], Awaitable[bool]],
    ] = field(default_factory=dict)
    pending_permissions: dict[
        str,
        tuple[str, asyncio.Future[tuple[bool, bool]]],
    ] = field(default_factory=dict)
    auto_approve_sessions: set[str] = field(default_factory=set)
    session_controllers: dict[str, SessionController] = field(default_factory=dict)
    server_extensions: list[Any] = field(default_factory=list)
    default_project_dir: str = ""
    store: SessionStore | None = None

def _tool_kind_for_name(tool_name: str) -> str:
    mapping = {
        "read": "read",
        "write": "edit",
        "edit": "edit",
        "bash": "execute",
        "grep": "search",
        "find": "search",
        "ls": "search",
    }
    return mapping.get(tool_name, "other")


def _tool_locations(tool_name: str, args: dict[str, Any]) -> list[dict[str, Any]] | None:
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    location: dict[str, Any] = {"path": path}
    if tool_name == "read":
        start_line = args.get("start_line")
        if isinstance(start_line, int) and start_line > 0:
            location["line"] = start_line
    return [location]


def _agent_event_payload(event: Any) -> dict[str, Any]:
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
        payload["kind"] = _tool_kind_for_name(event.tool_name)
        locations = _tool_locations(event.tool_name, event.tool_args)
        if locations:
            payload["locations"] = locations
    elif event.type == AgentEventType.TOOL_RESULT:
        payload["tool"] = event.tool_name
        payload["call_id"] = event.tool_call_id
        payload["output"] = event.content
        payload["is_error"] = event.is_error
    elif event.type == AgentEventType.DONE:
        if event.usage:
            payload["usage"] = {
                "input": event.usage.input_tokens,
                "output": event.usage.output_tokens,
            }
    elif event.type == AgentEventType.ERROR:
        payload["error"] = event.error
    return payload


class SessionController:
    """Own an agent run in the background and fan events out to subscribers."""

    def __init__(self, state: ServerState, session_id: str) -> None:
        self.state = state
        self.session_id = session_id
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._run_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._run_task is not None and not self._run_task.done()

    @property
    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, payload: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers):
            queue.put_nowait(payload)

    async def ensure_idle(self) -> None:
        task = self._run_task
        if task is None:
            return
        if not task.done():
            raise RuntimeError("Session is busy")
        with suppress(Exception):
            task.result()

    async def start(self, content: str) -> None:
        async with self._start_lock:
            if self.running:
                raise RuntimeError("Session is busy")
            self._run_task = asyncio.create_task(
                self._run(content),
                name=f"worker-session-{self.session_id}",
            )

    async def abort(self) -> None:
        session = self.state.sessions.get(self.session_id)
        if session is not None:
            session.abort()
        task = self._run_task
        if task is not None:
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(task)

    async def _run(self, content: str) -> None:
        try:
            session = self.state.sessions.get(self.session_id)
            if session is None:
                session = await _create_server_session(self.state, self.session_id)
            asyncio.create_task(self._maybe_generate_title(session, content))
            async for event in session.run(content):
                await self.publish(_agent_event_payload(event))
        except Exception as exc:
            logger.exception("Background session run failed for %s", self.session_id)
            await self.publish({"type": "error", "error": str(exc)})

    async def _maybe_generate_title(self, session: AgentSession, content: str) -> None:
        if self.state.store is None:
            return
        session_info = await self.state.store.get_session(self.session_id)
        if session_info is None or session_info.title:
            return
        try:
            title = await session.generate_title(content)
        except Exception:
            return
        title = title.strip()
        if not title:
            return
        await self.state.store.rename_session(self.session_id, title)
        await self.publish(
            {
                "type": "session_updated",
                "session": await _serialize_session(self.state, self.session_id),
            }
        )


def _get_session_controller(state: ServerState, session_id: str) -> SessionController:
    controller = state.session_controllers.get(session_id)
    if controller is None:
        controller = SessionController(state, session_id)
        state.session_controllers[session_id] = controller
    return controller


async def _ensure_session_idle(state: ServerState, session_id: str) -> None:
    await _get_session_controller(state, session_id).ensure_idle()


def _session_model_label(
    state: ServerState,
    session_id: str,
    session: AgentSession | None,
    session_info: SessionInfo | None = None,
) -> str:
    if session_id in state.session_provider_models:
        return state.session_provider_models[session_id]
    if session_info is not None and session_info.model:
        return session_info.model
    if session is not None and session.model:
        return session.model
    return state.config.agent.model


def _session_project_dir(
    state: ServerState,
    session_id: str,
    session: AgentSession | None,
    session_info: SessionInfo | None = None,
) -> str:
    if session_id in state.session_projects:
        return state.session_projects[session_id]
    if session_info is not None and session_info.project_dir:
        return session_info.project_dir
    if session is not None and session.project_dir:
        return session.project_dir
    if state.default_project_dir:
        return state.default_project_dir
    return os.getcwd()


def _session_thinking_level(
    state: ServerState,
    session_id: str,
    session: AgentSession | None,
    session_info: SessionInfo | None = None,
) -> str:
    if session_id in state.session_thinking_levels:
        return state.session_thinking_levels[session_id]
    if session is not None:
        return session.thinking_level
    if session_info is not None and session_info.thinking_level:
        return session_info.thinking_level
    return state.config.agent.thinking


async def _stored_session_context(
    state: ServerState,
    session_id: str,
) -> tuple[SessionInfo | None, list[Message]]:
    if state.store is None:
        return None, []
    session_info = await state.store.get_session(session_id)
    if session_info is None:
        return None, []
    return session_info, await state.store.get_messages(session_id)


async def _persist_session_record(
    state: ServerState,
    session_id: str,
    *,
    model: str,
    project_dir: str,
    thinking_level: str,
) -> None:
    if state.store is None:
        return
    session_info = await state.store.get_session(session_id)
    if session_info is None:
        await state.store.create_session(
            session_id,
            model,
            project_dir=project_dir,
            thinking_level=thinking_level,
        )
        return
    if session_info.model != model:
        await state.store.update_session_model(session_id, model)
    if session_info.project_dir != project_dir:
        await state.store.update_session_project(session_id, project_dir)
    if session_info.thinking_level != thinking_level:
        await state.store.update_session_thinking(session_id, thinking_level)


async def _initialize_session_state(
    state: ServerState,
    session_id: str,
    *,
    model: str,
    project_dir: str,
    thinking_level: str | None = None,
) -> None:
    state.session_provider_models[session_id] = model
    state.session_projects[session_id] = project_dir
    if thinking_level is not None:
        state.session_thinking_levels[session_id] = thinking_level
    session_info = await state.store.get_session(session_id) if state.store is not None else None
    effective_thinking = thinking_level or _session_thinking_level(
        state,
        session_id,
        state.sessions.get(session_id),
        session_info,
    )
    await _persist_session_record(
        state,
        session_id,
        model=model,
        project_dir=project_dir,
        thinking_level=effective_thinking,
    )


async def _serialize_session(
    state: ServerState,
    session_id: str,
    session_info: SessionInfo | None = None,
) -> dict[str, Any]:
    if session_info is None and state.store is not None:
        session_info = await state.store.get_session(session_id)
    session = state.sessions.get(session_id)
    message_count = len(session.messages) if session is not None else 0
    if message_count == 0 and session_info is not None and state.store is not None:
        message_count = await state.store.count_messages(session_id) + 1
    exists = (
        session is not None
        or session_info is not None
        or session_id in state.session_provider_models
        or session_id in state.session_projects
        or session_id in state.session_thinking_levels
    )
    return {
        "id": session_id,
        "title": session_info.title if session_info is not None else "",
        "model": _session_model_label(state, session_id, session, session_info),
        "project_dir": _session_project_dir(state, session_id, session, session_info),
        "thinking_level": _session_thinking_level(state, session_id, session, session_info),
        "messages": message_count,
        "created_at": session_info.created_at if session_info is not None else "",
        "updated_at": session_info.updated_at if session_info is not None else "",
        "exists": exists,
    }


def _serialize_message(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": message.role.value,
        "content": message.content,
    }
    if message.reasoning:
        payload["reasoning"] = message.reasoning
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": tool_call.id,
                "name": tool_call.name,
                "arguments": tool_call.arguments,
            }
            for tool_call in message.tool_calls
        ]
    if message.tool_result is not None:
        payload["tool_result"] = {
            "tool_call_id": message.tool_result.tool_call_id,
            "content": message.tool_result.content,
            "is_error": message.tool_result.is_error,
        }
    return payload


async def _session_history_messages(state: ServerState, session_id: str) -> list[Message]:
    session = state.sessions.get(session_id)
    if session is not None:
        return list(session.messages[1:])
    if state.store is not None:
        session_info = await state.store.get_session(session_id)
        if session_info is not None:
            return await state.store.get_messages(session_id)
    if (
        session_id in state.session_provider_models
        or session_id in state.session_projects
        or session_id in state.session_thinking_levels
    ):
        return []
    raise RuntimeError("Session not found")


async def _get_or_create_server_session(
    state: ServerState,
    session_id: str,
) -> AgentSession:
    session = state.sessions.get(session_id)
    if session is not None:
        return session
    if len(state.sessions) >= state.config.server.max_sessions:
        raise RuntimeError(f"Maximum sessions reached ({state.config.server.max_sessions})")
    return await _create_server_session(state, session_id)


async def _session_extension_commands(state: ServerState, session_id: str) -> list[str]:
    session = await _get_or_create_server_session(state, session_id)
    return sorted(session.hooks.commands)


async def _run_session_extension_command(
    state: ServerState,
    session_id: str,
    command_name: str,
    arg: str,
) -> tuple[str | None, dict[str, Any]]:
    session = await _get_or_create_server_session(state, session_id)
    handler = session.hooks.commands.get(command_name)
    if handler is None:
        raise RuntimeError(f"Unknown command: {command_name}")
    result = await handler(arg)
    return result, await _serialize_session(state, session_id)


async def _list_serialized_sessions(
    state: ServerState,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if state.store is None:
        return [await _serialize_session(state, sid) for sid in state.sessions]
    stored_sessions = await state.store.list_sessions(limit=limit)
    payload = [await _serialize_session(state, info.id, info) for info in stored_sessions]
    seen = {info.id for info in stored_sessions}
    extra_ids = (
        set(state.sessions)
        | set(state.session_provider_models)
        | set(state.session_projects)
        | set(state.session_thinking_levels)
    )
    for session_id in sorted(extra_ids):
        if session_id not in seen:
            payload.append(await _serialize_session(state, session_id))
    return payload


async def _request_tool_permission(
    state: ServerState,
    session_id: str,
    tool_name: str,
    args: dict[str, Any],
) -> bool:
    if session_id in state.auto_approve_sessions:
        return True
    controller = _get_session_controller(state, session_id)
    if not controller.has_subscribers:
        return False

    request_id = secrets.token_hex(16)
    future: asyncio.Future[tuple[bool, bool]] = asyncio.get_running_loop().create_future()
    state.pending_permissions[request_id] = (session_id, future)
    await controller.publish(
        {
            "type": "permission_request",
            "request_id": request_id,
            "tool": tool_name,
            "args": args,
        }
    )
    try:
        approved, remember = await future
    finally:
        state.pending_permissions.pop(request_id, None)
    if approved and remember:
        state.auto_approve_sessions.add(session_id)
    return approved


def _server_permission_callback(
    state: ServerState,
    session_id: str,
) -> Callable[[str, dict[str, Any]], Awaitable[bool]]:
    async def _callback(tool_name: str, args: dict[str, Any]) -> bool:
        return await _request_tool_permission(state, session_id, tool_name, args)

    return _callback


async def _create_server_session(
    state: ServerState,
    session_id: str,
    *,
    provider_name: str | None = None,
    model_id: str | None = None,
    project_dir: str | None = None,
    prior_messages: list[Any] | None = None,
) -> AgentSession:
    from worker_core.cli import _resolve_api_key

    stored_info, stored_messages = await _stored_session_context(state, session_id)
    resolved_provider, resolved_model = (
        (provider_name, model_id)
        if provider_name is not None and model_id is not None
        else (
            stored_info.model.split("/", 1)
            if stored_info is not None and "/" in stored_info.model
            else resolve_model(state.config)
        )
    )
    resolved_project_dir = project_dir or _session_project_dir(
        state,
        session_id,
        None,
        stored_info,
    )
    runtime = await bootstrap_runtime(
        state.config,
        resolved_provider,
        resolved_model,
        project_dir=resolved_project_dir,
        resolve_api_key=_resolve_api_key,
        include_extensions=True,
        runtime="server",
    )
    permission_callback = state.permission_callbacks.get(session_id)
    if permission_callback is None:
        permission_callback = _server_permission_callback(state, session_id)
    session = create_agent_session_from_bootstrap(
        state.config,
        runtime,
        project_dir=resolved_project_dir,
        store=state.store,
        session_id=session_id,
        permission_callback=permission_callback,
    )
    session.thinking_level = _session_thinking_level(
        state,
        session_id,
        None,
        stored_info,
    )  # type: ignore[assignment]
    messages_to_restore = prior_messages if prior_messages is not None else stored_messages
    if messages_to_restore:
        session.messages.extend(messages_to_restore)
    state.sessions[session_id] = session
    model_label = f"{resolved_provider}/{resolved_model}"
    await _initialize_session_state(
        state,
        session_id,
        model=model_label,
        project_dir=resolved_project_dir,
        thinking_level=_session_thinking_level(state, session_id, session, stored_info),
    )
    return session


async def _switch_server_session_model(
    state: ServerState,
    session_id: str,
    model_str: str,
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    if "/" not in model_str:
        raise RuntimeError("Format: provider/model-id")
    provider_name, model_id = model_str.split("/", 1)
    model_info = await get_effective_model_info(state.config, provider_name, model_id)
    if model_info is None:
        raise RuntimeError(
            f"Model '{model_id}' not found for provider '{provider_name}'."
        )

    previous = state.sessions.pop(session_id, None)
    stored_info, stored_messages = await _stored_session_context(state, session_id)
    prior_messages = previous.messages[1:] if previous is not None else stored_messages
    project_dir = _session_project_dir(state, session_id, previous, stored_info)
    if previous is not None:
        with suppress(Exception):
            await previous.provider.close()
    if previous is None and not prior_messages:
        await _initialize_session_state(
            state,
            session_id,
            model=model_str,
            project_dir=project_dir,
        )
        return await _serialize_session(state, session_id)

    await _create_server_session(
        state,
        session_id,
        provider_name=provider_name,
        model_id=model_id,
        project_dir=project_dir,
        prior_messages=prior_messages,
    )
    return await _serialize_session(state, session_id)


async def _resolve_session_project_dir(
    state: ServerState,
    session_id: str,
    project_dir: str,
) -> str:
    requested = project_dir.strip()
    if not requested:
        raise RuntimeError("Missing project_dir")

    stored_info, _ = await _stored_session_context(state, session_id)
    current_dir = _session_project_dir(
        state,
        session_id,
        state.sessions.get(session_id),
        stored_info,
    )
    expanded = Path(requested).expanduser()
    candidate = expanded if expanded.is_absolute() else Path(current_dir) / expanded
    resolved = candidate.resolve()
    if not resolved.exists():
        raise RuntimeError(f"Directory not found: {resolved}")
    if not resolved.is_dir():
        raise RuntimeError(f"Not a directory: {resolved}")
    return str(resolved)


async def _set_server_session_project(
    state: ServerState,
    session_id: str,
    project_dir: str,
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    resolved_project_dir = await _resolve_session_project_dir(state, session_id, project_dir)
    previous = state.sessions.pop(session_id, None)
    stored_info, stored_messages = await _stored_session_context(state, session_id)
    if previous is None and not stored_messages:
        model_label = _session_model_label(state, session_id, previous, stored_info)
        await _initialize_session_state(
            state,
            session_id,
            model=model_label,
            project_dir=resolved_project_dir,
        )
        return await _serialize_session(state, session_id)

    prior_messages = previous.messages[1:] if previous is not None else stored_messages
    model_label = _session_model_label(state, session_id, previous, stored_info)
    if "/" in model_label:
        provider_name, model_id = model_label.split("/", 1)
    else:
        provider_name, model_id = resolve_model(state.config)
    if previous is not None:
        with suppress(Exception):
            await previous.provider.close()
    await _create_server_session(
        state,
        session_id,
        provider_name=provider_name,
        model_id=model_id,
        project_dir=resolved_project_dir,
        prior_messages=prior_messages,
    )
    return await _serialize_session(state, session_id)


async def _set_server_session_thinking(
    state: ServerState,
    session_id: str,
    thinking_level: str,
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    valid = ("off", "minimal", "low", "medium", "high", "xhigh")
    level = thinking_level.strip().lower()
    if level not in valid:
        raise RuntimeError(f"Invalid thinking level: {thinking_level}")
    state.session_thinking_levels[session_id] = level
    session = state.sessions.get(session_id)
    if session is not None:
        session.thinking_level = level  # type: ignore[assignment]
    stored_info, _ = await _stored_session_context(state, session_id)
    await _persist_session_record(
        state,
        session_id,
        model=_session_model_label(state, session_id, session, stored_info),
        project_dir=_session_project_dir(state, session_id, session, stored_info),
        thinking_level=level,
    )
    return await _serialize_session(state, session_id)


async def _rename_server_session(
    state: ServerState,
    session_id: str,
    title: str,
) -> dict[str, Any]:
    if not title.strip():
        raise RuntimeError("Missing title")
    model_label = _session_model_label(state, session_id, state.sessions.get(session_id))
    project_dir = _session_project_dir(state, session_id, state.sessions.get(session_id))
    await _initialize_session_state(
        state,
        session_id,
        model=model_label,
        project_dir=project_dir,
    )
    if state.store is None:
        raise RuntimeError("Session store not available")
    await state.store.rename_session(session_id, title.strip())
    return await _serialize_session(state, session_id)


async def _session_tree_nodes(
    state: ServerState,
    session_id: str,
) -> list[dict[str, Any]]:
    if state.store is None:
        raise RuntimeError("Session store not available")
    return await state.store.get_message_nodes(session_id)


async def _compact_server_session(
    state: ServerState,
    session_id: str,
    custom_prompt: str = "",
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    session = state.sessions.get(session_id)
    if session is None:
        session = await _create_server_session(state, session_id)
    summary = await session.compact(custom_prompt)
    return {
        "summary": summary,
        "session": await _serialize_session(state, session_id),
    }


async def _fork_server_session(
    state: ServerState,
    session_id: str,
    up_to_message_idx: int | None = None,
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    if state.store is None:
        raise RuntimeError("Session store not available")
    new_session_id = str(uuid.uuid4())
    await state.store.fork_session(
        session_id,
        new_session_id,
        up_to_message_idx=up_to_message_idx,
    )
    info = await state.store.get_session(new_session_id)
    if info is not None:
        state.session_provider_models[new_session_id] = info.model
        state.session_projects[new_session_id] = info.project_dir
        if info.thinking_level:
            state.session_thinking_levels[new_session_id] = info.thinking_level
    return {
        "session_id": new_session_id,
        "session": await _serialize_session(state, new_session_id, info),
    }


async def _inject_skill_into_session(
    state: ServerState,
    session_id: str,
    skill_name: str,
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    session = state.sessions.get(session_id)
    if session is None:
        session = await _create_server_session(state, session_id)

    from worker_core.skills import inject_skill, load_skills

    project_dir = _session_project_dir(state, session_id, session)
    skills = load_skills(project_dir)
    skill = skills.get(skill_name)
    if skill is None:
        raise RuntimeError(f"Skill '{skill_name}' not found")
    session.system_prompt = inject_skill(session.system_prompt, skill)
    session.messages[0].content = session.system_prompt
    return await _serialize_session(state, session_id)


async def _reload_server_session_runtime(
    state: ServerState,
    session_id: str,
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    import importlib

    previous = state.sessions.pop(session_id, None)
    stored_info, stored_messages = await _stored_session_context(state, session_id)
    prior_messages = previous.messages[1:] if previous is not None else stored_messages
    model_label = _session_model_label(state, session_id, previous, stored_info)
    project_dir = _session_project_dir(state, session_id, previous, stored_info)
    thinking_level = _session_thinking_level(state, session_id, previous, stored_info)
    importlib.invalidate_caches()
    if previous is not None:
        with suppress(Exception):
            await previous.provider.close()
    if "/" in model_label:
        provider_name, model_id = model_label.split("/", 1)
    else:
        provider_name, model_id = resolve_model(state.config)
    state.session_thinking_levels[session_id] = thinking_level
    await _create_server_session(
        state,
        session_id,
        provider_name=provider_name,
        model_id=model_id,
        project_dir=project_dir,
        prior_messages=prior_messages,
    )
    return await _serialize_session(state, session_id)


def _extract_persistent_cd_target(command: str) -> str | None:
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not parts or parts[0] != "cd":
        return None
    if len(parts) == 1:
        return "~"
    if len(parts) == 2:
        return parts[1]
    return None


async def _run_server_shell(command: str, *, cwd: str) -> tuple[str, int]:
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode(errors="replace").rstrip()
    if len(output) > 5000:
        output = output[:5000] + f"\n... (truncated, {len(stdout)} bytes total)"
    return output, proc.returncode


async def handle_client(ws: ServerConnection, state: ServerState) -> None:
    """Handle a single WebSocket client connection."""
    logger.info("Client connected: %s", ws.remote_address)
    stream_tasks: set[asyncio.Task[None]] = set()
    ws._worker_stream_tasks = stream_tasks

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
                session_id = str(msg.get("session_id", "")).strip()
                if not session_id:
                    await ws.send(json.dumps({"type": "error", "error": "Missing session_id"}))
                    continue
                session = state.sessions.get(session_id)
                if session is not None:
                    session.abort()
            elif msg_type == "approve_tool":
                await _handle_tool_approval(ws, msg, state)
            else:
                await ws.send(json.dumps({"type": "error", "error": f"Unknown type: {msg_type}"}))

    except websockets.exceptions.ConnectionClosed:
        logger.info("Client disconnected: %s", ws.remote_address)
    finally:
        stream_tasks = getattr(ws, "_worker_stream_tasks", stream_tasks)
        for task in list(stream_tasks):
            task.cancel()
        for task in list(stream_tasks):
            with suppress(asyncio.CancelledError, Exception):
                await task
        for _request_id, (session_id, future) in list(state.pending_permissions.items()):
            controller = state.session_controllers.get(session_id)
            if future.done():
                continue
            if controller is None or not controller.has_subscribers:
                future.set_result((False, False))


async def _handle_message(ws: ServerConnection, msg: dict[str, Any], state: ServerState) -> None:
    """Start a user message run and stream it to this websocket subscriber."""
    session_id = str(msg.get("session_id", "default")).strip() or "default"
    content = str(msg.get("content", ""))

    if not content:
        await ws.send(json.dumps({"type": "error", "error": "Empty message"}))
        return

    session_exists = (
        session_id in state.sessions
        or session_id in state.session_provider_models
        or session_id in state.session_projects
        or (state.store is not None and await state.store.get_session(session_id) is not None)
    )
    if not session_exists and len(state.sessions) >= state.config.server.max_sessions:
        await ws.send(
            json.dumps(
                {
                    "type": "error",
                    "error": f"Maximum sessions reached ({state.config.server.max_sessions})",
                }
            )
        )
        return

    controller = _get_session_controller(state, session_id)
    queue = controller.subscribe()
    try:
        await controller.start(content)
    except Exception as exc:
        controller.unsubscribe(queue)
        await ws.send(json.dumps({"type": "error", "error": str(exc)}))
        return

    async def _stream_events() -> None:
        try:
            while True:
                payload = await queue.get()
                await ws.send(json.dumps(payload))
                if payload.get("type") in {"done", "error"}:
                    break
        finally:
            controller.unsubscribe(queue)

    task = asyncio.create_task(_stream_events(), name=f"worker-ws-stream-{session_id}")
    stream_tasks = getattr(ws, "_worker_stream_tasks", None)
    if stream_tasks is None:
        stream_tasks = set()
        ws._worker_stream_tasks = stream_tasks
    stream_tasks.add(task)
    task.add_done_callback(lambda done: stream_tasks.discard(done))


async def _handle_tool_approval(
    ws: ServerConnection,
    msg: dict[str, Any],
    state: ServerState,
) -> None:
    request_id = str(msg.get("request_id", "")).strip()
    decision = str(msg.get("decision", "")).strip().lower()
    if not request_id:
        await ws.send(json.dumps({"type": "error", "error": "Missing request_id"}))
        return
    pending = state.pending_permissions.get(request_id)
    if pending is None or pending[1].done():
        await ws.send(json.dumps({"type": "error", "error": "Permission request not found"}))
        return
    if decision not in {"once", "all", "deny"}:
        await ws.send(json.dumps({"type": "error", "error": "Invalid permission decision"}))
        return
    pending[1].set_result((decision in {"once", "all"}, decision == "all"))


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
    async def handle_server_info(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "project_dir": state.default_project_dir,
                "sessions_db": state.config.sessions.db_path,
                "default_model": state.config.agent.model,
            }
        )
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
        sessions_info = await _list_serialized_sessions(state)
        return web.json_response({"sessions": sessions_info})
    async def handle_session_get(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        return web.json_response({"session": await _serialize_session(state, sid)})

    async def handle_session_messages_get(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            messages = await _session_history_messages(state, sid)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=404)
        return web.json_response(
            {"messages": [_serialize_message(message) for message in messages]}
        )
    async def handle_session_tree_get(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            nodes = await _session_tree_nodes(state, sid)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=404)
        return web.json_response({"nodes": nodes})

    async def handle_session_commands_get(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            commands = await _session_extension_commands(state, sid)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"commands": commands})

    async def handle_session_command_post(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        command_name = request.match_info["command_name"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        arg = str(payload.get("arg", ""))
        try:
            output, session = await _run_session_extension_command(
                state,
                sid,
                command_name,
                arg,
            )
        except RuntimeError as exc:
            status = 404 if str(exc).startswith("Unknown command: ") else 400
            return web.json_response({"error": str(exc)}, status=status)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(
            {
                "command": command_name,
                "output": output,
                "session": session,
            }
        )

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
    async def handle_session_title_put(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        title = str(payload.get("title", "")).strip()
        if not title:
            return web.json_response({"error": "Missing title"}, status=400)
        try:
            session = await _rename_server_session(state, sid, title)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"session": session})
    async def handle_session_project_put(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        project_dir = str(payload.get("project_dir", "")).strip()
        if not project_dir:
            return web.json_response({"error": "Missing project_dir"}, status=400)
        try:
            session = await _set_server_session_project(state, sid, project_dir)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"session": session})

    async def handle_session_thinking_put(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        thinking_level = str(payload.get("thinking_level", "")).strip()
        if not thinking_level:
            return web.json_response({"error": "Missing thinking_level"}, status=400)
        try:
            session = await _set_server_session_thinking(state, sid, thinking_level)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"session": session})
    async def handle_session_compact_post(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        prompt = str(payload.get("prompt", ""))
        try:
            result = await _compact_server_session(state, sid, prompt)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(result)
    async def handle_session_fork_post(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        message_index = payload.get("message_index")
        if message_index is not None and not isinstance(message_index, int):
            return web.json_response({"error": "Invalid message_index"}, status=400)
        try:
            result = await _fork_server_session(state, sid, message_index)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(result)
    async def handle_session_skill_post(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        skill_name = str(payload.get("skill", "")).strip()
        if not skill_name:
            return web.json_response({"error": "Missing skill"}, status=400)
        try:
            session = await _inject_skill_into_session(state, sid, skill_name)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"session": session})
    async def handle_session_reload_post(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            session = await _reload_server_session_runtime(state, sid)
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
        cd_target = _extract_persistent_cd_target(command)
        if cd_target is not None:
            try:
                session = await _set_server_session_project(
                    state,
                    request.match_info["session_id"],
                    cd_target,
                )
            except Exception as exc:
                return web.json_response({"error": str(exc)}, status=400)
            return web.json_response(
                {
                    "command": command,
                    "output": str(session.get("project_dir", "")),
                    "exit_code": 0,
                    "session": session,
                }
            )

        stored_info, _ = await _stored_session_context(state, request.match_info["session_id"])
        cwd = _session_project_dir(
            state,
            request.match_info["session_id"],
            state.sessions.get(request.match_info["session_id"]),
            stored_info,
        )
        output, exit_code = await _run_server_shell(command, cwd=cwd)
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
        deleted = False
        controller = state.session_controllers.pop(sid, None)
        if controller is not None:
            with suppress(Exception):
                await controller.abort()
        state.auto_approve_sessions.discard(sid)
        state.permission_callbacks.pop(sid, None)
        if sid in state.sessions:
            session = state.sessions.pop(sid)
            state.session_provider_models.pop(sid, None)
            state.session_projects.pop(sid, None)
            state.session_thinking_levels.pop(sid, None)
            with suppress(Exception):
                await session.provider.close()
            deleted = True
        else:
            state.session_provider_models.pop(sid, None)
            state.session_projects.pop(sid, None)
            state.session_thinking_levels.pop(sid, None)
        if state.store is not None and await state.store.get_session(sid) is not None:
            await state.store.delete_session(sid)
            deleted = True
        if deleted:
            return web.json_response({"deleted": sid})
        return web.json_response({"error": "Session not found"}, status=404)

    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/server/info", handle_server_info)
    app.router.add_get("/api/providers", handle_providers)
    app.router.add_get("/api/models", handle_models)
    app.router.add_post("/api/credentials/import", handle_credentials_import)
    app.router.add_post("/api/oauth/start", handle_oauth_start)
    app.router.add_post("/api/oauth/complete", handle_oauth_complete)
    app.router.add_get("/api/sessions", handle_sessions_list)
    app.router.add_get("/api/sessions/{session_id}", handle_session_get)
    app.router.add_get("/api/sessions/{session_id}/messages", handle_session_messages_get)
    app.router.add_get("/api/sessions/{session_id}/tree", handle_session_tree_get)
    app.router.add_get("/api/sessions/{session_id}/commands", handle_session_commands_get)
    app.router.add_post(
        "/api/sessions/{session_id}/commands/{command_name}",
        handle_session_command_post,
    )
    app.router.add_put("/api/sessions/{session_id}/model", handle_session_model_put)
    app.router.add_put("/api/sessions/{session_id}/title", handle_session_title_put)
    app.router.add_put("/api/sessions/{session_id}/project", handle_session_project_put)
    app.router.add_put("/api/sessions/{session_id}/thinking", handle_session_thinking_put)
    app.router.add_post("/api/sessions/{session_id}/compact", handle_session_compact_post)
    app.router.add_post("/api/sessions/{session_id}/fork", handle_session_fork_post)
    app.router.add_post("/api/sessions/{session_id}/skill", handle_session_skill_post)
    app.router.add_post("/api/sessions/{session_id}/reload", handle_session_reload_post)
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
    store = SessionStore(config.sessions.db_path)
    await store.open()
    state = ServerState(
        config=config,
        default_project_dir=project_dir,
        provider_overlay=provider_overlay,
        server_extensions=server_extensions,
        store=store,
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

    try:
        async with websockets.serve(ws_handler, host, port):  # type: ignore[attr-defined]
            await asyncio.Future()  # Run forever
    finally:
        for session in list(state.sessions.values()):
            with suppress(Exception):
                await session.provider.close()
        await rest_runner.cleanup()
        await store.close()
