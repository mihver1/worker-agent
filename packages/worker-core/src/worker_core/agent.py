"""Agent session — the core agent loop."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

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


# ── Agent events (yielded to the client) ─────────────────────────


class AgentEventType(str, Enum):
    TEXT_DELTA = "text_delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    DONE = "done"
    ERROR = "error"


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


class AgentSession:
    """A single agent conversation session.

    Manages the message history, tool execution, and the LLM loop.
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
        permission_callback: Any | None = None,
        hooks: HookDispatcher | None = None,
    ):
        self.provider = provider
        self.model = model
        self.tools = {t.name: t for t in tools}
        self.temperature = temperature
        self.max_turns = max_turns
        self.permission_callback = permission_callback
        self.hooks = hooks or HookDispatcher()

        # Build system prompt: default + config + AGENTS.md
        self.system_prompt = self._build_system_prompt(system_prompt, project_dir)

        self.messages: list[Message] = [
            Message(role=Role.SYSTEM, content=self.system_prompt),
        ]
        self.session_id = str(uuid.uuid4())

    @staticmethod
    def _build_system_prompt(custom: str, project_dir: str) -> str:
        parts = [_DEFAULT_SYSTEM_PROMPT]
        if custom:
            parts.append(custom)
        # Load .worker/AGENTS.md if present
        if project_dir:
            from pathlib import Path

            agents_md = Path(project_dir) / ".worker" / "AGENTS.md"
            if agents_md.exists():
                try:
                    content = agents_md.read_text(encoding="utf-8").strip()
                    if content:
                        parts.append(content)
                except OSError:
                    pass
        return "\n\n".join(parts)

    def _tool_defs(self) -> list[ToolDef]:
        return [t.definition() for t in self.tools.values()]

    async def run(self, user_message: str) -> AsyncIterator[AgentEvent]:
        """Process a user message through the agent loop.

        Yields AgentEvents for text, tool calls, tool results, and completion.
        """
        self.messages.append(Message(role=Role.USER, content=user_message))

        for _turn in range(self.max_turns):
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
                ):
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
                yield AgentEvent(type=AgentEventType.ERROR, error=str(e))
                return

            # Record assistant message
            self.messages.append(
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
            for tc in tool_calls:
                # Hook: on_tool_call (can inspect/log, not modify)
                await self.hooks.fire(
                    "on_tool_call", session=self, tool_name=tc.name, args=tc.arguments
                )

                tool = self.tools.get(tc.name)
                if not tool:
                    result = f"Error: Unknown tool '{tc.name}'"
                    is_error = True
                else:
                    try:
                        result = await tool.execute(**tc.arguments)
                        is_error = False
                    except Exception as e:
                        result = f"Error executing {tc.name}: {e}"
                        is_error = True

                yield AgentEvent(
                    type=AgentEventType.TOOL_RESULT,
                    tool_name=tc.name,
                    tool_call_id=tc.id,
                    content=result,
                )

                self.messages.append(
                    Message(
                        role=Role.TOOL,
                        tool_result=ToolResult(
                            tool_call_id=tc.id,
                            content=result,
                            is_error=is_error,
                        ),
                    )
                )

            # Loop back — the LLM will see the tool results and continue

        # Hit max turns
        yield AgentEvent(
            type=AgentEventType.ERROR,
            error=f"Reached maximum of {self.max_turns} iterations.",
        )
