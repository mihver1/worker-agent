from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_local_git_status_command_renders_summary(monkeypatch, tmp_path):
    from worker_tui.app import WorkerApp

    app = WorkerApp()
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]
    monkeypatch.setattr(
        "worker_tui.app.render_git_status", lambda *, cwd: "Git status\nModified (1):\n  - app.py"
    )
    monkeypatch.chdir(tmp_path)

    await app._cmd_git("/git", "status")

    assert messages[-1][0] == "tool"
    assert "Modified (1):" in messages[-1][1]


@pytest.mark.asyncio
async def test_remote_git_diff_command_uses_bash_endpoint(monkeypatch):
    from worker_tui.app import WorkerApp

    app = WorkerApp(remote_url="ws://127.0.0.1:7432")
    app._remote_session_id = "sess-1"
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]

    class _Control:
        async def run_bash(self, session_id: str, command: str):
            assert session_id == "sess-1"
            assert command == "git diff -- app.py"
            return {"output": "diff --git a/app.py b/app.py\n+print(1)", "exit_code": 0}

    app._remote_control = lambda: _Control()  # type: ignore[method-assign]

    await app._cmd_git("/diff", "app.py")

    assert messages[-1][0] == "tool"
    assert "```diff" in messages[-1][1]


@pytest.mark.asyncio
async def test_local_git_rollback_all_reports_result(monkeypatch, tmp_path):
    from worker_tui.app import WorkerApp

    app = WorkerApp()
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]
    monkeypatch.setattr(
        "worker_tui.app.restore_all", lambda *, cwd: "Restored all unstaged changes."
    )
    monkeypatch.chdir(tmp_path)

    await app._cmd_git("/rollback", "--all")

    assert messages[-1] == ("tool", "Restored all unstaged changes.")
