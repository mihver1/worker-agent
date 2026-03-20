from __future__ import annotations

from types import SimpleNamespace

import pytest
from artel_core.delegation.registry import get_registry, reset_registry
from artel_tui.delegation_widget import DelegationStatusWidget


async def test_status_widget_loads_local_session_runs() -> None:
    reset_registry()
    registry = get_registry()
    run = registry.create_run(
        parent_session_id="session-1",
        task="Inspect local state",
        context="",
        model="mock/mock-model",
        project_dir="/tmp/project",
        mode="readonly",
    )
    registry.mark_running(run.id)
    app = SimpleNamespace(remote_url="", _session=SimpleNamespace(session_id="session-1"))
    widget = DelegationStatusWidget(app)

    runs, error = await widget._load_runs()

    assert error == ""
    assert runs[0]["id"] == run.id
    assert runs[0]["status"] == "running"


async def test_status_widget_loads_remote_session_runs() -> None:
    class _RemoteControl:
        async def request(self, method: str, path: str):
            assert method == "GET"
            assert path == "/api/sessions/session-1/delegates"
            return {
                "delegates": [
                    {
                        "id": "run-1",
                        "status": "completed",
                        "task": "Inspect remote state",
                    }
                ]
            }

    app = SimpleNamespace(
        remote_url="ws://localhost:7432",
        _remote_session_id="session-1",
        _remote_control=lambda: _RemoteControl(),
    )
    widget = DelegationStatusWidget(app)

    runs, error = await widget._load_runs()

    assert error == ""
    assert runs == [{"id": "run-1", "status": "completed", "task": "Inspect remote state"}]


def test_status_widget_skips_poll_when_workspace_sidebar_hidden() -> None:
    app = SimpleNamespace(remote_url="", _session=SimpleNamespace(session_id="session-1"), _sidebar_visible=False)
    widget = DelegationStatusWidget(app)
    assert widget._should_poll() is False


def test_delegate_commands_appear_in_command_suggestions():
    from artel_tui.app import ArtelApp

    app = ArtelApp()
    values = [suggestion.value for suggestion in app._command_suggestions()]

    assert "/delegates" in values
    assert "/agents" in values


@pytest.mark.asyncio
async def test_handle_local_agents_command_lists_runs(monkeypatch):
    from artel_tui.app import ArtelApp

    reset_registry()
    registry = get_registry()
    run = registry.create_run(
        parent_session_id="local-session",
        task="Inspect local delegation state",
        context="",
        model="mock/mock-model",
        project_dir="/tmp/project",
        mode="readonly",
    )
    registry.mark_completed(run.id, "Done")

    app = ArtelApp()
    app._session = SimpleNamespace(session_id="local-session")
    seen_messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app, "_add_message", lambda content, role="assistant": seen_messages.append((content, role))
    )

    await app._cmd_agents("")

    assert seen_messages == [
        (
            "Orchestration runs: 1 total (completed=1)\n"
            f"- {run.id} [completed] (readonly) Inspect local delegation state — Done",
            "tool",
        )
    ]


@pytest.mark.asyncio
async def test_handle_remote_agents_command_uses_control_plane(monkeypatch):
    from artel_tui.app import ArtelApp

    class _RemoteClient:
        async def request(self, method: str, path: str, *, json_data=None):
            if method == "GET" and path == "/api/sessions/remote-session/delegates":
                return {
                    "delegates": [
                        {
                            "id": "run-1",
                            "status": "running",
                            "mode": "readonly",
                            "task": "Inspect src",
                        }
                    ]
                }
            if method == "GET" and path == "/api/sessions/remote-session/delegates/run-1":
                return {
                    "delegate": {
                        "id": "run-1",
                        "status": "running",
                        "mode": "readonly",
                        "task": "Inspect src",
                        "events": ["tool read", "result read: ok"],
                        "latest_update": "result read: ok",
                    }
                }
            raise AssertionError((method, path, json_data))

    app = ArtelApp(remote_url="ws://localhost:7432")
    app._remote_session_id = "remote-session"
    app._remote_control_client = _RemoteClient()
    seen_messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app, "_add_message", lambda content, role="assistant": seen_messages.append((content, role))
    )

    await app._cmd_agents("")

    assert seen_messages == [
        (
            "Orchestration runs: 1 total (running=1)\n- run-1 [running] (readonly) Inspect src",
            "tool",
        )
    ]


@pytest.mark.asyncio
async def test_handle_local_agents_tail_command(monkeypatch):
    from artel_tui.app import ArtelApp

    reset_registry()
    registry = get_registry()
    run = registry.create_run(
        parent_session_id="local-session",
        task="Inspect local tail",
        context="",
        model="mock/mock-model",
        project_dir="/tmp/project",
        mode="readonly",
    )
    registry.mark_running(run.id)
    registry.append_event(run.id, "tool read")
    registry.append_event(run.id, "result read: ok")

    app = ArtelApp()
    app._session = SimpleNamespace(session_id="local-session")
    seen_messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app, "_add_message", lambda content, role="assistant": seen_messages.append((content, role))
    )

    await app._cmd_agents(f"tail {run.id}")

    assert seen_messages == [
        (
            f"Tail for orchestration run {run.id}:\n"
            "- tool read\n"
            "- result read: ok\n\n"
            "Latest: result read: ok",
            "tool",
        )
    ]


@pytest.mark.asyncio
async def test_handle_remote_agents_tail_command(monkeypatch):
    from artel_tui.app import ArtelApp

    class _RemoteClient:
        async def request(self, method: str, path: str, *, json_data=None):
            if method == "GET" and path == "/api/sessions/remote-session/delegates/run-1":
                return {
                    "delegate": {
                        "id": "run-1",
                        "status": "running",
                        "mode": "readonly",
                        "task": "Inspect src",
                        "events": ["tool read", "result read: ok"],
                        "latest_update": "result read: ok",
                    }
                }
            raise AssertionError((method, path, json_data))

    app = ArtelApp(remote_url="ws://localhost:7432")
    app._remote_session_id = "remote-session"
    app._remote_control_client = _RemoteClient()
    seen_messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app, "_add_message", lambda content, role="assistant": seen_messages.append((content, role))
    )

    await app._cmd_agents("tail run-1")

    assert seen_messages == [
        (
            "Tail for orchestration run run-1:\n"
            "- tool read\n"
            "- result read: ok\n\n"
            "Latest: result read: ok",
            "tool",
        )
    ]
