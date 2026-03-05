"""Agent session — the core agent loop."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from worker_core.sessions import SessionStore

from worker_ai.models import (
    Done,
    Message,
    Role,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolDef,
    ToolResult,
    ReasoningDelta,
    Usage,
)
from worker_ai.provider import Provider

from worker_core.extensions import HookDispatcher
from worker_core.tools import Tool


# ── Thinking levels ───────────────────────────────────────────────

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]


# ── Agent events (yielded to the client) ─────────────────────────


class AgentEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DONE = "done"
    ERROR = "error"
    COMPACT = "compact"


@dataclass
class AgentEvent:
    type: AgentEventType
    content: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""
    usage: Usage | None = None
    error: str = ""


# ── Agent session ─────────────────────────────────────────────────

_DEFAULT_SYSTEM_PROMPT = (
    "You are Worker, a helpful coding assistant. "
    "You have access to tools for reading, writing, editing files and running shell commands. "
    "Use them to help the user with their coding tasks. Be concise and direct."
)

_CONTEXT_FILE_NAMES = ("AGENTS.md", "CLAUDE.md")


class AgentSession:
    """A single agent conversation session.

    Manages the message history, tool execution, and the LLM loop.
    Supports steering (mid-run interrupts) and follow-up message queues.
    """

    def __init__(
        self,
        provider: Provider,
        model: str,
        tools: list[Tool],
        *,
        system_prompt: str = "",
        project_dir: str = "",
        temperature: float = 0.0,
        max_turns: int = 50,
        thinking_level: ThinkingLevel = "off",
        permission_callback: Any | None = None,
        hooks: HookDispatcher | None = None,
        store: SessionStore | None = None,
        session_id: str = "",
        auto_compact: bool = False,
        compact_threshold: float = 0.8,
        context_window: int = 0,
    ):
        self.provider = provider
        self.model = model
        self.tools = {t.name: t for t in tools}
        self.temperature = temperature
        self.max_turns = max_turns
        self.thinking_level: ThinkingLevel = thinking_level
        self.permission_callback = permission_callback
        self.hooks = hooks or HookDispatcher()

        # Build system prompt: default + config + context files
        self.system_prompt = self._build_system_prompt(system_prompt, project_dir)

        self.messages: list[Message] = [
            Message(role=Role.SYSTEM, content=self.system_prompt),
        ]
        self.session_id = session_id or str(uuid.uuid4())

        # Persistence & compaction
        self.store: SessionStore | None = store
        self.auto_compact = auto_compact
        self.compact_threshold = compact_threshold
        self.context_window = context_window

        # Steering & follow-up message queues
        self._steering_queue: asyncio.Queue[str] = asyncio.Queue()
        self._followup_queue: asyncio.Queue[str] = asyncio.Queue()
        self._abort_event = asyncio.Event()
        self.steering_mode: Literal["one-at-a-time", "all"] = "one-at-a-time"
        self.followup_mode: Literal["one-at-a-time", "all"] = "one-at-a-time"

    # ── Steering / follow-up API ──────────────────────────────────

    def steer(self, message: str) -> None:
        """Queue a steering message — interrupts after current tool."""
        self._steering_queue.put_nowait(message)

    def follow_up(self, message: str) -> None:
        """Queue a follow-up message — delivered after agent finishes."""
        self._followup_queue.put_nowait(message)

    def abort(self) -> None:
        """Abort the current run."""
        self._abort_event.set()

    def _drain_steering(self) -> list[str]:
        msgs: list[str] = []
        while not self._steering_queue.empty():
            msgs.append(self._steering_queue.get_nowait())
            if self.steering_mode == "one-at-a-time":
                break
        return msgs

    def _drain_followup(self) -> list[str]:
        msgs: list[str] = []
        while not self._followup_queue.empty():
            msgs.append(self._followup_queue.get_nowait())
            if self.followup_mode == "one-at-a-time":
                break
        return msgs

    # ── Persistence ─────────────────────────────────────────────────

    async def _append_message(self, message: Message) -> None:
        """Append a message to history and persist to store."""
        self.messages.append(message)
        if self.store:
            await self.store.add_message(self.session_id, message)
        await self.hooks.fire("on_message", session=self, message=message)

    def _estimate_tokens(self) -> int:
        """Rough token estimate (~4 chars per token)."""
        total_chars = 0
        for msg in self.messages:
            total_chars += len(msg.content or "")
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    total_chars += len(tc.name) + len(str(tc.arguments))
            if msg.tool_result:
                total_chars += len(msg.tool_result.content)
            if msg.reasoning:
                total_chars += len(msg.reasoning)
        return total_chars // 4

    async def compact(self, custom_prompt: str = "") -> str:
        """Compact conversation history by summarizing it via LLM."""
        conv_parts: list[str] = []
        for msg in self.messages[1:]:  # Skip system prompt
            if msg.role == Role.USER:
                conv_parts.append(f"User: {msg.content}")
            elif msg.role == Role.ASSISTANT:
                text = msg.content
                if msg.tool_calls:
                    calls = ", ".join(tc.name for tc in msg.tool_calls)
                    text += f" [tools: {calls}]"
                conv_parts.append(f"Assistant: {text}")
            elif msg.role == Role.TOOL and msg.tool_result:
                content = msg.tool_result.content
                if len(content) > 500:
                    content = content[:500] + "\u2026"
                conv_parts.append(f"Tool ({msg.tool_result.tool_call_id}): {content}")

        if not conv_parts:
            return ""

        conversation_text = "\n".join(conv_parts)

        summary_prompt = custom_prompt or (
            "Summarize this conversation concisely. Preserve: key decisions, "
            "code changes with file paths, errors encountered, current task state, "
            "and any context needed to continue working."
        )

        summary_messages = [
            Message(role=Role.SYSTEM, content=summary_prompt),
            Message(role=Role.USER, content=conversation_text),
        ]

        summary_text = ""
        async for event in self.provider.stream_chat(
            self.model, summary_messages, temperature=0.0,
        ):
            if isinstance(event, TextDelta):
                summary_text += event.content

        old_system = self.messages[0]
        self.messages = [
            old_system,
            Message(
                role=Role.USER,
                content=f"[Compacted conversation history]\n{summary_text}",
            ),
            Message(
                role=Role.ASSISTANT,
                content="I have the conversation context. Ready to continue.",
            ),
        ]

        if self.store:
            await self.store.compact_messages(self.session_id, summary_text)

        await self.hooks.fire("on_compaction", session=self, summary=summary_text)
        return summary_text

    # ── System prompt construction ────────────────────────────────

    @staticmethod
    def _build_system_prompt(custom: str, project_dir: str) -> str:
        from pathlib import Path

        from worker_core.skills import build_skills_header, load_skills

        parts: list[str] = []

        # Check for SYSTEM.md override (project then global)
        system_override = None
        if project_dir:
            system_md = Path(project_dir) / ".worker" / "SYSTEM.md"
            if system_md.exists():
                try:
                    system_override = system_md.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
        global_system_md = Path("~/.config/worker/SYSTEM.md").expanduser()
        if system_override is None and global_system_md.exists():
            try:
                system_override = global_system_md.read_text(encoding="utf-8").strip()
            except OSError:
                pass

        if system_override:
            parts.append(system_override)
        else:
            parts.append(_DEFAULT_SYSTEM_PROMPT)

        # APPEND_SYSTEM.md
        for loc in (
            Path("~/.config/worker/APPEND_SYSTEM.md").expanduser(),
            Path(project_dir) / ".worker" / "APPEND_SYSTEM.md" if project_dir else None,
        ):
            if loc and loc.exists():
                try:
                    content = loc.read_text(encoding="utf-8").strip()
                    if content:
                        parts.append(content)
                except OSError:
                    pass

        # Custom (from config)
        if custom:
            parts.append(custom)

        # Walk up from project_dir collecting AGENTS.md / CLAUDE.md
        context_parts: list[str] = []

        # Global context file
        global_agents = Path("~/.config/worker/AGENTS.md").expanduser()
        if global_agents.exists():
            try:
                content = global_agents.read_text(encoding="utf-8").strip()
                if content:
                    context_parts.append(content)
            except OSError:
                pass

        # Walk up from project_dir to root
        if project_dir:
            found_files: list[tuple[Path, str]] = []
            current = Path(project_dir).resolve()
            home = Path.home()
            while True:
                for fname in _CONTEXT_FILE_NAMES:
                    # Check .worker/AGENTS.md
                    candidate = current / ".worker" / fname
                    if candidate.exists():
                        try:
                            content = candidate.read_text(encoding="utf-8").strip()
                            if content:
                                found_files.append((candidate, content))
                        except OSError:
                            pass
                    # Check AGENTS.md directly
                    candidate = current / fname
                    if candidate.exists():
                        try:
                            content = candidate.read_text(encoding="utf-8").strip()
                            if content:
                                found_files.append((candidate, content))
                        except OSError:
                            pass
                parent = current.parent
                if parent == current or current == home.parent:
                    break
                current = parent
            # Reverse so parents come first, child last (child overrides)
            for _, content in reversed(found_files):
                context_parts.append(content)

        if context_parts:
            parts.extend(context_parts)

        # Inject skills headers (Claude Code style)
        skills = load_skills(project_dir)
        header = build_skills_header(skills)
        if header:
            parts.append(header)

        return "\n\n".join(parts)

    def _tool_defs(self) -> list[ToolDef]:
        return [t.definition() for t in self.tools.values()]

    async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
        """Process a user message through the agent loop.

        Yields AgentEvents for text, tool calls, tool results, and completion.
        Supports mid-run steering via steer() and post-run follow-ups via follow_up().
        """
        self._abort_event.clear()
        await self._append_message(Message(role=Role.USER, content=user_message))

        # Fire session-level hook
        await self.hooks.fire("on_session_start", session=self)

        async for event in self._run_loop():
            yield event

        # Check for follow-up messages
        followups = self._drain_followup()
        while followups:
            for fu in followups:
                await self._append_message(Message(role=Role.USER, content=fu))
                async for event in self._run_loop():
                    yield event
            followups = self._drain_followup()

        # Auto-compact if enabled and over threshold
        if self.auto_compact and self.context_window > 0:
            estimated = self._estimate_tokens()
            if estimated > int(self.compact_threshold * self.context_window):
                summary = await self.compact()
                yield AgentEvent(type=AgentEventType.COMPACT, content=summary)

        await self.hooks.fire("on_session_end", session=self)

    async def _run_loop(self) -> AsyncIterator[AgentEvent]:
        """Inner agent loop — one logical run (may be multiple turns)."""
        for _turn in range(self.max_turns):
            if self._abort_event.is_set():
                yield AgentEvent(type=AgentEventType.ERROR, error="Aborted.")
                return

            # Hook: before_turn
            await self.hooks.fire("before_turn", session=self, turn=_turn)

            # Collect full assistant response from stream
            text_content = ""
            reasoning_content = ""
            tool_calls: list[ToolCall] = []
            final_usage: Usage | None = None

            try:
                async for event in self.provider.stream_chat(
                    self.model,
                    self.messages,
                    tools=self._tool_defs(),
                    temperature=self.temperature,
                    thinking_level=self.thinking_level,
                ):
                    if self._abort_event.is_set():
                        break

                    if isinstance(event, TextDelta):
                        text_content += event.content
                        yield AgentEvent(type=AgentEventType.TEXT_DELTA, content=event.content)

                    elif isinstance(event, ReasoningDelta):
                        reasoning_content += event.content
                        yield AgentEvent(
                            type=AgentEventType.REASONING_DELTA, content=event.content
                        )

                    elif isinstance(event, ToolCallDelta):
                        tc = ToolCall(id=event.id, name=event.name, arguments=event.arguments)
                        tool_calls.append(tc)
                        yield AgentEvent(
                            type=AgentEventType.TOOL_CALL,
                            tool_name=event.name,
                            tool_args=event.arguments,
                            tool_call_id=event.id,
                        )

                    elif isinstance(event, Done):
                        final_usage = event.usage

            except Exception as e:
                await self.hooks.fire("on_error", session=self, error=e)
                yield AgentEvent(type=AgentEventType.ERROR, error=str(e))
                return

            if self._abort_event.is_set():
                yield AgentEvent(type=AgentEventType.ERROR, error="Aborted.")
                return

            # Record assistant message
            await self._append_message(
                Message(
                    role=Role.ASSISTANT,
                    content=text_content,
                    tool_calls=tool_calls if tool_calls else None,
                    reasoning=reasoning_content or None,
                )
            )

            # Hook: after_turn
            await self.hooks.fire("after_turn", session=self, turn=_turn)

            # If no tool calls, we're done
            if not tool_calls:
                yield AgentEvent(type=AgentEventType.DONE, usage=final_usage)
                return

            # Execute tool calls
            for i, tc in enumerate(tool_calls):
                if self._abort_event.is_set():
                    yield AgentEvent(type=AgentEventType.ERROR, error="Aborted.")
                    return

                # Hook: before_tool_call (can modify args)
                exec_args = await self.hooks.fire_filter(
                    "before_tool_call", value=tc.arguments,
                    session=self, tool_name=tc.name,
                )

                # Hook: on_tool_call (notification, read-only)
                await self.hooks.fire(
                    "on_tool_call", session=self, tool_name=tc.name, args=exec_args,
                )

                tool = self.tools.get(tc.name)
                if not tool:
                    result = f"Error: Unknown tool '{tc.name}'"
                    is_error = True
                else:
                    try:
                        result = await tool.execute(**exec_args)
                        is_error = False
                    except Exception as e:
                        await self.hooks.fire("on_error", session=self, error=e)
                        result = f"Error executing {tc.name}: {e}"
                        is_error = True

                yield AgentEvent(
                    type=AgentEventType.TOOL_RESULT,
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    content=result,
                )

                await self._append_message(
                    Message(
                        role=Role.TOOL,
                        tool_result=ToolResult(
                            tool_call_id=tc.id,
                            content=result,
                            is_error=is_error,
                        ),
                    )
                )

                # Check for steering messages after each tool execution
                steering = self._drain_steering()
                if steering:
                    # Skip remaining tool calls, inject steering messages
                    for sm in steering:
                        await self._append_message(Message(role=Role.USER, content=sm))
                    break  # Break out of tool execution, continue agent loop

            # Loop back — the LLM will see the tool results and continue

        # Hit max turns
        error_msg = f"Reached maximum of {self.max_turns} iterations."
        await self.hooks.fire("on_error", session=self, error=RuntimeError(error_msg))
        yield AgentEvent(type=AgentEventType.ERROR, error=error_msg)
