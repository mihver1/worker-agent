"""ACP stdio agent entrypoint for Artel."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from worker_core.config import load_config
from worker_core.sessions import SessionStore

from worker_server import server as server_mod
from worker_server.provider_overlay import load_provider_overlay, merge_provider_overlay

_THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh")


@dataclass(slots=True)
class _AcpSessionRuntime:
    allow_all: bool = False
    pending_tool_calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    prompt_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    permission_broker: Any | None = None
    tracker: Any | None = None


def _resolve_cwd(default_project_dir: str, cwd: str | None) -> str:
    requested = (cwd or "").strip()
    base = Path(default_project_dir).resolve()
    if not requested:
        return str(base)
    candidate = Path(requested).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return str(candidate.resolve())


def _workspace_matches(session_dir: str, requested_dir: str) -> bool:
    session_path = Path(session_dir).resolve()
    requested_path = Path(requested_dir).resolve()
    return (
        session_path == requested_path
        or session_path in requested_path.parents
        or requested_path in session_path.parents
    )


def _iso_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return value
    return parsed.isoformat().replace("+00:00", "Z")


def _extract_block_text(block: Any) -> str:
    text = getattr(block, "text", None)
    if isinstance(text, str) and text.strip():
        return text

    resource = getattr(block, "resource", None)
    if resource is not None:
        resource_text = getattr(resource, "text", None)
        if isinstance(resource_text, str) and resource_text.strip():
            return resource_text
        contents = getattr(resource, "contents", None)
        contents_text = getattr(contents, "text", None)
        if isinstance(contents_text, str) and contents_text.strip():
            return contents_text
        uri = getattr(resource, "uri", None)
        if isinstance(uri, str) and uri.strip():
            return f"[resource] {uri}"

    uri = getattr(block, "uri", None)
    if isinstance(uri, str) and uri.strip():
        return f"[resource] {uri}"
    return ""


def _prompt_to_text(prompt: list[Any]) -> str:
    parts = [
        part.strip() for part in (_extract_block_text(block) for block in prompt) if part.strip()
    ]
    return "\n\n".join(parts)


def _text_chunks(text: str, *, chunk_size: int = 4096) -> list[str]:
    if not text:
        return []
    return [text[index : index + chunk_size] for index in range(0, len(text), chunk_size)]


def _tool_title(tool_name: str, args: dict[str, Any]) -> str:
    if not args:
        return tool_name
    rendered_args = ", ".join(f"{key}={value!r}" for key, value in args.items())
    return f"{tool_name}({rendered_args})"


def _selected_permission_option(outcome: Any) -> str | None:
    if isinstance(outcome, dict):
        if outcome.get("outcome") == "selected":
            return str(outcome.get("optionId", "")).strip() or None
        return None
    if getattr(outcome, "outcome", None) != "selected":
        return None
    option_id = getattr(outcome, "option_id", None)
    if isinstance(option_id, str) and option_id.strip():
        return option_id
    option_id = getattr(outcome, "optionId", None)
    if isinstance(option_id, str) and option_id.strip():
        return option_id
    return None


def _acp_usage_payload(usage: Any) -> Any:
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    thought_tokens = int(getattr(usage, "reasoning_tokens", 0) or 0)
    cached_read_tokens = int(getattr(usage, "cache_read_tokens", 0) or 0)
    cached_write_tokens = int(getattr(usage, "cache_write_tokens", 0) or 0)
    total_tokens = (
        input_tokens + output_tokens + thought_tokens + cached_read_tokens + cached_write_tokens
    )
    from acp.schema import Usage as AcpUsage

    payload: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
    if thought_tokens:
        payload["thought_tokens"] = thought_tokens
    if cached_read_tokens:
        payload["cached_read_tokens"] = cached_read_tokens
    if cached_write_tokens:
        payload["cached_write_tokens"] = cached_write_tokens
    return AcpUsage(**payload)


async def _create_state(project_dir: str | None = None) -> server_mod.ServerState:
    resolved_project_dir = str(Path(project_dir or Path.cwd()).resolve())
    config = load_config(resolved_project_dir)
    provider_overlay = load_provider_overlay()
    merge_provider_overlay(config, provider_overlay)
    store = SessionStore(config.sessions.db_path)
    await store.open()
    return server_mod.ServerState(
        config=config,
        default_project_dir=resolved_project_dir,
        provider_overlay=provider_overlay,
        store=store,
    )


async def _close_state(state: server_mod.ServerState) -> None:
    for session in list(state.sessions.values()):
        with server_mod.suppress(Exception):
            await session.provider.close()
        mcp_runtime = getattr(session, "mcp_runtime", None)
        if mcp_runtime is not None:
            with server_mod.suppress(Exception):
                await mcp_runtime.close()
        lsp_runtime = getattr(session, "lsp_runtime", None)
        if lsp_runtime is not None:
            with server_mod.suppress(Exception):
                await lsp_runtime.close()
    if state.mcp_runtime is not None:
        with server_mod.suppress(Exception):
            await state.mcp_runtime.close()
    if state.store is not None:
        with server_mod.suppress(Exception):
            await state.store.close()


async def run_acp() -> None:
    """Run the Artel ACP agent on stdin/stdout."""

    try:
        from acp import (
            Agent,
            run_agent,
            text_block,
            tool_content,
            update_agent_message_text,
            update_agent_thought_text,
            update_user_message_text,
        )
        from acp.contrib.permissions import PermissionBroker
        from acp.contrib.tool_calls import ToolCallTracker
        from acp.interfaces import Client
        from acp.schema import (
            AgentCapabilities,
            AuthenticateResponse,
            ConfigOptionUpdate,
            CurrentModeUpdate,
            Implementation,
            InitializeResponse,
            ListSessionsResponse,
            LoadSessionResponse,
            ModelInfo,
            NewSessionResponse,
            PromptCapabilities,
            PromptResponse,
            SessionCapabilities,
            SessionConfigOption,
            SessionConfigOptionSelect,
            SessionConfigSelectOption,
            SessionForkCapabilities,
            SessionInfo,
            SessionInfoUpdate,
            SessionListCapabilities,
            SessionMode,
            SessionModelState,
            SessionModeState,
            SessionResumeCapabilities,
            SetSessionConfigOptionResponse,
            SetSessionModelResponse,
            SetSessionModeResponse,
            ToolCallLocation,
            UsageUpdate,
        )
    except ImportError as exc:
        raise RuntimeError(
            "ACP support requires the 'agent-client-protocol' package. Run 'uv sync' first."
        ) from exc

    state = await _create_state()

    class WorkerAcpAgent(Agent):
        _conn: Client

        def __init__(self) -> None:
            self._session_runtimes: dict[str, _AcpSessionRuntime] = {}
            self._available_models_cache: list[ModelInfo] | None = None

        def on_connect(self, conn: Client) -> None:
            self._conn = conn

        def _runtime(self, session_id: str) -> _AcpSessionRuntime:
            runtime = self._session_runtimes.get(session_id)
            if runtime is None:
                runtime = _AcpSessionRuntime()
                self._session_runtimes[session_id] = runtime

                async def _callback(tool_name: str, args: dict[str, Any]) -> bool:
                    if runtime.allow_all:
                        return True
                    if runtime.permission_broker is None:
                        return False
                    tool_call_id = self._match_pending_tool_call(runtime, tool_name, args)
                    if tool_call_id is None:
                        return False
                    response = await runtime.permission_broker.request_for(tool_call_id)
                    selected = _selected_permission_option(response.outcome)
                    if selected == "approve_for_session":
                        runtime.allow_all = True
                    approved = selected in {"approve", "approve_for_session"}
                    if approved and runtime.tracker is not None:
                        update = runtime.tracker.progress(tool_call_id, status="in_progress")
                        await self._conn.session_update(session_id=session_id, update=update)
                    return approved

                state.permission_callbacks[session_id] = _callback
            return runtime

        def _match_pending_tool_call(
            self,
            runtime: _AcpSessionRuntime,
            tool_name: str,
            args: dict[str, Any],
        ) -> str | None:
            for index, (
                tool_call_id,
                pending_name,
                pending_args,
            ) in enumerate(runtime.pending_tool_calls):
                if pending_name == tool_name and pending_args == args:
                    runtime.pending_tool_calls.pop(index)
                    return tool_call_id
            if runtime.pending_tool_calls:
                tool_call_id, _, _ = runtime.pending_tool_calls.pop(0)
                return tool_call_id
            return None

        async def _available_models(self) -> list[ModelInfo]:
            if self._available_models_cache is not None:
                return self._available_models_cache
            from worker_core.cli import _resolve_api_key

            catalog = await server_mod.get_effective_provider_catalog(state.config)
            available_models: list[ModelInfo] = []
            for provider_id, provider in catalog.items():
                requires_key = server_mod.provider_requires_api_key(state.config, provider_id)
                api_key, _ = await _resolve_api_key(state.config, provider_id)
                if not api_key and requires_key:
                    continue
                for model in provider.models:
                    description = provider.name
                    if model.context_window:
                        description = f"{provider.name} · {model.context_window // 1000}k ctx"
                    available_models.append(
                        ModelInfo(
                            model_id=f"{provider_id}/{model.id}",
                            name=model.name,
                            description=description,
                        )
                    )
            self._available_models_cache = available_models
            return available_models

        async def _session_model_state(self, session_id: str) -> SessionModelState:
            session = await server_mod._serialize_session(state, session_id)
            return SessionModelState(
                available_models=await self._available_models(),
                current_model_id=str(session.get("model", "")).strip() or state.config.agent.model,
            )

        def _session_mode_state(self, session_id: str) -> SessionModeState:
            runtime = self._runtime(session_id)
            return SessionModeState(
                available_modes=[
                    SessionMode(
                        id="ask",
                        name="Ask",
                        description="Request permission before protected tool calls.",
                    ),
                    SessionMode(
                        id="code",
                        name="Code",
                        description="Automatically approve protected tool calls for this session.",
                    ),
                ],
                current_mode_id="code" if runtime.allow_all else "ask",
            )

        async def _session_config_options(self, session_id: str) -> list[SessionConfigOption]:
            session = await server_mod._serialize_session(state, session_id)
            model_state = await self._session_model_state(session_id)
            thinking_level = (
                str(session.get("thinking_level", "")).strip() or state.config.agent.thinking
            )
            return [
                SessionConfigOption(
                    root=SessionConfigOptionSelect(
                        id="mode",
                        name="Session Mode",
                        category="mode",
                        description="Controls whether protected tools ask for approval.",
                        type="select",
                        current_value=self._session_mode_state(session_id).current_mode_id,
                        options=[
                            SessionConfigSelectOption(
                                value="ask",
                                name="Ask",
                                description="Request permission before protected tool calls.",
                            ),
                            SessionConfigSelectOption(
                                value="code",
                                name="Code",
                                description=(
                                    "Automatically approve protected tool calls for this session."
                                ),
                            ),
                        ],
                    )
                ),
                SessionConfigOption(
                    root=SessionConfigOptionSelect(
                        id="model",
                        name="Model",
                        category="model",
                        description="The model used for this session.",
                        type="select",
                        current_value=model_state.current_model_id,
                        options=[
                            SessionConfigSelectOption(
                                value=model.model_id,
                                name=model.name,
                                description=model.description,
                            )
                            for model in model_state.available_models
                        ],
                    )
                ),
                SessionConfigOption(
                    root=SessionConfigOptionSelect(
                        id="thinking",
                        name="Thinking",
                        category="reasoning",
                        description="Controls the reasoning budget for this session.",
                        type="select",
                        current_value=thinking_level,
                        options=[
                            SessionConfigSelectOption(value=level, name=level, description=None)
                            for level in _THINKING_LEVELS
                        ],
                    )
                ),
            ]

        async def _session_response_payload(self, session_id: str) -> dict[str, Any]:
            return {
                "config_options": await self._session_config_options(session_id),
                "models": await self._session_model_state(session_id),
                "modes": self._session_mode_state(session_id),
            }

        async def _replay_session_history(self, session_id: str) -> None:
            from worker_ai.models import Role as ArtelRole

            messages = await server_mod._session_history_messages(state, session_id)
            for message in messages:
                if message.role == ArtelRole.USER:
                    for chunk in _text_chunks(message.content):
                        await self._conn.session_update(
                            session_id=session_id,
                            update=update_user_message_text(chunk),
                        )
                elif message.role == ArtelRole.ASSISTANT and message.content:
                    for chunk in _text_chunks(message.reasoning or ""):
                        await self._conn.session_update(
                            session_id=session_id,
                            update=update_agent_thought_text(chunk),
                        )
                    for chunk in _text_chunks(message.content):
                        await self._conn.session_update(
                            session_id=session_id,
                            update=update_agent_message_text(chunk),
                        )

        async def _ensure_session_known(
            self,
            session_id: str,
            *,
            cwd: str | None = None,
            create_if_missing: bool = False,
        ) -> dict[str, Any]:
            self._runtime(session_id)
            serialized = await server_mod._serialize_session(state, session_id)
            if not serialized.get("exists"):
                if not create_if_missing:
                    raise RuntimeError(f"Session not found: {session_id}")
                project_dir = _resolve_cwd(state.default_project_dir, cwd)
                await server_mod._initialize_session_state(
                    state,
                    session_id,
                    model=state.config.agent.model,
                    project_dir=project_dir,
                    thinking_level=state.config.agent.thinking,
                )
                serialized = await server_mod._serialize_session(state, session_id)
            return serialized

        async def initialize(
            self,
            protocol_version: int,
            client_capabilities: Any | None = None,
            client_info: Implementation | None = None,
            **kwargs: Any,
        ) -> InitializeResponse:
            del client_capabilities, client_info, kwargs
            return InitializeResponse(
                protocol_version=protocol_version,
                agent_capabilities=AgentCapabilities(
                    load_session=True,
                    prompt_capabilities=PromptCapabilities(embedded_context=True),
                    session_capabilities=SessionCapabilities(
                        fork=SessionForkCapabilities(),
                        list=SessionListCapabilities(),
                        resume=SessionResumeCapabilities(),
                    ),
                ),
                agent_info=Implementation(
                    name="artel",
                    title="Artel ACP",
                    version="0.1.0",
                ),
            )

        async def authenticate(self, method_id: str, **kwargs: Any) -> AuthenticateResponse | None:
            del method_id, kwargs
            return AuthenticateResponse()

        async def new_session(
            self,
            cwd: str,
            mcp_servers: list[Any] | None = None,
            **kwargs: Any,
        ) -> NewSessionResponse:
            del mcp_servers, kwargs
            session_id = uuid.uuid4().hex
            project_dir = _resolve_cwd(state.default_project_dir, cwd)
            self._runtime(session_id)
            await server_mod._initialize_session_state(
                state,
                session_id,
                model=state.config.agent.model,
                project_dir=project_dir,
                thinking_level=state.config.agent.thinking,
            )
            return NewSessionResponse(
                session_id=session_id,
                **(await self._session_response_payload(session_id)),
            )

        async def load_session(
            self,
            cwd: str,
            session_id: str,
            mcp_servers: list[Any] | None = None,
            **kwargs: Any,
        ) -> LoadSessionResponse | None:
            del mcp_servers, kwargs
            requested_dir = _resolve_cwd(state.default_project_dir, cwd)
            serialized = await self._ensure_session_known(session_id)
            project_dir = str(serialized.get("project_dir", "")).strip()
            if project_dir and not _workspace_matches(project_dir, requested_dir):
                return None
            await self._replay_session_history(session_id)
            return LoadSessionResponse(**(await self._session_response_payload(session_id)))

        async def list_sessions(
            self,
            cursor: str | None = None,
            cwd: str | None = None,
            **kwargs: Any,
        ) -> ListSessionsResponse:
            del kwargs
            requested_dir = _resolve_cwd(state.default_project_dir, cwd)
            serialized_sessions = await server_mod._list_serialized_sessions(state, limit=500)
            filtered: list[SessionInfo] = []
            for session in serialized_sessions:
                project_dir = str(session.get("project_dir", "")).strip()
                if not project_dir or not _workspace_matches(project_dir, requested_dir):
                    continue
                filtered.append(
                    SessionInfo(
                        session_id=str(session.get("id", "")),
                        cwd=project_dir,
                        title=str(session.get("title", "")).strip() or None,
                        updated_at=_iso_timestamp(
                            str(session.get("updated_at", "")).strip() or None
                        ),
                    )
                )
            offset = int(cursor) if cursor and cursor.isdigit() else 0
            page_size = 100
            page = filtered[offset : offset + page_size]
            next_cursor = str(offset + page_size) if offset + page_size < len(filtered) else None
            return ListSessionsResponse(sessions=page, next_cursor=next_cursor)

        async def set_session_mode(
            self,
            mode_id: str,
            session_id: str,
            **kwargs: Any,
        ) -> SetSessionModeResponse | None:
            del kwargs
            runtime = self._runtime(session_id)
            if mode_id not in {"ask", "code"}:
                raise RuntimeError(f"Unsupported session mode: {mode_id}")
            runtime.allow_all = mode_id == "code"
            await self._conn.session_update(
                session_id=session_id,
                update=CurrentModeUpdate(
                    session_update="current_mode_update",
                    current_mode_id=mode_id,
                ),
            )
            await self._conn.session_update(
                session_id=session_id,
                update=ConfigOptionUpdate(
                    session_update="config_option_update",
                    config_options=await self._session_config_options(session_id),
                ),
            )
            return SetSessionModeResponse()

        async def set_session_model(
            self,
            model_id: str,
            session_id: str,
            **kwargs: Any,
        ) -> SetSessionModelResponse | None:
            del kwargs
            await self._ensure_session_known(session_id)
            await server_mod._switch_server_session_model(state, session_id, model_id)
            await self._conn.session_update(
                session_id=session_id,
                update=ConfigOptionUpdate(
                    session_update="config_option_update",
                    config_options=await self._session_config_options(session_id),
                ),
            )
            return SetSessionModelResponse()

        async def set_config_option(
            self,
            config_id: str,
            session_id: str,
            value: str,
            **kwargs: Any,
        ) -> SetSessionConfigOptionResponse | None:
            del kwargs
            await self._ensure_session_known(session_id)
            if config_id == "mode":
                await self.set_session_mode(value, session_id)
            elif config_id == "model":
                await self.set_session_model(value, session_id)
            elif config_id == "thinking":
                await server_mod._set_server_session_thinking(state, session_id, value)
                await self._conn.session_update(
                    session_id=session_id,
                    update=ConfigOptionUpdate(
                        session_update="config_option_update",
                        config_options=await self._session_config_options(session_id),
                    ),
                )
            else:
                raise RuntimeError(f"Unknown config option: {config_id}")
            return SetSessionConfigOptionResponse(
                config_options=await self._session_config_options(session_id),
            )

        async def prompt(
            self,
            prompt: list[Any],
            session_id: str,
            **kwargs: Any,
        ) -> PromptResponse:
            del kwargs
            serialized = await self._ensure_session_known(session_id, create_if_missing=False)
            runtime = self._runtime(session_id)
            content = _prompt_to_text(prompt)
            if not content.strip():
                return PromptResponse(stop_reason="end_turn")

            session = state.sessions.get(session_id)
            if session is None:
                session = await server_mod._create_server_session(state, session_id)

            async with runtime.prompt_lock:
                tracker = ToolCallTracker()

                async def _request_permission(request: Any) -> Any:
                    kwargs = {
                        "session_id": request.session_id,
                        "tool_call": request.tool_call,
                        "options": request.options,
                    }
                    field_meta = getattr(request, "field_meta", None)
                    if isinstance(field_meta, dict):
                        kwargs.update(field_meta)
                    return await self._conn.request_permission(**kwargs)

                broker = PermissionBroker(
                    session_id=session_id,
                    requester=_request_permission,
                    tracker=tracker,
                )
                runtime.tracker = tracker
                runtime.permission_broker = broker
                runtime.pending_tool_calls.clear()

                stop_reason = "end_turn"
                final_usage = None
                initial_title = str(serialized.get("title", "")).strip()

                try:
                    async for event in session.run(content):
                        if event.type == server_mod.AgentEventType.TEXT_DELTA:
                            await self._conn.session_update(
                                session_id=session_id,
                                update=update_agent_message_text(event.content),
                            )
                        elif event.type == server_mod.AgentEventType.REASONING_DELTA:
                            await self._conn.session_update(
                                session_id=session_id,
                                update=update_agent_thought_text(event.content),
                            )
                        elif event.type == server_mod.AgentEventType.TOOL_CALL:
                            runtime.pending_tool_calls.append(
                                (event.tool_call_id, event.tool_name, dict(event.tool_args))
                            )
                            raw_locations = (
                                server_mod._tool_locations(
                                    event.tool_name,
                                    event.tool_args,
                                    cwd=session.project_dir,
                                )
                                or []
                            )
                            locations = [
                                ToolCallLocation(
                                    path=str(location["path"]),
                                    line=location.get("line"),
                                )
                                for location in raw_locations
                                if isinstance(location.get("path"), str)
                            ]
                            await self._conn.session_update(
                                session_id=session_id,
                                update=tracker.start(
                                    event.tool_call_id,
                                    title=_tool_title(event.tool_name, event.tool_args),
                                    kind=server_mod._tool_kind_for_name(event.tool_name),
                                    status="pending",
                                    raw_input=event.tool_args,
                                    locations=locations or None,
                                ),
                            )
                        elif event.type == server_mod.AgentEventType.TOOL_RESULT:
                            await self._conn.session_update(
                                session_id=session_id,
                                update=tracker.progress(
                                    event.tool_call_id,
                                    status="failed" if event.is_error else "completed",
                                    content=[tool_content(text_block(event.content))],
                                    raw_output={
                                        "output": event.content,
                                        "is_error": event.is_error,
                                        "display": event.display,
                                    },
                                ),
                            )
                            runtime.pending_tool_calls = [
                                pending
                                for pending in runtime.pending_tool_calls
                                if pending[0] != event.tool_call_id
                            ]
                            tracker.forget(event.tool_call_id)
                        elif event.type == server_mod.AgentEventType.ERROR:
                            if event.error == "Aborted.":
                                stop_reason = "cancelled"
                            await self._conn.session_update(
                                session_id=session_id,
                                update=update_agent_message_text(f"Error: {event.error}"),
                            )
                        elif event.type == server_mod.AgentEventType.COMPACT:
                            await self._conn.session_update(
                                session_id=session_id,
                                update=update_agent_message_text("Session auto-compacted."),
                            )
                        elif event.type == server_mod.AgentEventType.DONE:
                            final_usage = event.usage
                finally:
                    runtime.permission_broker = None
                    runtime.tracker = None
                    runtime.pending_tool_calls.clear()

                session_info = await server_mod._serialize_session(state, session_id)
                updated_title = str(session_info.get("title", "")).strip()
                if not updated_title and state.store is not None:
                    try:
                        generated_title = (await session.generate_title(content)).strip()
                    except Exception:
                        generated_title = ""
                    if generated_title:
                        await state.store.rename_session(session_id, generated_title)
                        updated_serialized = await server_mod._serialize_session(state, session_id)
                        await self._conn.session_update(
                            session_id=session_id,
                            update=SessionInfoUpdate(
                                session_update="session_info_update",
                                title=generated_title,
                                updated_at=_iso_timestamp(
                                    str(updated_serialized.get("updated_at", "")).strip() or None
                                ),
                            ),
                        )
                        updated_title = generated_title
                elif updated_title and updated_title != initial_title:
                    await self._conn.session_update(
                        session_id=session_id,
                        update=SessionInfoUpdate(
                            session_update="session_info_update",
                            title=updated_title,
                            updated_at=_iso_timestamp(
                                str(session_info.get("updated_at", "")).strip() or None
                            ),
                        ),
                    )

                if final_usage is not None:
                    context_size = max(session.context_window, session._estimate_tokens())
                    await self._conn.session_update(
                        session_id=session_id,
                        update=UsageUpdate(
                            session_update="usage_update",
                            used=session._estimate_tokens(),
                            size=context_size,
                        ),
                    )
                    return PromptResponse(
                        stop_reason=stop_reason,
                        usage=_acp_usage_payload(final_usage),
                    )
                return PromptResponse(stop_reason=stop_reason)

        async def fork_session(
            self,
            cwd: str,
            session_id: str,
            mcp_servers: list[Any] | None = None,
            **kwargs: Any,
        ) -> Any:
            del cwd, mcp_servers, kwargs
            await self._ensure_session_known(session_id)
            result = await server_mod._fork_server_session(state, session_id)
            new_session_id = str(result.get("session_id", "")).strip()
            if new_session_id:
                self._runtime(new_session_id)
            from acp.schema import ForkSessionResponse

            return ForkSessionResponse(session_id=new_session_id)

        async def resume_session(
            self,
            cwd: str,
            session_id: str,
            mcp_servers: list[Any] | None = None,
            **kwargs: Any,
        ) -> Any:
            del mcp_servers, kwargs
            requested_dir = _resolve_cwd(state.default_project_dir, cwd)
            serialized = await self._ensure_session_known(session_id)
            project_dir = str(serialized.get("project_dir", "")).strip()
            if project_dir and not _workspace_matches(project_dir, requested_dir):
                raise RuntimeError(f"Session not in workspace: {session_id}")
            await self._replay_session_history(session_id)
            from acp.schema import ResumeSessionResponse

            return ResumeSessionResponse(**(await self._session_response_payload(session_id)))

        async def cancel(self, session_id: str, **kwargs: Any) -> None:
            del kwargs
            session = state.sessions.get(session_id)
            if session is not None:
                session.abort()

        async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            del params
            return {"error": f"Unknown method: {method}"}

        async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
            del method, params

    try:
        await run_agent(WorkerAcpAgent(), use_unstable_protocol=True)
    finally:
        await _close_state(state)
