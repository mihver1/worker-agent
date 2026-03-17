from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_local_rule_persist_disable_updates_storage(monkeypatch, tmp_path):
    from artel_core import config as cfg_mod
    from artel_core.rules import add_rule, get_rule
    from artel_tui.app import ArtelApp

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())

    rule = add_rule(scope="project", text="Use pytest.", project_dir=str(tmp_path), enabled=True)
    app = ArtelApp()
    seen: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant": seen.append((content, role))  # type: ignore[method-assign]

    await app._cmd_rule(f"persist disable {rule.id}")

    updated = get_rule(rule.id, str(tmp_path))
    assert updated is not None
    assert updated.enabled is False
    assert any(f"Persistently disabled rule {rule.id}." in message for message, _ in seen)


@pytest.mark.asyncio
async def test_remote_rule_persist_enable_uses_edit_rule(monkeypatch):
    from artel_tui.app import ArtelApp

    class _RemoteClient:
        def __init__(self):
            self.calls = []

        async def edit_rule(
            self, rule_id: str, *, text=None, scope=None, enabled=None, project_dir: str = ""
        ):
            self.calls.append((rule_id, text, scope, enabled, project_dir))
            return {"rule": {"id": rule_id, "enabled": enabled}}

    app = ArtelApp(remote_url="ws://localhost:7432")
    app._remote_project_dir = "/srv/project"
    client = _RemoteClient()
    app._remote_control_client = client
    seen: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant": seen.append((content, role))  # type: ignore[method-assign]

    await app._cmd_rule("persist enable rule-1")

    assert client.calls == [("rule-1", None, None, True, "/srv/project")]
    assert any("Persistently enabled rule rule-1." in message for message, _ in seen)
