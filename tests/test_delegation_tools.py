from __future__ import annotations

from artel_ai.models import Done, TextDelta, Usage
from artel_core.agent import AgentSession
from artel_core.bootstrap import RuntimeBootstrap
from artel_core.config import ArtelConfig
from artel_core.delegation.registry import get_registry, reset_registry
from artel_core.execution import ToolExecutionContext, bind_tool_execution_context
from artel_core.extensions import ExtensionContext, HookDispatcher
from conftest import MockProvider


async def _fake_bootstrap_runtime(*args, **kwargs) -> RuntimeBootstrap:
    return RuntimeBootstrap(
        provider_name="mock",
        model_id="mock-model",
        provider=MockProvider(
            responses=[[TextDelta(content="Delegated answer."), Done(usage=Usage())]]
        ),
        tools=[],
        hooks=HookDispatcher(),
        extensions=[],
        context_window=0,
        input_price_per_m=0.0,
        output_price_per_m=0.0,
    )


async def test_delegate_tools_run_and_wait(tmp_path, monkeypatch) -> None:
    import artel_core.delegation.service as service_mod
    from artel_core.delegation.service import DelegationService
    from artel_core.delegation.tools import (
        CancelDelegateTool,
        DelegateTaskTool,
        GetDelegateTool,
        ListDelegatesTool,
    )

    reset_registry()
    monkeypatch.setattr(service_mod, "bootstrap_runtime", _fake_bootstrap_runtime)

    def service_factory():
        return DelegationService(
            ExtensionContext(project_dir=str(tmp_path), runtime="local", config=ArtelConfig())
        )  # noqa: E731

    spawn_tool = DelegateTaskTool(service_factory)
    list_tool = ListDelegatesTool(service_factory)
    get_tool = GetDelegateTool(service_factory)
    cancel_tool = CancelDelegateTool(service_factory)
    parent_session = AgentSession(
        provider=MockProvider(),
        model="parent-model",
        tools=[],
        project_dir=str(tmp_path),
        session_id="parent-session",
    )

    with bind_tool_execution_context(
        ToolExecutionContext(
            session=parent_session, tool_name="delegate_task", tool_call_id="tool-1", arguments={}
        )
    ):
        result = await spawn_tool.execute(task="Inspect the repo", wait=True)

    assert "status: completed" in result
    assert "Delegated answer." in result

    with bind_tool_execution_context(
        ToolExecutionContext(
            session=parent_session, tool_name="list_delegates", tool_call_id="tool-2", arguments={}
        )
    ):
        listed = await list_tool.execute()

    assert "Delegates:" in listed
    assert "Inspect the repo" in listed

    run_id = get_registry().list_runs("parent-session")[0].id
    with bind_tool_execution_context(
        ToolExecutionContext(
            session=parent_session, tool_name="get_delegate", tool_call_id="tool-3", arguments={}
        )
    ):
        detail = await get_tool.execute(run_id=run_id)

    assert f"- id: {run_id}" in detail
    assert "Delegated answer." in detail

    with bind_tool_execution_context(
        ToolExecutionContext(
            session=parent_session, tool_name="cancel_delegate", tool_call_id="tool-4", arguments={}
        )
    ):
        cancelled = await cancel_tool.execute(run_id=run_id)

    assert "already finished" in cancelled.lower() or "unknown" in cancelled.lower()
