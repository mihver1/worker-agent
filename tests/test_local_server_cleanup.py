"""Tests for stray managed server cleanup."""

from __future__ import annotations

import asyncio


def test_kill_managed_server_processes_includes_registry_pid_when_discovery_empty(
    tmp_path, monkeypatch
):
    import artel_tui.local_server as local_server_mod

    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(local_server_mod, "_managed_server_processes", lambda project_dir: [])
    monkeypatch.setattr(local_server_mod.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    result = local_server_mod._kill_managed_server_processes(str(tmp_path), include_pid=4321)

    assert result == [4321]
    assert killed == [(4321, local_server_mod.signal.SIGTERM)]


async def _run_stop(tmp_path, monkeypatch):
    import artel_tui.local_server as local_server_mod

    existing = local_server_mod.LocalServerHandle(
        remote_url="ws://127.0.0.1:9011",
        auth_token="artel_existing",
        project_dir=str(tmp_path),
        pid=777,
    )
    local_server_mod._save_registry(existing)
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(
        local_server_mod, "_managed_server_processes", lambda project_dir: [1001, 1002]
    )
    monkeypatch.setattr(local_server_mod.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    await local_server_mod.stop_managed_local_server(str(tmp_path))
    return killed


def test_stop_managed_local_server_kills_discovered_strays_and_registry_pid(tmp_path, monkeypatch):
    killed = asyncio.run(_run_stop(tmp_path, monkeypatch))

    assert killed == [
        (777, 15),
        (1001, 15),
        (1002, 15),
    ]
    assert not (tmp_path / ".artel" / "server.json").exists()
