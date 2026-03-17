from __future__ import annotations


def test_orchestration_module_reexports_delegation_surface() -> None:
    from artel_core.orchestration import (
        OrchestrationRegistry,
        OrchestrationRun,
        OrchestrationService,
        format_orchestration_list,
        get_orchestration_registry,
    )

    registry = get_orchestration_registry()
    assert isinstance(registry, OrchestrationRegistry)
    run = registry.create_run(
        parent_session_id="session-1",
        task="Inspect orchestration surface",
        context="",
        model="mock/mock-model",
        project_dir="/tmp/project",
        mode="readonly",
    )
    assert isinstance(run, OrchestrationRun)
    assert callable(format_orchestration_list)
    assert OrchestrationService is not None
