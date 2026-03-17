from __future__ import annotations

import json

from click.testing import CliRunner


def test_schedule_cli_add_list_show_enable_disable_delete(monkeypatch, tmp_path):
    import importlib

    from artel_core import config as cfg_mod

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()

    cli_mod = importlib.import_module("artel_core.cli")
    runner = CliRunner()

    add_result = runner.invoke(
        cli_mod.cli,
        [
            "schedule",
            "add",
            "heartbeat",
            "--every",
            "60",
            "--prompt",
            "Summarize repo health",
            "--run-missed",
            "latest",
        ],
    )
    assert add_result.exit_code == 0
    assert "Schedule added: heartbeat" in add_result.output

    list_result = runner.invoke(cli_mod.cli, ["schedule", "list"])
    assert list_result.exit_code == 0
    assert "heartbeat" in list_result.output
    assert "run_missed=latest" in list_result.output

    show_result = runner.invoke(cli_mod.cli, ["schedule", "show", "heartbeat"])
    assert show_result.exit_code == 0
    payload = json.loads(show_result.output)
    assert payload["id"] == "heartbeat"

    disable_result = runner.invoke(cli_mod.cli, ["schedule", "disable", "heartbeat"])
    assert disable_result.exit_code == 0
    assert "Schedule disabled: heartbeat" in disable_result.output

    enable_result = runner.invoke(cli_mod.cli, ["schedule", "enable", "heartbeat"])
    assert enable_result.exit_code == 0
    assert "Schedule enabled: heartbeat" in enable_result.output

    delete_result = runner.invoke(cli_mod.cli, ["schedule", "delete", "heartbeat"])
    assert delete_result.exit_code == 0
    assert "Schedule deleted: heartbeat" in delete_result.output


def test_schedule_cli_run_calls_remote_control(monkeypatch, tmp_path):
    import importlib

    from artel_core import config as cfg_mod

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()

    cli_mod = importlib.import_module("artel_core.cli")
    seen: dict[str, str] = {}

    class _FakeControl:
        def __init__(self, remote_url: str, auth_token: str = "") -> None:
            seen["remote_url"] = remote_url
            seen["auth_token"] = auth_token

        async def run_schedule(self, schedule_id: str):
            return {"schedule_id": schedule_id, "ok": True}

    monkeypatch.setattr("artel_core.control.RemoteArtelControl", _FakeControl)
    runner = CliRunner()
    result = runner.invoke(
        cli_mod.cli,
        ["schedule", "run", "heartbeat", "--remote-url", "ws://127.0.0.1:7432", "--token", "abc"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["schedule_id"] == "heartbeat"
    assert seen == {"remote_url": "ws://127.0.0.1:7432", "auth_token": "abc"}
