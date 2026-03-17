from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from artel_tui.app import InlineInputSubmitted


def _patch_tui_test_context(monkeypatch):
    from types import SimpleNamespace

    import artel_tui.app as tui_app

    monkeypatch.setattr(
        tui_app,
        "load_config",
        lambda _: SimpleNamespace(
            ui=SimpleNamespace(theme="dark"),
            keybindings=SimpleNamespace(bindings={}),
        ),
    )
    monkeypatch.setattr(tui_app, "load_prompts", lambda _: {})
    monkeypatch.setattr(tui_app, "load_skills", lambda _: {})
    monkeypatch.setattr(
        tui_app.ArtelApp,
        "_apply_theme",
        lambda self, name: setattr(self, "_active_theme", name),
    )


@pytest.mark.asyncio
async def test_remote_oauth_code_uses_inline_input(monkeypatch, tmp_path):
    import artel_tui.app as tui_app
    from artel_tui.app import ArtelApp

    _patch_tui_test_context(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())

    app = ArtelApp(remote_url="ws://prod:7432", auth_token="tok")
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]

    class _Control:
        def __init__(self, remote_url: str, auth_token: str = "") -> None:
            self.remote_url = remote_url
            self.auth_token = auth_token

        async def start_oauth(self, provider: str):
            assert provider == "anthropic"
            return {"authorize_url": "https://example.test/auth", "login_id": "login-1"}

        async def complete_oauth(self, login_id: str, payload: dict[str, str]):
            assert login_id == "login-1"
            assert payload == {"code": "abc123"}
            return {"ok": True}

    monkeypatch.setattr(tui_app, "RemoteControlClient", _Control)
    monkeypatch.setattr(tui_app.webbrowser, "open", lambda url: True)

    async with app.run_test() as pilot:
        await pilot.pause()
        await app._run_remote_code_paste_oauth("anthropic")
        await pilot.pause()
        assert app._inline_input_panel().is_open() is True
        await app.on_inline_input_submitted(InlineInputSubmitted("remote_oauth_code", "abc123"))
        await pilot.pause()

    assert messages[-1] == ("tool", "Anthropic authorized on the remote server!")
