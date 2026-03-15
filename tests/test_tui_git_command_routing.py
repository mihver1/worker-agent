from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_handle_command_routes_git_aliases(monkeypatch):
    from worker_tui.app import WorkerApp

    app = WorkerApp()
    seen: list[tuple[str, str]] = []

    async def fake_cmd_git(cmd: str, arg: str) -> None:
        seen.append((cmd, arg))

    monkeypatch.setattr(app, "_cmd_git", fake_cmd_git)

    await app._handle_command("/status")
    await app._handle_command("/diff app.py")
    await app._handle_command("/rollback --all")

    assert seen == [
        ("/status", ""),
        ("/diff", "app.py"),
        ("/rollback", "--all"),
    ]
