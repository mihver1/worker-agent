"""Tests for managed local server idempotency."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


class TestManagedLocalServerIdempotency:
    @pytest.mark.asyncio
    async def test_concurrent_ensure_reuses_single_started_server(self, tmp_path, monkeypatch):
        import artel_tui.local_server as local_server_mod

        config = SimpleNamespace(server=SimpleNamespace(auth_token="artel_configured", port=7432))
        started: list[list[str]] = []
        wait_started = asyncio.Event()
        release_wait = asyncio.Event()

        class _Process:
            def __init__(self, pid: int):
                self.pid = pid
                self.returncode = None

            def poll(self):
                return None

        def fake_popen(command, **kwargs):
            started.append(command)
            return _Process(4000 + len(started))

        async def fake_wait_until_ready(handle, process):
            wait_started.set()
            await release_wait.wait()

        async def fake_server_matches_project(handle):
            return bool(started)

        monkeypatch.setattr(local_server_mod, "load_config", lambda _: config)
        monkeypatch.setattr(local_server_mod, "_pick_port", lambda preferred_port: 9011)
        monkeypatch.setattr(local_server_mod, "_wait_until_ready", fake_wait_until_ready)
        monkeypatch.setattr(
            local_server_mod, "_server_matches_project", fake_server_matches_project
        )
        monkeypatch.setattr(local_server_mod.subprocess, "Popen", fake_popen)

        first = asyncio.create_task(
            local_server_mod.ensure_managed_local_server(str(tmp_path), ensure_tray=False)
        )
        await wait_started.wait()
        second = asyncio.create_task(
            local_server_mod.ensure_managed_local_server(str(tmp_path), ensure_tray=False)
        )
        release_wait.set()

        handle1 = await first
        handle2 = await second

        assert len(started) == 1
        assert handle1.remote_url == handle2.remote_url
        assert handle1.pid == handle2.pid
