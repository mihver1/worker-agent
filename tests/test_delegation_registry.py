from __future__ import annotations

from artel_core.delegation.registry import get_registry, reset_registry


def test_registry_tracks_run_lifecycle() -> None:
    reset_registry()
    registry = get_registry()
    run = registry.create_run(
        parent_session_id="session-1",
        task="Inspect repository structure",
        context="",
        model="mock/mock-model",
        project_dir="/tmp/project",
        mode="readonly",
    )

    registry.mark_running(run.id)
    registry.append_event(run.id, "tool read")
    registry.mark_completed(run.id, "Found the relevant files.")

    runs = registry.list_runs("session-1")

    assert [item.id for item in runs] == [run.id]
    assert runs[0].status == "completed"
    assert runs[0].events == ["tool read"]
    assert runs[0].result == "Found the relevant files."
