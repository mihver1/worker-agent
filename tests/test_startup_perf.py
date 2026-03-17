from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace


def test_ensure_managed_local_server_does_not_block_on_tray_bootstrap(tmp_path, monkeypatch):
    import artel_tui.local_server as local_server_mod
    import artel_tui.server_tray as tray_mod

    config = SimpleNamespace(server=SimpleNamespace(auth_token="artel_configured", port=7432))
    tray_started = threading.Event()

    class _Process:
        pid = 4321
        returncode = None

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        return _Process()

    async def fake_wait_until_ready(handle, process):
        return None

    def slow_ensure_server_tray(project_dir: str) -> None:
        tray_started.set()
        time.sleep(0.5)

    monkeypatch.setattr(local_server_mod, "load_config", lambda _: config)
    monkeypatch.setattr(local_server_mod, "_pick_port", lambda preferred_port: 9011)
    monkeypatch.setattr(local_server_mod, "_wait_until_ready", fake_wait_until_ready)
    monkeypatch.setattr(local_server_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(local_server_mod.sys, "platform", "darwin")
    monkeypatch.setattr(tray_mod, "ensure_server_tray", slow_ensure_server_tray)

    started = time.perf_counter()
    handle = asyncio.run(local_server_mod.ensure_managed_local_server(str(tmp_path)))
    elapsed = time.perf_counter() - started

    assert handle.remote_url == "ws://127.0.0.1:9011"
    assert elapsed < 0.2
    assert tray_started.wait(0.2)
