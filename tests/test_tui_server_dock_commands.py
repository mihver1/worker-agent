from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _tui_test_config():
    return SimpleNamespace(
        ui=SimpleNamespace(theme="dark"), keybindings=SimpleNamespace(bindings={})
    )


def _patch_tui_test_context(monkeypatch):
    import artel_tui.app as tui_app

    monkeypatch.setattr(tui_app, "load_config", lambda _: _tui_test_config())
    monkeypatch.setattr(tui_app, "load_prompts", lambda _: {})
    monkeypatch.setattr(tui_app, "load_skills", lambda _: {})
    monkeypatch.setattr(
        tui_app.ArtelApp,
        "_apply_theme",
        lambda self, name: setattr(self, "_active_theme", name),
    )


@pytest.mark.asyncio
async def test_server_add_command_opens_inline_input(monkeypatch, tmp_path):
    from artel_tui.app import ArtelApp

    _patch_tui_test_context(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())

    app = ArtelApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        await app._handle_command("/server-add")
        await pilot.pause()
        assert app._server_dock_input_panel().is_open() is True


@pytest.mark.asyncio
async def test_server_add_command_rejects_inline_argument(monkeypatch, tmp_path):
    from artel_tui.app import ArtelApp

    _patch_tui_test_context(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())
    monkeypatch.setattr("artel_tui.app.load_saved_servers", lambda: [])

    app = ArtelApp()
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        await app._handle_command("/server-add ws://saved:7432")
        await pilot.pause()

    assert messages[-1][0] == "error"
    assert "no longer accepts inline arguments" in messages[-1][1]
    assert not app._saved_servers


@pytest.mark.asyncio
async def test_server_add_submit_composer_opens_inline_input(monkeypatch, tmp_path):
    from artel_tui.app import ArtelApp

    _patch_tui_test_context(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())

    app = ArtelApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        app._composer().load_text("/server-add")
        await app.action_submit_composer()
        await pilot.pause()
        assert app._server_dock_input_panel().is_open() is True


@pytest.mark.asyncio
async def test_server_dock_project_action_deletes_all_project_sessions(monkeypatch, tmp_path):
    import artel_tui.app as tui_app
    from artel_tui.app import ArtelApp, ServerDockNodeData

    _patch_tui_test_context(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())
    monkeypatch.setattr("artel_tui.app.load_saved_servers", lambda: [])

    app = ArtelApp(remote_url="ws://prod:7432", auth_token="tok")
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]

    deleted: list[str] = []

    class _Control:
        def __init__(self, remote_url: str, auth_token: str = "") -> None:
            self.remote_url = remote_url
            self.auth_token = auth_token

        async def list_sessions(self):
            return {
                "sessions": [
                    {"id": "sess-1", "project_dir": "/srv/artel-alice-smith", "title": "A"},
                    {"id": "sess-2", "project_dir": "/srv/artel-alice-smith", "title": "B"},
                    {"id": "sess-3", "project_dir": "/srv/other", "title": "C"},
                ]
            }

        async def request(self, method: str, path: str, *, json_data=None):
            assert method == "DELETE"
            deleted.append(path)
            return {"deleted": path.rsplit("/", 1)[-1]}

    monkeypatch.setattr(tui_app, "RemoteControlClient", _Control)
    app._refresh_server_dock = AsyncMock()  # type: ignore[method-assign]
    app._sync_remote_session_state = AsyncMock()  # type: ignore[method-assign]
    app._load_board_state = AsyncMock()  # type: ignore[method-assign]
    app._remote_session_id = "sess-2"
    app._remote_project_dir = "/srv/artel-alice-smith"

    await app._run_server_dock_action(
        ServerDockNodeData(
            kind="project",
            remote_url="ws://prod:7432",
            auth_token="tok",
            project_dir="/srv/artel-alice-smith",
            name="artel-alice-smith",
        ),
        "delete_project_sessions",
    )

    assert deleted == ["/api/sessions/sess-1", "/api/sessions/sess-2"]
    assert app._refresh_server_dock.await_count == 1
    assert app._sync_remote_session_state.await_count == 1
    assert app._load_board_state.await_count == 1
    assert messages[-1] == (
        "tool",
        "Deleted 2 session(s) for project: artel-alice-smith",
    )


