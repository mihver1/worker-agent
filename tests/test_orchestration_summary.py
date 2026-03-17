from __future__ import annotations

from artel_core.delegation.formatting import format_run_list
from artel_core.delegation.registry import get_registry, reset_registry


def test_format_run_list_includes_counts_and_latest_updates() -> None:
    reset_registry()
    registry = get_registry()
    run1 = registry.create_run(
        parent_session_id="session-1",
        task="Inspect src/app.py",
        context="",
        model="mock/mock-model",
        project_dir="/tmp/project",
        mode="readonly",
    )
    registry.mark_running(run1.id)
    registry.append_event(run1.id, "tool read")

    run2 = registry.create_run(
        parent_session_id="session-1",
        task="Run tests",
        context="",
        model="mock/mock-model",
        project_dir="/tmp/project",
        mode="inherit",
    )
    registry.mark_completed(run2.id, "All green")

    rendered = format_run_list(registry.list_runs("session-1"))

    assert "Delegates: 2 total (completed=1, running=1)" in rendered
    assert f"- {run1.id} [running] (readonly) Inspect src/app.py — tool read" in rendered
    assert f"- {run2.id} [completed] (inherit) Run tests — All green" in rendered
