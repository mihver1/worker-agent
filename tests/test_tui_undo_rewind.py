from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_local_undo_uses_last_ai_changed_paths(monkeypatch):
    from artel_tui.app import ArtelApp

    app = ArtelApp()
    app._session = type("_Session", (), {"messages": [object()]})()
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]
    monkeypatch.setattr(
        "artel_tui.app.collect_last_ai_changed_paths", lambda msgs: ["a.py", "b.py"]
    )
    monkeypatch.setattr(
        "artel_tui.app.restore_paths",
        lambda *, cwd, paths: "Restored 2 files:\n  - a.py\n  - b.py",
    )

    await app._cmd_undo()

    assert messages[-1][0] == "tool"
    assert "Undid latest AI file changes" in messages[-1][1]
    assert "a.py" in messages[-1][1]


@pytest.mark.asyncio
async def test_local_rewind_forks_and_resumes(monkeypatch):
    from artel_tui.app import ArtelApp

    app = ArtelApp()
    app._session = type("_Session", (), {"session_id": "s1"})()
    app._store = type("_Store", (), {})()
    calls: list[tuple[str, object]] = []
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]

    async def fake_fork(source_id: str, new_id: str, up_to_message_idx=None, title=""):
        calls.append((source_id, up_to_message_idx))

    async def fake_resume(new_id: str):
        calls.append(("resume", new_id))

    app._store.fork_session = fake_fork  # type: ignore[attr-defined]
    app._resume_session = fake_resume  # type: ignore[method-assign]

    await app._cmd_rewind("3")

    assert calls[0] == ("s1", 3)
    assert calls[1][0] == "resume"
    assert messages[-1] == ("tool", "Rewound session to message 3.")


@pytest.mark.asyncio
async def test_remote_rewind_forks_and_switches(monkeypatch):
    from artel_tui.app import ArtelApp

    app = ArtelApp(remote_url="ws://127.0.0.1:7432")
    app._remote_session_id = "s1"
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]
    seen: list[tuple[str, object]] = []

    class _Control:
        async def fork_session(self, session_id: str, *, message_index=None):
            seen.append((session_id, message_index))
            return {"session_id": "forked-1"}

    async def fake_resume_remote(session_id: str):
        seen.append(("resume", session_id))

    app._remote_control = lambda: _Control()  # type: ignore[method-assign]
    app._resume_remote_session = fake_resume_remote  # type: ignore[method-assign]

    await app._cmd_rewind("5")

    assert seen == [("s1", 5), ("resume", "forked-1")]
    assert messages[-1] == ("tool", "Rewound session to message 5.")
