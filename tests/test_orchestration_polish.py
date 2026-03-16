from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from worker_core.delegation.registry import get_registry, reset_registry


def test_registry_publishes_latest_update_and_completion_events() -> None:
    reset_registry()
    registry = get_registry()
    queue = registry.subscribe()
    run = registry.create_run(
        parent_session_id="session-1",
        task="Inspect event stream",
        context="",
        model="mock/mock-model",
        project_dir="/tmp/project",
        mode="readonly",
    )
    created = queue.get_nowait()
    assert created["type"] == "created"

    registry.mark_running(run.id)
    updated = queue.get_nowait()
    assert updated["type"] == "updated"
    assert updated["run"]["latest_update"] == "started"

    registry.append_event(run.id, "tool read")
    updated = queue.get_nowait()
    assert updated["run"]["latest_update"] == "tool read"

    registry.mark_completed(run.id, "done result")
    completed = queue.get_nowait()
    assert completed["type"] == "completed"
    assert completed["run"]["latest_update"] == "done result"

    registry.unsubscribe(queue)


@pytest.mark.asyncio
async def test_tui_auto_surfaces_completed_delegation(monkeypatch):
    from worker_tui.app import WorkerApp

    app = WorkerApp()
    seen_messages: list[tuple[str, str]] = []
    monkeypatch.setattr(
        app, "_add_message", lambda content, role="assistant": seen_messages.append((content, role))
    )

    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    task = asyncio.create_task(app._consume_delegation_events(queue))
    await queue.put(
        {
            "type": "completed",
            "run": {
                "id": "run-1",
                "task": "Inspect src",
                "result_preview": "Found the issue in src/app.py",
            },
        }
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert seen_messages == [
        ("✅ Delegation completed: Inspect src\nFound the issue in src/app.py", "tool")
    ]


def test_widget_renders_latest_update() -> None:
    from worker_tui.delegation_widget import DelegationStatusWidget

    widget = DelegationStatusWidget(
        SimpleNamespace(remote_url="", _session=SimpleNamespace(session_id="s1"))
    )
    rendered = widget._render_text(
        [
            {
                "id": "run-1",
                "status": "running",
                "task": "Inspect src/app.py",
                "latest_update": "tool read",
            }
        ]
    )

    assert "Orchestration:" in rendered
    assert "Inspect src/app.py" in rendered
    assert "tool read" in rendered
