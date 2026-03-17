from __future__ import annotations

import pytest
from artel_ai.models import Message, Role, ToolResult
from artel_core.sessions import SessionStore


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test_sessions.db")
    s = SessionStore(db_path)
    await s.open()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_tool_result_display_round_trip(store):
    await store.create_session("s1", "test-model")
    message = Message(
        role=Role.TOOL,
        tool_result=ToolResult(
            tool_call_id="tc1",
            content="Applied edit to file",
            is_error=False,
            display={
                "kind": "file_diff",
                "path": "demo.py",
                "added_lines": 1,
                "removed_lines": 1,
                "diff": "@@\n-old\n+new",
            },
        ),
    )
    await store.add_message("s1", message)

    loaded = await store.get_messages("s1")
    assert loaded[0].tool_result is not None
    assert loaded[0].tool_result.display is not None
    assert loaded[0].tool_result.display["path"] == "demo.py"
