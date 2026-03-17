"""Tests for runtime-aware extension loading and tool execution context."""

from __future__ import annotations

import pytest
from artel_ai.models import Done, ToolCallDelta, ToolDef, Usage
from artel_core import extensions as extensions_mod
from artel_core.agent import AgentEventType, AgentSession
from artel_core.execution import get_current_tool_execution_context
from artel_core.extensions import Extension, ExtensionContext, load_extensions_async
from artel_core.tools import Tool
from conftest import MockProvider


class ContextAwareTool(Tool):
    name = "context_tool"
    description = "Return current execution context."

    async def execute(self, **kwargs) -> str:
        ctx = get_current_tool_execution_context()
        assert ctx is not None
        return f"{ctx.tool_name}|{ctx.tool_call_id}|{ctx.session.project_dir}"

    def definition(self) -> ToolDef:
        return ToolDef(name=self.name, description=self.description, parameters=[])


@pytest.mark.asyncio
async def test_load_extensions_async_binds_runtime_context(monkeypatch):
    class ContextExtension(Extension):
        name = "ctx"

        def __init__(self):
            self.loaded_runtime = ""
            self.loaded_project_dir = ""

        async def on_load(self) -> None:
            assert self.context is not None
            self.loaded_runtime = self.context.runtime
            self.loaded_project_dir = self.context.project_dir

    monkeypatch.setattr(
        extensions_mod,
        "discover_extensions",
        lambda group="artel.extensions": {"ctx": ContextExtension},
    )

    context = ExtensionContext(project_dir="/tmp/project", runtime="server")
    instances, dispatcher = await load_extensions_async(context=context)

    assert len(instances) == 1
    assert instances[0].context == context
    assert instances[0].loaded_runtime == "server"
    assert instances[0].loaded_project_dir == "/tmp/project"
    assert dispatcher.commands == {}


@pytest.mark.asyncio
async def test_tool_execution_context_is_available_inside_tool(tmp_path):
    provider = MockProvider(
        responses=[
            [ToolCallDelta(id="tc_1", name="context_tool", arguments={}), Done(usage=Usage())],
            [Done(usage=Usage())],
        ]
    )
    session = AgentSession(
        provider=provider,
        model="m",
        tools=[ContextAwareTool()],
        project_dir=str(tmp_path),
    )

    tool_results = []
    async for event in session.run("invoke context tool"):
        if event.type == AgentEventType.TOOL_RESULT:
            tool_results.append(event.content)

    assert tool_results == [f"context_tool|tc_1|{tmp_path}"]
