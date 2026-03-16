"""Built-in tools for single-window Artel delegation."""

from __future__ import annotations

from typing import Any

from worker_ai.models import ToolDef, ToolParam

from worker_core.delegation.formatting import format_run_detail, format_run_list, format_run_summary
from worker_core.execution import get_current_tool_execution_context
from worker_core.tools import Tool


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class DelegateTaskTool(Tool):
    name = "delegate_task"
    description = (
        "Delegate a focused task to an in-process Artel subagent running in the current window."
    )

    def __init__(self, service_factory: Any):
        self._service_factory = service_factory

    async def execute(self, **kwargs: Any) -> str:
        ctx = get_current_tool_execution_context()
        if ctx is None:
            return "Error: delegate_task requires an active agent session."
        task = str(kwargs.get("task", "")).strip()
        if not task:
            return "Error: Missing task."
        service = self._service_factory()
        run = await service.spawn(
            ctx.session,
            task=task,
            context=str(kwargs.get("context", "")),
            model=str(kwargs.get("model", "")),
            project_dir=str(kwargs.get("project_dir", "")),
            mode=str(kwargs.get("mode", "readonly")),
            wait=_coerce_bool(kwargs.get("wait", False)),
        )
        if _coerce_bool(kwargs.get("wait", False)):
            return format_run_detail(run)
        return format_run_summary(run)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="task", type="string", description="Task to delegate."),
                ToolParam(
                    name="context",
                    type="string",
                    description="Optional extra context.",
                    required=False,
                ),
                ToolParam(
                    name="model",
                    type="string",
                    description="Optional model override.",
                    required=False,
                ),
                ToolParam(
                    name="project_dir",
                    type="string",
                    description="Optional project directory override.",
                    required=False,
                ),
                ToolParam(
                    name="mode",
                    type="string",
                    description="readonly or inherit (default: readonly).",
                    required=False,
                ),
                ToolParam(
                    name="wait",
                    type="boolean",
                    description="Wait for completion before returning.",
                    required=False,
                ),
            ],
        )


class ListDelegatesTool(Tool):
    name = "list_delegates"
    description = "List delegated runs spawned by the current session."

    def __init__(self, service_factory: Any):
        self._service_factory = service_factory

    async def execute(self, **kwargs: Any) -> str:
        ctx = get_current_tool_execution_context()
        if ctx is None:
            return "Error: list_delegates requires an active agent session."
        service = self._service_factory()
        runs = service.list_for_session(ctx.session.session_id)
        status_filter = str(kwargs.get("status", "")).strip().lower()
        if status_filter:
            runs = [run for run in runs if run.status == status_filter]
        return format_run_list(runs)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(
                    name="status",
                    type="string",
                    description="Optional status filter.",
                    required=False,
                )
            ],
        )


class GetDelegateTool(Tool):
    name = "get_delegate"
    description = "Show status or final result for a delegated run."

    def __init__(self, service_factory: Any):
        self._service_factory = service_factory

    async def execute(self, **kwargs: Any) -> str:
        ctx = get_current_tool_execution_context()
        if ctx is None:
            return "Error: get_delegate requires an active agent session."
        run_id = str(kwargs.get("run_id", "")).strip()
        if not run_id:
            return "Error: Missing run_id."
        service = self._service_factory()
        if _coerce_bool(kwargs.get("wait", False)):
            run = await service.wait_for_session_run(ctx.session.session_id, run_id)
        else:
            run = service.get_for_session(ctx.session.session_id, run_id)
            if run is None:
                return f"Error: Unknown delegate: {run_id}"
        return format_run_detail(run)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="run_id", type="string", description="Delegated run ID."),
                ToolParam(
                    name="wait",
                    type="boolean",
                    description="Wait for completion before returning.",
                    required=False,
                ),
            ],
        )


class CancelDelegateTool(Tool):
    name = "cancel_delegate"
    description = "Cancel a running delegated task spawned by the current session."

    def __init__(self, service_factory: Any):
        self._service_factory = service_factory

    async def execute(self, **kwargs: Any) -> str:
        ctx = get_current_tool_execution_context()
        if ctx is None:
            return "Error: cancel_delegate requires an active agent session."
        run_id = str(kwargs.get("run_id", "")).strip()
        if not run_id:
            return "Error: Missing run_id."
        service = self._service_factory()
        if not service.cancel_for_session(ctx.session.session_id, run_id):
            return f"Error: Unknown or already finished delegate: {run_id}"
        run = service.get_for_session(ctx.session.session_id, run_id)
        if run is None:
            return f"Cancelled delegate: {run_id}"
        return format_run_detail(run)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[ToolParam(name="run_id", type="string", description="Delegated run ID.")],
        )


__all__ = [
    "CancelDelegateTool",
    "DelegateTaskTool",
    "GetDelegateTool",
    "ListDelegatesTool",
]
