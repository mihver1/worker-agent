from __future__ import annotations

import json

from click.testing import CliRunner


def test_schedule_cli_run_uses_managed_local_server_when_remote_not_provided(monkeypatch, tmp_path):
    import importlib

    from artel_core import config as cfg_mod

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()

    cli_mod = importlib.import_module("artel_core.cli")

    class _Handle:
        remote_url = "ws://127.0.0.1:7432"
        auth_token = "tok"

    async def fake_ensure_managed_local_server(project_dir: str):
        assert project_dir == str(tmp_path)
        return _Handle()

    seen: dict[str, str] = {}

    class _FakeControl:
        def __init__(self, remote_url: str, auth_token: str = "") -> None:
            seen["remote_url"] = remote_url
            seen["auth_token"] = auth_token

        async def run_schedule(self, schedule_id: str):
            return {"schedule_id": schedule_id, "ok": True}

    monkeypatch.setattr(
        "artel_tui.local_server.ensure_managed_local_server", fake_ensure_managed_local_server
    )
    monkeypatch.setattr("artel_core.control.RemoteArtelControl", _FakeControl)

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["schedule", "run", "heartbeat"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schedule_id"] == "heartbeat"
    assert seen == {"remote_url": "ws://127.0.0.1:7432", "auth_token": "tok"}
