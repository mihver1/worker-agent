"""Automated smoke coverage for primary Artel CLI entrypoints.

These tests define the minimum expected behavior for the primary user-facing
commands tracked in issue #12:
- `artel`
- `artel -p`
- `artel serve`
- `artel connect`
- `artel mcp ...`
- `artel schedule ...`
- `artel rules`
- `artel ext ...`
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from click.testing import CliRunner


def _run_coro(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_cli_smoke_default_artel_starts_local_tui(monkeypatch):
    import worker_tui.app as tui_app
    import worker_tui.local_server as local_server_mod
    from worker_core import cli as cli_mod
    from worker_core.artel_bootstrap import ArtelBootstrapResult

    expected_project_dir = str(Path("/tmp/project").resolve())
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli_mod.os, "getcwd", lambda: "/tmp/project")
    monkeypatch.setattr(
        "worker_core.artel_bootstrap.bootstrap_artel",
        lambda project_dir=None, command_name=None, prompt=None: ArtelBootstrapResult(
            project_dir=expected_project_dir,
            cmux_required=False,
            cmux_preflight=None,
        ),
    )

    async def fake_ensure_managed_local_server(project_dir: str):
        assert project_dir == expected_project_dir
        return local_server_mod.LocalServerHandle(
            remote_url="ws://127.0.0.1:9011",
            auth_token="artel_local_token",
            project_dir=project_dir,
            pid=4321,
        )

    def fake_run_tui(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(
        local_server_mod, "ensure_managed_local_server", fake_ensure_managed_local_server
    )
    monkeypatch.setattr(tui_app, "run_tui", fake_run_tui)
    monkeypatch.setattr(cli_mod.asyncio, "run", _run_coro)

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, [])

    assert result.exit_code == 0
    assert captured == {
        "remote_url": "ws://127.0.0.1:9011",
        "auth_token": "artel_local_token",
        "continue_session": False,
        "resume_id": "",
    }


def test_cli_smoke_prompt_mode_invokes_print_flow(monkeypatch):
    from worker_core import cli as cli_mod
    from worker_core.artel_bootstrap import ArtelBootstrapResult

    captured: dict[str, object] = {}

    async def fake_print_mode(prompt: str, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)

    monkeypatch.setattr(cli_mod.os, "getcwd", lambda: "/tmp/project")
    monkeypatch.setattr(
        "worker_core.artel_bootstrap.bootstrap_artel",
        lambda project_dir=None, command_name=None, prompt=None: ArtelBootstrapResult(
            project_dir=str(Path("/tmp/project").resolve()),
            cmux_required=False,
            cmux_preflight=None,
        ),
    )
    monkeypatch.setattr(cli_mod, "_print_mode", fake_print_mode)
    monkeypatch.setattr(cli_mod.asyncio, "run", _run_coro)

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["-p", "hello world"])

    assert result.exit_code == 0
    assert captured == {
        "prompt": "hello world",
        "continue_session": False,
        "resume_id": "",
    }


def test_cli_smoke_serve_invokes_server_entrypoint(monkeypatch):
    import worker_server.server as server_mod
    from worker_core import cli as cli_mod

    captured: dict[str, object] = {}

    async def fake_run_server(**kwargs):
        captured.update(kwargs)
        announce = kwargs["announce"]
        assert callable(announce)
        announce("Artel server starting")

    monkeypatch.setattr(server_mod, "run_server", fake_run_server)
    monkeypatch.setattr(cli_mod.asyncio, "run", _run_coro)

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["serve", "--host", "0.0.0.0", "--port", "9000"])

    assert result.exit_code == 0
    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9000
    assert "Artel server starting" in result.output


def test_cli_smoke_connect_invokes_remote_tui(monkeypatch):
    import worker_tui.app as tui_app
    from worker_core import cli as cli_mod

    captured: dict[str, str] = {}

    def fake_run_tui(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(tui_app, "run_tui", fake_run_tui)

    runner = CliRunner()
    result = runner.invoke(
        cli_mod.cli,
        ["connect", "ws://host:7432", "--token", "tok_test", "--forward-credentials", "all"],
    )

    assert result.exit_code == 0
    assert captured == {
        "remote_url": "ws://host:7432",
        "auth_token": "tok_test",
        "forward_credentials": "all",
    }


def test_cli_smoke_mcp_show_effective_returns_structured_payload(monkeypatch, tmp_path):
    from worker_core import cli as cli_mod
    from worker_core.mcp import LoadedMCPConfig, MCPServerConfig

    class FakeRegistry:
        def load_merged_config(self, project_dir: str):
            assert project_dir == str(tmp_path)
            return LoadedMCPConfig(
                servers={
                    "demo": MCPServerConfig(
                        name="demo",
                        transport="stdio",
                        command="python3",
                        args=["server.py"],
                        tool_prefix="demo__",
                    )
                },
                sources=[tmp_path / ".artel" / "mcp.json"],
            )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("worker_core.mcp.MCPRegistry", FakeRegistry)

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["mcp", "show", "--scope", "effective"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["sources"] == [str(tmp_path / ".artel" / "mcp.json")]
    assert payload["servers"]["demo"]["transport"] == "stdio"
    assert payload["servers"]["demo"]["tool_prefix"] == "demo__"


def test_cli_smoke_schedule_list_shows_expected_summary(monkeypatch, tmp_path):
    from worker_core import cli as cli_mod
    from worker_core.schedules import ScheduleRecord

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_mod,
        "load_schedules",
        lambda project_dir: [
            ScheduleRecord(
                id="heartbeat",
                scope="project",
                kind="interval",
                every_seconds=300,
                prompt="Summarize repo health",
                execution_mode="readonly",
                session_mode="reuse",
                run_missed="latest",
            )
        ],
    )

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["schedule", "list"])

    assert result.exit_code == 0
    assert "heartbeat [project] [enabled] interval=every 300s" in result.output
    assert "mode=readonly/reuse" in result.output
    assert "run_missed=latest" in result.output


def test_cli_smoke_rules_list_shows_expected_summary(monkeypatch, tmp_path):
    from worker_core import cli as cli_mod
    from worker_core.rules import RuleRecord

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_mod,
        "list_rules",
        lambda project_dir: [
            RuleRecord(
                id="rule-1",
                scope="project",
                text="Do not use bash in this repo.",
                enabled=True,
                order=1,
            )
        ],
    )

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["rules"])

    assert result.exit_code == 0
    assert "1. rule-1 [project] [enabled] Do not use bash in this repo." in result.output


def test_cli_smoke_ext_list_shows_expected_entries(monkeypatch):
    from worker_core import cli as cli_mod
    from worker_core.extensions_admin import ExtensionInfo

    monkeypatch.setattr(
        "worker_core.extensions_admin.list_installed_extensions",
        lambda: [
            ExtensionInfo(name="artel-mcp", version="bundled", source="bundled"),
            ExtensionInfo(
                name="demo-ext", version="1.2.3", source="git+https://example.com/demo-ext.git"
            ),
        ],
    )

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["ext", "list"])

    assert result.exit_code == 0
    assert "artel-mcp vbundled" in result.output
    assert "demo-ext v1.2.3" in result.output
