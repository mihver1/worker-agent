"""Tests for advanced agent features: steering, follow-up, abort, thinking levels, context files."""

from __future__ import annotations

import pytest
from conftest import MockProvider
from worker_ai.models import Done, TextDelta, ToolCallDelta, Usage
from worker_core.agent import AgentEventType, AgentSession

# ── Steering ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_steering_interrupts_tool_execution(tmp_workdir):
    """Steering message should skip remaining tools and be injected."""
    from worker_core.tools.builtins import create_builtin_tools

    provider = MockProvider(
        responses=[
            # Turn 1: model requests two tool calls
            [
                ToolCallDelta(id="tc_1", name="read", arguments={"path": "hello.txt"}),
                ToolCallDelta(id="tc_2", name="read", arguments={"path": "hello.txt"}),
                Done(usage=Usage()),
            ],
            # Turn 2: model sees steering message and responds
            [TextDelta(content="OK, steering received."), Done(usage=Usage())],
        ]
    )
    tools = create_builtin_tools(tmp_workdir)
    session = AgentSession(provider=provider, model="test-model", tools=tools)

    # Queue steering before first tool completes (simulated)
    session.steer("stop and do this instead")

    events = []
    async for event in session.run("read two files"):
        events.append(event)

    # Steering message should have been injected
    user_msgs = [m for m in session.messages if m.role.value == "user"]
    contents = [m.content for m in user_msgs]
    assert "stop and do this instead" in contents


@pytest.mark.asyncio
async def test_follow_up_after_completion():
    """Follow-up messages should be processed after agent finishes."""
    provider = MockProvider(
        responses=[
            [TextDelta(content="first response"), Done(usage=Usage())],
            [TextDelta(content="follow-up response"), Done(usage=Usage())],
        ]
    )
    session = AgentSession(provider=provider, model="test-model", tools=[])
    session.follow_up("and also do this")

    events = []
    async for event in session.run("do something"):
        events.append(event)

    text = "".join(e.content for e in events if e.type == AgentEventType.TEXT_DELTA)
    assert "first response" in text
    assert "follow-up response" in text
    assert len(provider.calls) == 2


@pytest.mark.asyncio
async def test_abort_stops_loop():
    """Abort should stop the agent loop."""
    from worker_core.tools.builtins import create_builtin_tools

    provider = MockProvider(
        responses=[
            [
                ToolCallDelta(id="tc_1", name="read", arguments={"path": "hello.txt"}),
                Done(usage=Usage()),
            ],
        ]
        * 10
    )
    session = AgentSession(
        provider=provider,
        model="test-model",
        tools=create_builtin_tools("/tmp"),
        max_turns=10,
    )

    events = []
    turn_count = 0
    async for event in session.run("loop"):
        events.append(event)
        if event.type == AgentEventType.TOOL_RESULT:
            turn_count += 1
            if turn_count >= 2:
                session.abort()

    errors = [e for e in events if e.type == AgentEventType.ERROR]
    assert len(errors) >= 1
    assert "Aborted" in errors[0].error
    # Should have stopped well before max_turns
    assert len(provider.calls) < 10


# ── Thinking levels ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_thinking_level_passed_to_provider():
    """Thinking level should be forwarded to provider.stream_chat."""
    provider = MockProvider(
        responses=[
            [TextDelta(content="ok"), Done(usage=Usage())],
        ]
    )
    session = AgentSession(provider=provider, model="test-model", tools=[], thinking_level="high")
    async for _ in session.run("test"):
        pass

    # MockProvider captures calls but doesn't check thinking_level in kwargs yet.
    # At minimum, verify it doesn't crash.
    assert len(provider.calls) == 1


# ── Context file walk-up ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_files_walk_up(tmp_path):
    """Should load AGENTS.md from parent directories."""
    # Create parent/AGENTS.md
    parent = tmp_path / "parent"
    parent.mkdir()
    (parent / "AGENTS.md").write_text("Parent context instructions")

    # Create parent/child/.artel/AGENTS.md
    child = parent / "child"
    artel_dir = child / ".artel"
    artel_dir.mkdir(parents=True)
    (artel_dir / "AGENTS.md").write_text("Child project instructions")

    provider = MockProvider()
    session = AgentSession(provider=provider, model="test-model", tools=[], project_dir=str(child))

    assert "Parent context instructions" in session.system_prompt
    assert "Child project instructions" in session.system_prompt


@pytest.mark.asyncio
async def test_system_md_override(tmp_path):
    """SYSTEM.md should replace default system prompt."""
    artel_dir = tmp_path / ".artel"
    artel_dir.mkdir()
    (artel_dir / "SYSTEM.md").write_text("You are a custom agent.")

    provider = MockProvider()
    session = AgentSession(
        provider=provider, model="test-model", tools=[], project_dir=str(tmp_path)
    )

    assert "You are a custom agent." in session.system_prompt
    assert "Artel" not in session.system_prompt  # Default replaced


@pytest.mark.asyncio
async def test_claude_md_also_loaded(tmp_path):
    """CLAUDE.md should be loaded as context file too."""
    (tmp_path / "CLAUDE.md").write_text("Claude-specific instructions")

    provider = MockProvider()
    session = AgentSession(
        provider=provider, model="test-model", tools=[], project_dir=str(tmp_path)
    )

    assert "Claude-specific instructions" in session.system_prompt
