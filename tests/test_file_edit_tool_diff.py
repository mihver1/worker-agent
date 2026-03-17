from __future__ import annotations

import pytest
from artel_core.execution import ToolExecutionContext, bind_tool_execution_context
from artel_core.tools.builtins import EditTool, WriteTool


class _SessionStub:
    project_dir = "."


@pytest.mark.asyncio
async def test_write_tool_populates_diff_display_payload(tmp_path):
    tool = WriteTool(str(tmp_path))
    ctx = ToolExecutionContext(
        session=_SessionStub(), tool_name="write", tool_call_id="tc1", arguments={}
    )

    with bind_tool_execution_context(ctx):
        result = await tool.execute(path="demo.py", content="print('hello')\n")

    assert "demo.py" in result
    assert ctx.display_payload is not None
    assert ctx.display_payload["kind"] == "file_diff"
    assert ctx.display_payload["path"] == "demo.py"
    assert ctx.display_payload["added_lines"] >= 1


@pytest.mark.asyncio
async def test_edit_tool_populates_diff_display_payload(tmp_path):
    path = tmp_path / "demo.py"
    path.write_text("print('old')\n", encoding="utf-8")
    tool = EditTool(str(tmp_path))
    ctx = ToolExecutionContext(
        session=_SessionStub(), tool_name="edit", tool_call_id="tc1", arguments={}
    )

    with bind_tool_execution_context(ctx):
        result = await tool.execute(path="demo.py", search="print('old')", replace="print('new')")

    assert result == f"Applied edit to {path}"
    assert ctx.display_payload is not None
    assert ctx.display_payload["kind"] == "file_diff"
    assert ctx.display_payload["removed_lines"] >= 1
    assert ctx.display_payload["added_lines"] >= 1
