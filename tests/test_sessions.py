"""Tests for the SQLite session store."""

from __future__ import annotations

import os

import pytest

from worker_ai.models import Message, Role, ToolCall, ToolResult
from worker_core.sessions import SessionStore


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test_sessions.db")
    s = SessionStore(db_path)
    await s.open()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_and_list_sessions(store):
    await store.create_session("s1", "anthropic/claude-sonnet-4-20250514", title="Test 1")
    await store.create_session("s2", "openai/gpt-4.1", title="Test 2")

    sessions = await store.list_sessions()
    assert len(sessions) == 2
    ids = {s.id for s in sessions}
    assert ids == {"s1", "s2"}


@pytest.mark.asyncio
async def test_add_and_get_messages(store):
    await store.create_session("s1", "test-model")

    msg1 = Message(role=Role.USER, content="Hello")
    msg2 = Message(role=Role.ASSISTANT, content="Hi there!")
    await store.add_message("s1", msg1)
    await store.add_message("s1", msg2)

    messages = await store.get_messages("s1")
    assert len(messages) == 2
    assert messages[0].role == Role.USER
    assert messages[0].content == "Hello"
    assert messages[1].role == Role.ASSISTANT
    assert messages[1].content == "Hi there!"


@pytest.mark.asyncio
async def test_message_with_tool_calls(store):
    await store.create_session("s1", "test-model")

    msg = Message(
        role=Role.ASSISTANT,
        content="Let me read that file.",
        tool_calls=[
            ToolCall(id="tc_1", name="read", arguments={"path": "foo.py"}),
        ],
    )
    await store.add_message("s1", msg)

    messages = await store.get_messages("s1")
    assert len(messages) == 1
    assert messages[0].tool_calls is not None
    assert len(messages[0].tool_calls) == 1
    assert messages[0].tool_calls[0].name == "read"
    assert messages[0].tool_calls[0].arguments == {"path": "foo.py"}


@pytest.mark.asyncio
async def test_message_with_tool_result(store):
    await store.create_session("s1", "test-model")

    msg = Message(
        role=Role.TOOL,
        tool_result=ToolResult(tool_call_id="tc_1", content="file contents here", is_error=False),
    )
    await store.add_message("s1", msg)

    messages = await store.get_messages("s1")
    assert len(messages) == 1
    assert messages[0].tool_result is not None
    assert messages[0].tool_result.tool_call_id == "tc_1"
    assert messages[0].tool_result.content == "file contents here"
    assert messages[0].tool_result.is_error is False


@pytest.mark.asyncio
async def test_delete_session(store):
    await store.create_session("s1", "test-model")
    await store.add_message("s1", Message(role=Role.USER, content="hello"))

    await store.delete_session("s1")

    sessions = await store.list_sessions()
    assert len(sessions) == 0

    messages = await store.get_messages("s1")
    assert len(messages) == 0


@pytest.mark.asyncio
async def test_compact_messages(store):
    await store.create_session("s1", "test-model")
    await store.add_message("s1", Message(role=Role.USER, content="msg1"))
    await store.add_message("s1", Message(role=Role.ASSISTANT, content="resp1"))
    await store.add_message("s1", Message(role=Role.USER, content="msg2"))

    await store.compact_messages("s1", "Summary of conversation")

    messages = await store.get_messages("s1")
    assert len(messages) == 1
    assert messages[0].role == Role.SYSTEM
    assert "Summary of conversation" in messages[0].content


@pytest.mark.asyncio
async def test_message_ordering(store):
    await store.create_session("s1", "test-model")
    for i in range(5):
        await store.add_message("s1", Message(role=Role.USER, content=f"msg_{i}"))

    messages = await store.get_messages("s1")
    assert [m.content for m in messages] == [f"msg_{i}" for i in range(5)]
