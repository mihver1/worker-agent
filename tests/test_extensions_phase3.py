"""Tests for Phase 3 extension system enhancements."""

from __future__ import annotations

import pytest
from conftest import MockProvider
from worker_ai.models import Done, Message, Role, TextDelta, ToolCallDelta, ToolDef, Usage
from worker_core import extensions as extensions_mod
from worker_core.agent import AgentEventType, AgentSession
from worker_core.extensions import (
    CommandHandler,
    Extension,
    HookDispatcher,
    hook,
    load_extensions,
    load_extensions_async,
    reload_extensions_async,
)
from worker_core.tools import Tool

# ── Test Extensions ───────────────────────────────────────────────


class DummyTool(Tool):
    name = "dummy"
    description = "A dummy tool"

    async def execute(self, **kwargs) -> str:
        return "dummy result"

    def definition(self) -> ToolDef:
        return ToolDef(name=self.name, description=self.description, parameters=[])


class SampleExtension(Extension):
    name = "test-ext"
    version = "1.0.0"

    def __init__(self):
        self.loaded = False
        self.unloaded = False
        self.messages_seen: list[Message] = []
        self.errors_seen: list[Exception] = []
        self.compactions_seen: list[str] = []
        self.tool_calls_seen: list[dict] = []

    async def on_load(self) -> None:
        self.loaded = True

    async def on_unload(self) -> None:
        self.unloaded = True

    def get_tools(self) -> list[Tool]:
        return [DummyTool()]

    def get_commands(self) -> dict[str, CommandHandler]:
        return {"testcmd": self._handle_testcmd, "echo": self._handle_echo}

    async def _handle_testcmd(self, arg: str) -> str | None:
        return f"test command executed with: {arg}"

    async def _handle_echo(self, arg: str) -> str | None:
        return arg or None

    @hook("on_message")
    async def on_msg(self, session, message):
        self.messages_seen.append(message)

    @hook("on_error")
    async def on_err(self, session, error):
        self.errors_seen.append(error)

    @hook("on_compaction")
    async def on_compact(self, session, summary):
        self.compactions_seen.append(summary)

    @hook("on_tool_call")
    async def on_tc(self, session, tool_name, args):
        self.tool_calls_seen.append({"name": tool_name, "args": args})


class ArgModifyExtension(Extension):
    """Extension that modifies tool call args via before_tool_call."""

    name = "arg-modifier"

    @hook("before_tool_call")
    async def modify_args(self, value, session, tool_name):
        # Add an extra key to args
        modified = dict(value)
        modified["_modified"] = True
        return modified


# ── HookDispatcher: commands ──────────────────────────────────────


def test_dispatcher_collects_commands():
    ext = SampleExtension()
    dispatcher = HookDispatcher([ext])
    assert "testcmd" in dispatcher.commands
    assert "echo" in dispatcher.commands


@pytest.mark.asyncio
async def test_dispatcher_command_execution():
    ext = SampleExtension()
    dispatcher = HookDispatcher([ext])
    handler = dispatcher.commands["testcmd"]
    result = await handler("hello")
    assert result == "test command executed with: hello"


@pytest.mark.asyncio
async def test_dispatcher_command_returns_none():
    ext = SampleExtension()
    dispatcher = HookDispatcher([ext])
    handler = dispatcher.commands["echo"]
    result = await handler("")
    assert result is None


# ── HookDispatcher: fire_filter ───────────────────────────────────


@pytest.mark.asyncio
async def test_fire_filter_modifies_value():
    ext = ArgModifyExtension()
    dispatcher = HookDispatcher([ext])

    original = {"path": "foo.txt"}
    result = await dispatcher.fire_filter(
        "before_tool_call", value=original, session=None, tool_name="read",
    )
    assert result["path"] == "foo.txt"
    assert result["_modified"] is True


@pytest.mark.asyncio
async def test_fire_filter_no_hooks():
    dispatcher = HookDispatcher([])
    original = {"key": "value"}
    result = await dispatcher.fire_filter("before_tool_call", value=original)
    assert result is original  # Unchanged


@pytest.mark.asyncio
async def test_fire_filter_chain():
    """Multiple hooks are applied in sequence."""

    class Ext1(Extension):
        @hook("before_tool_call")
        async def mod1(self, value, **kwargs):
            return {**value, "step1": True}

    class Ext2(Extension):
        @hook("before_tool_call")
        async def mod2(self, value, **kwargs):
            return {**value, "step2": True}

    dispatcher = HookDispatcher([Ext1(), Ext2()])
    result = await dispatcher.fire_filter("before_tool_call", value={"original": True})
    assert result["original"] is True
    assert result["step1"] is True
    assert result["step2"] is True


# ── Extension: get_tools ──────────────────────────────────────────


def test_extension_get_tools():
    ext = SampleExtension()
    tools = ext.get_tools()
    assert len(tools) == 1
    assert tools[0].name == "dummy"