@pytest.mark.asyncio
async def test_server_dock_session_action_deletes_remote_session(monkeypatch, tmp_path):
    import artel_tui.app as tui_app
    from artel_tui.app import ArtelApp, ServerDockNodeData

    _patch_tui_test_context(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())
    monkeypatch.setattr("artel_tui.app.load_saved_servers", lambda: [])

    app = ArtelApp(remote_url="ws://prod:7432", auth_token="tok")
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]

    class _Control:
        def __init__(self, remote_url: str, auth_token: str = "") -> None:
            self.remote_url = remote_url
            self.auth_token = auth_token

        async def request(self, method: str, path: str, *, json_data=None):
            assert method == "DELETE"
            assert path == "/api/sessions/sess-1"
            return {"deleted": "sess-1"}

    monkeypatch.setattr(tui_app, "RemoteControlClient", _Control)
    app._refresh_server_dock = AsyncMock()  # type: ignore[method-assign]

    await app._run_server_dock_action(
        ServerDockNodeData(
            kind="session",
            remote_url="ws://prod:7432",
            auth_token="tok",
            session_id="sess-1",
            project_dir="/srv/proj",
            name="Release prep",
        ),
        "delete_session",
    )

    assert messages[-1] == ("tool", "Deleted session: Release prep")
    app._refresh_server_dock.assert_awaited_once()


@pytest.mark.asyncio
async def test_server_select_command_connects_to_saved_server(monkeypatch, tmp_path):
    import artel_tui.app as tui_app
    from artel_tui.app import ArtelApp

    _patch_tui_test_context(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())
    monkeypatch.setattr(
        tui_app,
        "load_saved_servers",
        lambda: [
            tui_app.SavedArtelServer(name="Prod", remote_url="ws://prod:7432", auth_token="tok")
        ],
    )

    app = ArtelApp()
    seen: list[tuple[str, str, bool]] = []

    async def fake_connect(
        remote_url: str,
        *,
        auth_token: str = "",
        save: bool = True,
        project_dir: str = "",
        resume_session_id: str = "",
    ) -> None:
        seen.append((remote_url, auth_token, save))

    app._connect_to_server = fake_connect  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        await app._handle_command("/server-select Prod")
        await pilot.pause()

    assert seen == [("ws://prod:7432", "tok", False)]


@pytest.mark.asyncio
async def test_server_remove_current_remote_does_not_reappear_on_refresh(monkeypatch, tmp_path):
    import artel_tui.app as tui_app
    from artel_core import config as cfg_mod
    from artel_tui.app import ArtelApp, ServerDockNodeData

    _patch_tui_test_context(monkeypatch)
    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())
    monkeypatch.setattr(
        tui_app,
        "load_saved_servers",
        lambda: [
            tui_app.SavedArtelServer(name="Prod", remote_url="ws://prod:7432", auth_token="tok")
        ],
    )

    app = ArtelApp(remote_url="ws://prod:7432", auth_token="tok")
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]

    class _Control:
        def __init__(self, remote_url: str, auth_token: str = "") -> None:
            self.remote_url = remote_url
            self.auth_token = auth_token

        async def list_sessions(self):
            return {"sessions": []}

    monkeypatch.setattr(tui_app, "RemoteControlClient", _Control)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app._saved_servers) == 1

        await app._run_server_dock_action(
            ServerDockNodeData(
                kind="server",
                remote_url="ws://prod:7432",
                auth_token="tok",
                name="Prod",
            ),
            "remove",
        )
        await pilot.pause()

        assert "ws://prod:7432" in app._dismissed_server_urls
        assert app._saved_servers == []
        assert messages[-1] == ("tool", "Removed server: Prod")

        await app._refresh_server_dock()
        await pilot.pause()

        assert app._saved_servers == []


@pytest.mark.asyncio
async def test_connecting_removed_remote_clears_dismissed_state(monkeypatch, tmp_path):
    from artel_tui.app import ArtelApp

    _patch_tui_test_context(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())
    monkeypatch.setattr("artel_tui.app.load_saved_servers", lambda: [])

    app = ArtelApp()
    app._dismissed_server_urls.add("ws://prod:7432")
    app._sync_remote_session_state = AsyncMock()  # type: ignore[method-assign]
    app._sync_remote_extension_commands = AsyncMock()  # type: ignore[method-assign]
    app._refresh_server_dock = AsyncMock()  # type: ignore[method-assign]
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]

    await app._connect_to_server("ws://prod:7432", auth_token="tok", save=False)

    assert "ws://prod:7432" not in app._dismissed_server_urls
    assert app.remote_url == "ws://prod:7432"
    assert app.auth_token == "tok"
    assert messages[-1] == ("tool", "Connected to Artel @ prod:7432")


@pytest.mark.asyncio
async def test_dock_input_submit_adds_and_connects_server(monkeypatch, tmp_path):
    from artel_tui.app import ArtelApp, DockInputSubmitted

    _patch_tui_test_context(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())

    app = ArtelApp()
    seen: list[tuple[str, str, bool]] = []

    async def fake_connect(
        remote_url: str,
        *,
        auth_token: str = "",
        save: bool = True,
        project_dir: str = "",
        resume_session_id: str = "",
    ) -> None:
        seen.append((remote_url, auth_token, save))

    app._connect_to_server = fake_connect  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        await app.on_dock_input_submitted(
            DockInputSubmitted("add_server", "ws://saved-from-inline:7432")
        )
        await pilot.pause()

    assert seen == [("ws://saved-from-inline:7432", "", True)]
