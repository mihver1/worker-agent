"""Tests for Phase 2 session management features."""

from __future__ import annotations

import pytest
from conftest import MockProvider
from worker_ai.models import Message, Role
from worker_core.agent import AgentEventType, AgentSession
from worker_core.sessions import SessionStore

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
async def store(tmp_path):
    db_path = str(tmp_path / "test_sessions.db")
    s = SessionStore(db_path)
    await s.open()
    yield s
    await s.close()


# ── SessionStore: get_session ─────────────────────────────────────


@pytest.mark.asyncio
async def test_get_session(store):
    await store.create_session("s1", "test-model", title="My Session")
    info = await store.get_session("s1")
    assert info is not None
    assert info.id == "s1"
    assert info.title == "My Session"
    assert info.model == "test-model"


@pytest.mark.asyncio
async def test_get_session_not_found(store):
    info = await store.get_session("nonexistent")
    assert info is None


# ── SessionStore: get_last_session ────────────────────────────────


@pytest.mark.asyncio
async def test_get_last_session(store):
    await store.create_session("s1", "model-a", title="First")
    await store.create_session("s2", "model-b", title="Second")
    last = await store.get_last_session()
    assert last is not None
    assert last.id == "s2"


@pytest.mark.asyncio
async def test_get_last_session_empty(store):
    last = await store.get_last_session()
    assert last is None


# ── SessionStore: rename_session ──────────────────────────────────


@pytest.mark.asyncio
async def test_rename_session(store):
    await store.create_session("s1", "test-model", title="Old Title")
    await store.rename_session("s1", "New Title")
    info = await store.get_session("s1")
    assert info is not None
    assert info.title == "New Title"


# ── SessionStore: fork_session ────────────────────────────────────


@pytest.mark.asyncio
async def test_fork_session_partial(store):
    await store.create_session("s1", "test-model", title="Original")
    await store.add_message("s1", Message(role=Role.USER, content="msg1"))
    await store.add_message("s1", Message(role=Role.ASSISTANT, content="resp1"))
    await store.add_message("s1", Message(role=Role.USER, content="msg2"))
    await store.add_message("s1", Message(role=Role.ASSISTANT, content="resp2"))

    await store.fork_session("s1", "s2", up_to_message_idx=1)

    forked = await store.get_messages("s2")
    assert len(forked) == 2
    assert forked[0].content == "msg1"
    assert forked[1].content == "resp1"


@pytest.mark.asyncio
async def test_fork_session_full(store):
    await store.create_session("s1", "test-model", title="Original")
    await store.add_message("s1", Message(role=Role.USER, content="msg1"))
    await store.add_message("s1", Message(role=Role.ASSISTANT, content="resp1"))

    await store.fork_session("s1", "s2", title="My Fork")

    info = await store.get_session("s2")
    assert info is not None
    assert info.title == "My Fork"
    forked = await store.get_messages("s2")
    assert len(forked) == 2


@pytest.mark.asyncio
async def test_fork_session_not_found(store):
    with pytest.raises(ValueError, match="not found"):
        await store.fork_session("nonexistent", "s2")


# ── SessionStore: get_message_nodes ───────────────────────────────


@pytest.mark.asyncio
async def test_get_message_nodes(store):
    await store.create_session("s1", "test-model")
    await store.add_message("s1", Message(role=Role.USER, content="Hello"))
    await store.add_message("s1", Message(role=Role.ASSISTANT, content="Hi"))

    nodes = await store.get_message_nodes("s1")
    assert len(nodes) == 2
    assert nodes[0]["role"] == "user"
    assert nodes[0]["content"] == "Hello"
    assert nodes[1]["role"] == "assistant"


# ── Agent: auto-save to store ─────────────────────────────────────


@pytest.fixture
async def agent_with_store(tmp_path):
    db_path = str(tmp_path / "agent_sessions.db")
    store = SessionStore(db_path)
    await store.open()

    provider = MockProvider()
    session_id = "test-session-1"
    await store.create_session(session_id, "mock-model")

    session = AgentSession(
        provider=provider,
        model="mock-model",
        tools=[],
        store=store,
        session_id=session_id,
    )
    yield session, store
    await store.close()


@pytest.mark.asyncio
async def test_auto_save_messages(agent_with_store):
    session, store = agent_with_store

    async for _ in session.run("Hello"):
        pass

    messages = await store.get_messages("test-session-1")
    # At least user + assistant
    assert len(messages) >= 2
    assert messages[0].role == Role.USER
    assert messages[0].content == "Hello"
    assert messages[1].role == Role.ASSISTANT


@pytest.mark.asyncio
async def test_session_id_preserved(agent_with_store):
    session, _ = agent_with_store
    assert session.session_id == "test-session-1"


