"""Tests for the agent session loop."""

from __future__ import annotations

import pytest
from artel_ai.models import Done, TextDelta, ToolCallDelta, Usage
from artel_core.agent import AgentEventType, AgentSession
from artel_core.config import PermissionsConfig
from artel_core.tools.builtins import create_builtin_tools
from conftest import MockProvider


@pytest.mark.asyncio
async def test_simple_text_response():
    """Agent returns text without tool calls → single turn."""
    provider = MockProvider(
        responses=[
            [
                TextDelta(content="Hello "),
                TextDelta(content="World"),
                Done(usage=Usage(input_tokens=10, output_tokens=5)),
            ],
        ]
    )
    session = AgentSession(provider=provider, model="test-model", tools=[])

    events = []
    async for event in session.run("hi"):
        events.append(event)

    types = [e.type for e in events]
    assert AgentEventType.TEXT_DELTA in types
    assert AgentEventType.DONE in types
    assert types[-1] == AgentEventType.DONE

    # Check content assembled
    text = "".join(e.content for e in events if e.type == AgentEventType.TEXT_DELTA)
    assert text == "Hello World"

    # Provider was called once
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_tool_call_loop(tmp_workdir):
    """Agent calls a tool → gets result → responds."""
    provider = MockProvider(
        responses=[
            # Turn 1: model asks to read a file
            [
                ToolCallDelta(id="tc_1", name="read", arguments={"path": "hello.txt"}),
                Done(usage=Usage(input_tokens=20, output_tokens=10)),
            ],
            # Turn 2: model responds with text
            [
                TextDelta(content="The file says hello."),
                Done(usage=Usage(input_tokens=30, output_tokens=15)),
            ],
        ]
    )

    tools = create_builtin_tools(tmp_workdir)
    session = AgentSession(provider=provider, model="test-model", tools=tools)

    events = []
    async for event in session.run("read hello.txt"):
        events.append(event)

    types = [e.type for e in events]
    assert AgentEventType.TOOL_CALL in types
    assert AgentEventType.TOOL_RESULT in types
    assert AgentEventType.TEXT_DELTA in types
    assert AgentEventType.DONE in types

    # Provider called twice (tool call + follow-up)
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_unknown_tool():
    """Agent tries to call a tool that doesn't exist."""
    provider = MockProvider(
        responses=[
            [
                ToolCallDelta(id="tc_1", name="nonexistent", arguments={}),
                Done(usage=Usage()),
            ],
            [
                TextDelta(content="Sorry, that tool doesn't exist."),
                Done(usage=Usage()),
            ],
        ]
    )
    session = AgentSession(provider=provider, model="test-model", tools=[])

    events = []
    async for event in session.run("test"):
        events.append(event)

    results = [e for e in events if e.type == AgentEventType.TOOL_RESULT]
    assert len(results) == 1
    assert "Unknown tool" in results[0].content


@pytest.mark.asyncio
async def test_max_turns_limit():
    """Agent should stop after max_turns iterations."""
    # Always return a tool call → infinite loop without max_turns
    responses = [
        [
            ToolCallDelta(id=f"tc_{i}", name="read", arguments={"path": "hello.txt"}),
            Done(usage=Usage()),
        ]
        for i in range(10)
    ]
    provider = MockProvider(responses=responses)
    tools = create_builtin_tools("/tmp")
    session = AgentSession(provider=provider, model="test-model", tools=tools, max_turns=3)

    events = []
    async for event in session.run("loop forever"):
        events.append(event)

    # Should hit the max turns error
    errors = [e for e in events if e.type == AgentEventType.ERROR]
    assert len(errors) == 1
    assert "maximum" in errors[0].error.lower()


@pytest.mark.asyncio
async def test_session_preserves_history():
    """Session should accumulate messages across runs."""
    provider = MockProvider(
        responses=[
            [TextDelta(content="resp1"), Done(usage=Usage())],
            [TextDelta(content="resp2"), Done(usage=Usage())],
        ]
    )
    session = AgentSession(provider=provider, model="test-model", tools=[])

    async for _ in session.run("msg1"):
        pass
    async for _ in session.run("msg2"):
        pass

    # Should have: system + user1 + assistant1 + user2 + assistant2
    assert len(session.messages) == 5
    assert session.messages[0].role.value == "system"
    assert session.messages[1].content == "msg1"
    assert session.messages[3].content == "msg2"


@pytest.mark.asyncio
async def test_permission_denied_tool_call(tmp_workdir):
    """Denied permissions should block tool execution and return an error result."""
    provider = MockProvider(
        responses=[
            [
                ToolCallDelta(
                    id="tc_1",
                    name="write",
                    arguments={"path": "blocked.txt", "content": "secret"},
                ),
                Done(usage=Usage()),
            ],
            [TextDelta(content="handled"), Done(usage=Usage())],
        ]
    )
    tools = create_builtin_tools(tmp_workdir)
    session = AgentSession(
        provider=provider,
        model="test-model",
        tools=tools,
        permissions_config=PermissionsConfig(write="deny"),
    )

    events = []
    async for event in session.run("create file"):
        events.append(event)

    tool_results = [e for e in events if e.type == AgentEventType.TOOL_RESULT]
    assert len(tool_results) == 1
    assert "denied" in tool_results[0].content.lower()
