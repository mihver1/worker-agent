"""ACP unit tests using fake SDK shims."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from worker_core.config import WorkerConfig
from worker_server.server import ServerState


def _record_class(name: str) -> type[Any]:
    class _Record:
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

        def __repr__(self) -> str:
            return f"{name}({self.__dict__!r})"

    _Record.__name__ = name
    return _Record


class _FakeConn:
    def __init__(self, *, permission_option_id: str = "approve") -> None:
        self.permission_option_id = permission_option_id
        self.updates: list[tuple[str, Any]] = []
        self.permission_requests: list[dict[str, Any]] = []

    async def session_update(self, *, session_id: str, update: Any) -> None:
        self.updates.append((session_id, update))

    async def request_permission(self, **kwargs: Any) -> Any:
        self.permission_requests.append(dict(kwargs))
        return SimpleNamespace(
            outcome={
                "outcome": "selected",
                "optionId": self.permission_option_id,
            }
        )


def _install_fake_acp(monkeypatch: pytest.MonkeyPatch, *, run_agent: Any) -> None:
    acp_mod = ModuleType("acp")
    acp_mod.__path__ = []  # type: ignore[attr-defined]
    contrib_mod = ModuleType("acp.contrib")
    contrib_mod.__path__ = []  # type: ignore[attr-defined]
    permissions_mod = ModuleType("acp.contrib.permissions")
    tool_calls_mod = ModuleType("acp.contrib.tool_calls")
    interfaces_mod = ModuleType("acp.interfaces")
    schema_mod = ModuleType("acp.schema")

    class Agent:
        pass

    class Client:
        pass

    class PermissionBroker:
        def __init__(self, session_id: str, requester: Any, tracker: Any) -> None:
            self.session_id = session_id
            self.requester = requester
            self.tracker = tracker

        async def request_for(self, tool_call_id: str) -> Any:
            return await self.requester(
                session_id=self.session_id,
                tool_call_id=tool_call_id,
                options=[
                    {"id": "approve"},
                    {"id": "approve_for_session"},
                    {"id": "deny"},
                ],
            )

    class ToolCallTracker:
        def __init__(self) -> None:
            self.forgotten: list[str] = []

        def progress(self, tool_call_id: str, *, status: str) -> dict[str, str]:
            return {
                "kind": "tool_progress",
                "tool_call_id": tool_call_id,
                "status": status,
            }

        def forget(self, tool_call_id: str) -> None:
            self.forgotten.append(tool_call_id)

    def start_tool_call(**kwargs: Any) -> dict[str, Any]:
        tool_kind = kwargs.pop("kind", None)
        return {
            "kind": "start_tool_call",
            "tool_kind": tool_kind,
            **kwargs,
        }

    def update_tool_call(**kwargs: Any) -> dict[str, Any]:
        return {"kind": "update_tool_call", **kwargs}

    def update_agent_message_text(text: str) -> dict[str, str]:
        return {"kind": "message_text", "text": text}

    def update_agent_thought_text(text: str) -> dict[str, str]:
        return {"kind": "thought_text", "text": text}

    def text_block(text: str) -> dict[str, str]:
        return {"text": text}

    def tool_content(block: Any) -> Any:
        return block

    acp_mod.Agent = Agent
    acp_mod.run_agent = run_agent
    acp_mod.start_tool_call = start_tool_call
    acp_mod.text_block = text_block
    acp_mod.tool_content = tool_content
    acp_mod.update_agent_message_text = update_agent_message_text
    acp_mod.update_agent_thought_text = update_agent_thought_text
    acp_mod.update_tool_call = update_tool_call

    permissions_mod.PermissionBroker = PermissionBroker
    tool_calls_mod.ToolCallTracker = ToolCallTracker
    interfaces_mod.Client = Client

    for name in (
        "AgentCapabilities",
        "AuthenticateResponse",
        "ConfigOptionUpdate",
        "CurrentModeUpdate",
        "ForkSessionResponse",
        "Implementation",
        "InitializeResponse",
        "ListSessionsResponse",
        "LoadSessionResponse",
        "ModelInfo",
        "NewSessionResponse",
        "PromptCapabilities",
        "PromptResponse",
        "ResumeSessionResponse",
        "SessionCapabilities",
        "SessionConfigOption",
        "SessionConfigOptionSelect",
        "SessionConfigSelectOption",
        "SessionForkCapabilities",
        "SessionInfo",
        "SessionInfoUpdate",
        "SessionListCapabilities",
        "SessionMode",
        "SessionModeState",
        "SessionModelState",
        "SessionResumeCapabilities",
        "SetSessionConfigOptionResponse",
        "SetSessionModelResponse",
        "SetSessionModeResponse",
        "ToolCallLocation",
        "Usage",
        "UsageUpdate",
    ):
        setattr(schema_mod, name, _record_class(name))

    monkeypatch.setitem(sys.modules, "acp", acp_mod)
    monkeypatch.setitem(sys.modules, "acp.contrib", contrib_mod)
    monkeypatch.setitem(sys.modules, "acp.contrib.permissions", permissions_mod)
    monkeypatch.setitem(sys.modules, "acp.contrib.tool_calls", tool_calls_mod)
    monkeypatch.setitem(sys.modules, "acp.interfaces", interfaces_mod)
    monkeypatch.setitem(sys.modules, "acp.schema", schema_mod)


async def _fake_resolve_api_key(config: Any, provider_name: str) -> tuple[str, str]:
    del config, provider_name
    return "token", "api"


def _patch_acp_server_state(
    monkeypatch: pytest.MonkeyPatch,
    acp_mod: Any,
    state: ServerState,
    titles: dict[str, str],
) -> None:
    import worker_core.cli as cli_mod

    async def fake_create_state(project_dir: str | None = None) -> ServerState:
        del project_dir
        return state

    async def fake_close_state(state_obj: ServerState) -> None:
        state_obj.closed = True  # type: ignore[attr-defined]

    async def fake_initialize_session_state(
        state_obj: ServerState,
        session_id: str,
        *,
        model: str,
        project_dir: str,
        thinking_level: str | None = None,
    ) -> None:
        state_obj.session_provider_models[session_id] = model
        state_obj.session_projects[session_id] = project_dir
        if thinking_level is not None:
            state_obj.session_thinking_levels[session_id] = thinking_level

    async def fake_serialize_session(
        state_obj: ServerState,
        session_id: str,
        session_info: Any | None = None,
    ) -> dict[str, Any]:
        del session_info
        return {
            "id": session_id,
            "title": titles.get(session_id, ""),
            "model": state_obj.session_provider_models.get(
                session_id,
                state_obj.config.agent.model,
            ),
            "project_dir": state_obj.session_projects.get(
                session_id,
                state_obj.default_project_dir,
            ),
            "thinking_level": state_obj.session_thinking_levels.get(
                session_id,
                state_obj.config.agent.thinking,
            ),
            "updated_at": "2026-03-07 12:00:00",
            "exists": (
                session_id in state_obj.session_provider_models
                or session_id in state_obj.session_projects
                or session_id in state_obj.session_thinking_levels
            ),
        }

    async def fake_list_serialized_sessions(
        state_obj: ServerState,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        del limit
        session_ids = sorted(
            set(state_obj.session_provider_models)
            | set(state_obj.session_projects)
            | set(state_obj.session_thinking_levels)
        )
        return [
            await fake_serialize_session(state_obj, session_id)
            for session_id in session_ids
        ]

    async def fake_switch_server_session_model(
        state_obj: ServerState,
        session_id: str,
        model_id: str,
    ) -> dict[str, Any]:
        state_obj.session_provider_models[session_id] = model_id
        return await fake_serialize_session(state_obj, session_id)

    async def fake_set_server_session_thinking(
        state_obj: ServerState,
        session_id: str,
        thinking_level: str,
    ) -> dict[str, Any]:
        state_obj.session_thinking_levels[session_id] = thinking_level
        return await fake_serialize_session(state_obj, session_id)

    async def fake_catalog(config: Any) -> dict[str, Any]:
        del config
        return {
            "mock": SimpleNamespace(
                name="Mock Provider",
                models=[
                    SimpleNamespace(
                        id="mock-model",
                        name="Mock Model",
                        context_window=64_000,
                    )
                ],
            )
        }

    monkeypatch.setattr(acp_mod, "_create_state", fake_create_state)
    monkeypatch.setattr(acp_mod, "_close_state", fake_close_state)
    monkeypatch.setattr(cli_mod, "_resolve_api_key", _fake_resolve_api_key)
    monkeypatch.setattr(
        acp_mod.server_mod,
        "_initialize_session_state",
        fake_initialize_session_state,
    )
    monkeypatch.setattr(
        acp_mod.server_mod,
        "_serialize_session",
        fake_serialize_session,
    )
    monkeypatch.setattr(
        acp_mod.server_mod,
        "_list_serialized_sessions",
        fake_list_serialized_sessions,
    )
    monkeypatch.setattr(
        acp_mod.server_mod,
        "_switch_server_session_model",
        fake_switch_server_session_model,
    )
    monkeypatch.setattr(
        acp_mod.server_mod,
        "_set_server_session_thinking",
        fake_set_server_session_thinking,
    )
    monkeypatch.setattr(
        acp_mod.server_mod,
        "get_effective_provider_catalog",
        fake_catalog,
    )
    monkeypatch.setattr(
        acp_mod.server_mod,
        "provider_requires_api_key",
        lambda config, provider_id: False,
    )


def _event(event_type: Any, **kwargs: Any) -> Any:
    defaults = {
        "content": "",
        "tool_name": "",
        "tool_args": {},
        "tool_call_id": "",
        "is_error": False,
        "error": "",
        "usage": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(type=event_type, **defaults)


@pytest.mark.asyncio
async def test_run_acp_scopes_workspace_and_updates_config(monkeypatch, tmp_path):
    import worker_server.acp as acp_mod

    captured: dict[str, Any] = {}
    titles: dict[str, str] = {}
    state = ServerState(config=WorkerConfig(), default_project_dir=str(tmp_path))

    async def fake_run_agent(agent: Any) -> None:
        conn = _FakeConn()
        captured["conn"] = conn
        agent.on_connect(conn)
        captured["initialize"] = await agent.initialize(protocol_version=7)
        new_session = await agent.new_session(cwd="project")
        captured["new_session"] = new_session
        session_id = new_session.session_id
        await agent.set_session_mode("code", session_id)
        await agent.set_config_option("thinking", session_id, "high")
        await agent.set_config_option("model", session_id, "mock/mock-model")
        state.session_provider_models["outside-session"] = "mock/mock-model"
        state.session_projects["outside-session"] = str(tmp_path / "outside")
        state.session_thinking_levels["outside-session"] = "off"
        captured["list_sessions"] = await agent.list_sessions(cwd=str(tmp_path / "project"))
        captured["load_session"] = await agent.load_session(
            cwd=str(tmp_path / "project"),
            session_id=session_id,
        )

    _install_fake_acp(monkeypatch, run_agent=fake_run_agent)
    _patch_acp_server_state(monkeypatch, acp_mod, state, titles)

    await acp_mod.run_acp()

    session_id = captured["new_session"].session_id
    assert getattr(state, "closed", False) is True
    assert state.session_projects[session_id] == str((tmp_path / "project").resolve())
    assert state.session_thinking_levels[session_id] == "high"
    assert state.session_provider_models[session_id] == "mock/mock-model"
    assert [item.session_id for item in captured["list_sessions"].sessions] == [session_id]
    assert captured["load_session"].models.current_model_id == "mock/mock-model"
    updates = [update for _, update in captured["conn"].updates]
    assert any(getattr(update, "current_mode_id", "") == "code" for update in updates)


@pytest.mark.asyncio
async def test_run_acp_prompt_streams_updates_and_permission_requests(
    monkeypatch,
    tmp_path,
):
    import worker_server.acp as acp_mod
    import worker_server.server as server_mod

    captured: dict[str, Any] = {}
    titles: dict[str, str] = {}
    state = ServerState(config=WorkerConfig(), default_project_dir=str(tmp_path))

    class _Store:
        def __init__(self) -> None:
            self.renamed: list[tuple[str, str]] = []

        async def rename_session(self, session_id: str, title: str) -> None:
            titles[session_id] = title
            self.renamed.append((session_id, title))

    state.store = _Store()  # type: ignore[assignment]

    class _FakeSession:
        def __init__(self, session_id: str) -> None:
            self.session_id = session_id
            self.context_window = 100

        async def run(self, content: str):
            assert content == "Inspect README"
            yield _event(server_mod.AgentEventType.REASONING_DELTA, content="thinking")
            yield _event(server_mod.AgentEventType.TEXT_DELTA, content="hello")
            yield _event(
                server_mod.AgentEventType.TOOL_CALL,
                tool_name="read",
                tool_args={"path": "README.md"},
                tool_call_id="tc1",
            )
            approved = await state.permission_callbacks[self.session_id](
                "read",
                {"path": "README.md"},
            )
            assert approved is True
            yield _event(
                server_mod.AgentEventType.TOOL_RESULT,
                content="contents",
                tool_name="read",
                tool_call_id="tc1",
                is_error=False,
            )
            yield _event(
                server_mod.AgentEventType.DONE,
                usage=SimpleNamespace(input_tokens=3, output_tokens=2),
            )

        async def generate_title(self, content: str) -> str:
            assert content == "Inspect README"
            return "Prompt title"

        def _estimate_tokens(self) -> int:
            return 12

    async def fake_create_server_session(state_obj: ServerState, session_id: str) -> Any:
        session = _FakeSession(session_id)
        state_obj.sessions[session_id] = session
        return session

    async def fake_run_agent(agent: Any) -> None:
        conn = _FakeConn(permission_option_id="approve_for_session")
        captured["conn"] = conn
        agent.on_connect(conn)
        new_session = await agent.new_session(cwd=".")
        session_id = new_session.session_id
        captured["session_id"] = session_id
        captured["response"] = await agent.prompt(
            prompt=[SimpleNamespace(text="Inspect README")],
            session_id=session_id,
        )

    _install_fake_acp(monkeypatch, run_agent=fake_run_agent)
    _patch_acp_server_state(monkeypatch, acp_mod, state, titles)
    monkeypatch.setattr(
        acp_mod.server_mod,
        "_create_server_session",
        fake_create_server_session,
    )

    await acp_mod.run_acp()

    session_id = captured["session_id"]
    response = captured["response"]
    assert state.store.renamed == [(session_id, "Prompt title")]  # type: ignore[union-attr]
    assert response.stop_reason == "end_turn"
    assert response.usage.input_tokens == 3
    assert response.usage.output_tokens == 2
    assert captured["conn"].permission_requests == [
        {
            "session_id": session_id,
            "tool_call_id": "tc1",
            "options": [
                {"id": "approve"},
                {"id": "approve_for_session"},
                {"id": "deny"},
            ],
        }
    ]
    updates = [update for _, update in captured["conn"].updates]
    assert any(
        isinstance(update, dict)
        and update.get("kind") == "thought_text"
        and update.get("text") == "thinking"
        for update in updates
    )
    assert any(
        isinstance(update, dict)
        and update.get("kind") == "message_text"
        and update.get("text") == "hello"
        for update in updates
    )
    assert any(
        isinstance(update, dict)
        and update.get("kind") == "start_tool_call"
        and update.get("tool_call_id") == "tc1"
        for update in updates
    )
    assert any(
        isinstance(update, dict)
        and update.get("kind") == "tool_progress"
        and update.get("status") == "in_progress"
        for update in updates
    )
    assert any(
        isinstance(update, dict)
        and update.get("kind") == "update_tool_call"
        and update.get("status") == "completed"
        for update in updates
    )
    assert any(
        getattr(update, "title", "") == "Prompt title"
        for update in updates
        if not isinstance(update, dict)
    )
    assert any(
        getattr(update, "used", None) == 12 and getattr(update, "size", None) == 100
        for update in updates
        if not isinstance(update, dict)
    )