def test_base_extension_get_tools_empty():
    ext = Extension()
    assert ext.get_tools() == []


def test_base_extension_get_commands_empty():
    ext = Extension()
    assert ext.get_commands() == {}


# ── Agent hooks: on_message ───────────────────────────────────────


@pytest.mark.asyncio
async def test_on_message_hook_fires():
    ext = SampleExtension()
    dispatcher = HookDispatcher([ext])
    provider = MockProvider()

    session = AgentSession(
        provider=provider, model="m", tools=[], hooks=dispatcher,
    )

    async for _ in session.run("hello"):
        pass

    # Should see at least user + assistant messages
    roles = [m.role for m in ext.messages_seen]
    assert Role.USER in roles
    assert Role.ASSISTANT in roles


# ── Agent hooks: on_error ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_error_hook_fires():
    ext = SampleExtension()
    dispatcher = HookDispatcher([ext])

    # Provider that always errors
    class ErrorProvider(MockProvider):
        async def stream_chat(self, *args, **kwargs):
            raise RuntimeError("test error")
            if False:  # pragma: no cover
                yield

    provider = ErrorProvider()
    session = AgentSession(
        provider=provider, model="m", tools=[], hooks=dispatcher,
    )

    events = []
    async for event in session.run("cause error"):
        events.append(event)

    error_events = [e for e in events if e.type == AgentEventType.ERROR]
    assert len(error_events) == 1
    assert "test error" in error_events[0].error

    assert len(ext.errors_seen) == 1
    assert isinstance(ext.errors_seen[0], RuntimeError)


# ── Agent hooks: on_compaction ────────────────────────────────────


@pytest.mark.asyncio
async def test_on_compaction_hook_fires():
    ext = SampleExtension()
    dispatcher = HookDispatcher([ext])
    provider = MockProvider()

    session = AgentSession(
        provider=provider, model="m", tools=[], hooks=dispatcher,
    )

    # Run a message first
    async for _ in session.run("hello"):
        pass

    # Compact
    await session.compact()

    assert len(ext.compactions_seen) == 1
    assert ext.compactions_seen[0]  # Non-empty summary


# ── Agent hooks: before_tool_call ─────────────────────────────────


@pytest.mark.asyncio
async def test_before_tool_call_modifies_args(tmp_workdir):
    from worker_core.tools.builtins import create_builtin_tools

    ext = ArgModifyExtension()
    track_ext = SampleExtension()
    dispatcher = HookDispatcher([ext, track_ext])

    provider = MockProvider(
        responses=[
            [
                ToolCallDelta(id="tc_1", name="read", arguments={"path": "hello.txt"}),
                Done(usage=Usage()),
            ],
            [TextDelta(content="done"), Done(usage=Usage())],
        ]
    )

    tools = create_builtin_tools(tmp_workdir)
    session = AgentSession(
        provider=provider, model="m", tools=tools, hooks=dispatcher,
    )

    async for _ in session.run("read hello.txt"):
        pass

    # on_tool_call should have seen modified args
    assert len(track_ext.tool_calls_seen) >= 1
    assert track_ext.tool_calls_seen[0]["args"].get("_modified") is True


# ── reload_extensions_async ───────────────────────────────────────


@pytest.mark.asyncio
async def test_load_extensions_async_calls_on_load(monkeypatch):
    class LifecycleExtension(Extension):
        name = "lifecycle"

        def __init__(self):
            self.loaded = False

        async def on_load(self) -> None:
            self.loaded = True

    monkeypatch.setattr(
        extensions_mod,
        "discover_extensions",
        lambda group="worker.extensions": {"lifecycle": LifecycleExtension},
    )

    instances, dispatcher = await load_extensions_async()

    assert len(instances) == 1
    assert instances[0].loaded is True
    assert isinstance(dispatcher, HookDispatcher)


@pytest.mark.asyncio
async def test_reload_extensions_async_unloads_old_instances_and_activates_new(monkeypatch):
    events: list[str] = []

    class OldExtension(Extension):
        def __init__(self):
            self.unloaded = False

        async def on_unload(self) -> None:
            self.unloaded = True
            events.append("old-unload")

    class NewExtension(Extension):
        name = "replacement"

        def __init__(self):
            self.loaded = False

        async def on_load(self) -> None:
            self.loaded = True
            events.append("new-load")

    old = OldExtension()
    monkeypatch.setattr(
        extensions_mod,
        "discover_extensions",
        lambda group="worker.extensions": {"replacement": NewExtension},
    )

    new_instances, new_dispatcher = await reload_extensions_async([old])

    assert old.unloaded is True
    assert events == ["old-unload", "new-load"]
    assert len(new_instances) == 1
    assert new_instances[0].loaded is True
    assert isinstance(new_dispatcher, HookDispatcher)


# ── load_extensions ───────────────────────────────────────────────


def test_load_extensions_returns_tuple():
    instances, dispatcher = load_extensions()
    assert isinstance(instances, list)
    assert isinstance(dispatcher, HookDispatcher)