@pytest.mark.asyncio
async def test_session_id_auto_generated():
    provider = MockProvider()
    session = AgentSession(provider=provider, model="m", tools=[])
    assert len(session.session_id) > 0  # UUID generated


# ── Agent: _estimate_tokens ──────────────────────────────────────


@pytest.mark.asyncio
async def test_estimate_tokens(agent_with_store):
    session, _ = agent_with_store
    initial_tokens = session._estimate_tokens()
    assert initial_tokens > 0  # System prompt

    session.messages.append(Message(role=Role.USER, content="A" * 400))
    new_tokens = session._estimate_tokens()
    assert new_tokens > initial_tokens
    # 400 chars / 4 = 100 additional tokens
    assert new_tokens >= initial_tokens + 100


# ── Agent: compact ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_compact(agent_with_store):
    session, store = agent_with_store

    # Run two messages
    async for _ in session.run("First message"):
        pass
    async for _ in session.run("Second message"):
        pass

    msg_count_before = len(session.messages)
    assert msg_count_before > 3  # system + 2*(user+assistant)

    summary = await session.compact()
    assert summary  # Non-empty summary

    # Messages reduced to system + compacted + ack
    assert len(session.messages) == 3
    assert session.messages[0].role == Role.SYSTEM
    assert "[Compacted" in session.messages[1].content

    # Store also compacted
    stored = await store.get_messages("test-session-1")
    assert len(stored) == 1
    assert stored[0].role == Role.SYSTEM


@pytest.mark.asyncio
async def test_compact_empty_session(agent_with_store):
    session, _ = agent_with_store
    # Only system prompt, nothing to compact
    summary = await session.compact()
    assert summary == ""


@pytest.mark.asyncio
async def test_compact_with_custom_prompt(agent_with_store):
    session, _ = agent_with_store

    async for _ in session.run("Write hello world"):
        pass

    summary = await session.compact(custom_prompt="Just say OK")
    assert summary  # Provider returns "mock response"


# ── Agent: auto-compact ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_compact_triggers(tmp_path):
    db_path = str(tmp_path / "autocompact.db")
    store = SessionStore(db_path)
    await store.open()

    provider = MockProvider()
    session_id = "compact-session"
    await store.create_session(session_id, "mock-model")

    session = AgentSession(
        provider=provider,
        model="mock-model",
        tools=[],
        store=store,
        session_id=session_id,
        auto_compact=True,
        compact_threshold=0.5,
        context_window=100,  # Very small for testing
    )

    events = []
    # System prompt + "A"*300 user + "mock response" assistant ≈ 95 tokens > 50 threshold
    async for event in session.run("A" * 300):
        events.append(event)

    compact_events = [e for e in events if e.type == AgentEventType.COMPACT]
    assert len(compact_events) == 1

    await store.close()


@pytest.mark.asyncio
async def test_auto_compact_skipped_below_threshold(tmp_path):
    db_path = str(tmp_path / "no_compact.db")
    store = SessionStore(db_path)
    await store.open()

    provider = MockProvider()
    session_id = "no-compact-session"
    await store.create_session(session_id, "mock-model")

    session = AgentSession(
        provider=provider,
        model="mock-model",
        tools=[],
        store=store,
        session_id=session_id,
        auto_compact=True,
        compact_threshold=0.8,
        context_window=100_000,  # Very large — won't trigger
    )

    events = []
    async for event in session.run("short"):
        events.append(event)

    compact_events = [e for e in events if e.type == AgentEventType.COMPACT]
    assert len(compact_events) == 0

    await store.close()


# ── Agent: resume flow ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_session(tmp_path):
    db_path = str(tmp_path / "resume.db")
    store = SessionStore(db_path)
    await store.open()

    provider = MockProvider()
    session_id = "resume-session"
    await store.create_session(session_id, "mock-model")

    # Session 1: run a message
    session1 = AgentSession(
        provider=provider,
        model="mock-model",
        tools=[],
        store=store,
        session_id=session_id,
    )
    async for _ in session1.run("Hello"):
        pass

    stored_after_first = await store.get_messages(session_id)
    assert len(stored_after_first) >= 2

    # Session 2: resume
    provider2 = MockProvider()
    session2 = AgentSession(
        provider=provider2,
        model="mock-model",
        tools=[],
        store=store,
        session_id=session_id,
    )

    # Load prior messages
    prior = await store.get_messages(session_id)
    session2.messages.extend(prior)

    assert len(session2.messages) == 1 + len(prior)  # system + prior

    # Continue
    async for _ in session2.run("Continue"):
        pass

    all_stored = await store.get_messages(session_id)
    assert len(all_stored) > len(prior)  # New messages added

    await store.close()


# ── Agent: COMPACT event type ────────────────────────────────────


def test_compact_event_type_exists():
    assert AgentEventType.COMPACT == "compact"
