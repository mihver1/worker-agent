"""Tests for the macOS server tray helper."""

from __future__ import annotations

import json
import plistlib
from types import SimpleNamespace


def test_tray_registry_path_uses_artel_dir(tmp_path):
    from artel_tui.server_tray import tray_registry_path

    assert tray_registry_path(str(tmp_path)) == tmp_path / ".artel" / "server-tray.json"


def test_launch_agent_plist_path_uses_launchagents_dir(monkeypatch, tmp_path):
    import artel_tui.server_tray as tray_mod

    monkeypatch.setattr(tray_mod.Path, "home", lambda: tmp_path)
    assert (
        tray_mod.launch_agent_plist_path()
        == tmp_path / "Library" / "LaunchAgents" / "dev.artel.server-tray.plist"
    )


def test_ensure_server_tray_bootstraps_launch_agent_on_macos(tmp_path, monkeypatch):
    import artel_tui.server_tray as tray_mod

    monkeypatch.setattr(tray_mod.sys, "platform", "darwin")
    monkeypatch.delenv(tray_mod._ARTEL_SERVER_TRAY_ACTIVE_ENV, raising=False)
    monkeypatch.setattr(tray_mod, "_local_server_running", lambda project_dir: True)
    monkeypatch.setattr(tray_mod.Path, "home", lambda: tmp_path)
    launchctl_calls: list[tuple[str, ...]] = []

    def fake_launchctl(*args: str):
        launchctl_calls.append(args)
        if args[:2] == ("print", f"gui/{tray_mod.os.getuid()}/{tray_mod.LAUNCH_AGENT_LABEL}"):
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(tray_mod, "_launchctl", fake_launchctl)

    handle = tray_mod.ensure_server_tray(str(tmp_path))

    assert handle is not None
    plist_path = tmp_path / "Library" / "LaunchAgents" / "dev.artel.server-tray.plist"
    assert plist_path.exists()
    payload = plistlib.loads(plist_path.read_bytes())
    assert payload["Label"] == tray_mod.LAUNCH_AGENT_LABEL
    assert payload["ProgramArguments"] == tray_mod.tray_command(str(tmp_path))
    assert any(call[0] == "bootstrap" for call in launchctl_calls)
    assert any(call[0] == "kickstart" for call in launchctl_calls)
    saved = json.loads((tmp_path / ".artel" / "server-tray.json").read_text(encoding="utf-8"))
    assert saved["label"] == tray_mod.LAUNCH_AGENT_LABEL


def test_ensure_server_tray_skips_start_when_server_not_running(tmp_path, monkeypatch):
    import artel_tui.server_tray as tray_mod

    monkeypatch.setattr(tray_mod.sys, "platform", "darwin")
    monkeypatch.delenv(tray_mod._ARTEL_SERVER_TRAY_ACTIVE_ENV, raising=False)
    monkeypatch.setattr(tray_mod, "_local_server_running", lambda project_dir: False)
    monkeypatch.setattr(tray_mod.Path, "home", lambda: tmp_path)
    started: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        tray_mod,
        "_launchctl",
        lambda *args: started.append(args) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    handle = tray_mod.ensure_server_tray(str(tmp_path))

    assert handle is None
    assert started == []


def test_stop_server_tray_boots_out_and_removes_registry(tmp_path, monkeypatch):
    import artel_tui.server_tray as tray_mod

    monkeypatch.setattr(tray_mod.sys, "platform", "darwin")
    monkeypatch.setattr(tray_mod.Path, "home", lambda: tmp_path)
    registry = tmp_path / ".artel" / "server-tray.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"project_dir": str(tmp_path)}), encoding="utf-8")
    plist_path = tmp_path / "Library" / "LaunchAgents" / "dev.artel.server-tray.plist"
    plist_path.parent.mkdir(parents=True)
    plist_path.write_text("plist", encoding="utf-8")
    launchctl_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        tray_mod,
        "_launchctl",
        lambda *args: (
            launchctl_calls.append(args) or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )

    tray_mod.stop_server_tray(str(tmp_path))

    assert any(call[0] == "bootout" for call in launchctl_calls)
    assert not registry.exists()
    assert not plist_path.exists()


def test_server_status_text_reports_duplicate_processes(tmp_path, monkeypatch):
    import artel_tui.server_tray as tray_mod

    monkeypatch.setattr(
        "artel_tui.local_server._managed_server_processes", lambda project_dir: [111, 222]
    )
    monkeypatch.setattr("artel_tui.local_server._load_registry", lambda project_dir: None)

    assert tray_mod._server_status_text(str(tmp_path)) == "Server: duplicate processes detected (2)"


def test_clean_duplicate_managed_servers_kills_all_but_one(tmp_path, monkeypatch):
    import artel_tui.local_server as local_server_mod

    monkeypatch.setattr(
        local_server_mod, "_managed_server_processes", lambda project_dir: [100, 200, 300]
    )
    monkeypatch.setattr(
        local_server_mod,
        "_load_registry",
        lambda project_dir: local_server_mod.LocalServerHandle(
            remote_url="ws://127.0.0.1:7432",
            auth_token="tok",
            project_dir=str(tmp_path),
            pid=200,
        ),
    )
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(local_server_mod.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    result = local_server_mod.cleanup_duplicate_managed_servers(str(tmp_path))

    assert result == [100, 300]
    assert killed == [
        (100, local_server_mod.signal.SIGTERM),
        (300, local_server_mod.signal.SIGTERM),
    ]


def test_ensure_managed_local_server_bootstraps_tray_on_macos(tmp_path, monkeypatch):
    import artel_tui.local_server as local_server_mod

    config = SimpleNamespace(server=SimpleNamespace(auth_token="artel_configured", port=7432))
    started_trays: list[str] = []

    class _Process:
        pid = 4321
        returncode = None

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        return _Process()

    async def fake_wait_until_ready(handle, process):
        return None

    monkeypatch.setattr(local_server_mod, "load_config", lambda _: config)
    monkeypatch.setattr(local_server_mod, "_pick_port", lambda preferred_port: 9011)
    monkeypatch.setattr(local_server_mod, "_wait_until_ready", fake_wait_until_ready)
    monkeypatch.setattr(local_server_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(local_server_mod.sys, "platform", "darwin")
    monkeypatch.setattr(
        local_server_mod,
        "_spawn_server_tray_ensure",
        lambda project_dir: started_trays.append(project_dir),
    )

    import asyncio

    handle = asyncio.run(local_server_mod.ensure_managed_local_server(str(tmp_path)))

    assert handle.remote_url == "ws://127.0.0.1:9011"
    assert started_trays == [str(tmp_path)]
