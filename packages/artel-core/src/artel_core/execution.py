"""Execution context helpers for tool calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from artel_core.agent import AgentSession


@dataclass(slots=True)
class ToolExecutionContext:
    """Context for the currently executing tool call."""

    session: AgentSession
    tool_name: str
    tool_call_id: str
    arguments: dict[str, Any]
    display_payload: dict[str, Any] | None = None


_CURRENT_TOOL_EXECUTION: ContextVar[ToolExecutionContext | None] = ContextVar(
    "artel_current_tool_execution",
    default=None,
)


def get_current_tool_execution_context() -> ToolExecutionContext | None:
    """Return the current tool execution context when inside Tool.execute()."""
    return _CURRENT_TOOL_EXECUTION.get()


@contextmanager
def bind_tool_execution_context(context: ToolExecutionContext) -> Iterator[ToolExecutionContext]:
    """Bind the current tool execution context for the duration of a tool call."""
    token = _CURRENT_TOOL_EXECUTION.set(context)
    try:
        yield context
    finally:
        _CURRENT_TOOL_EXECUTION.reset(token)
