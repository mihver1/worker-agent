"""Core data models for the LLM API layer."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Union

from pydantic import BaseModel, Field


# ── Messages ──────────────────────────────────────────────────────


class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


class ToolResult(BaseModel):
    """Result of a tool execution, sent back as a message."""

    tool_call_id: str
    content: str
    is_error: bool = False


class Message(BaseModel):
    """A single message in a conversation."""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    tool_result: ToolResult | None = None
    reasoning: str | None = None


# ── Tool definitions ──────────────────────────────────────────────


class ToolParam(BaseModel):
    """A single parameter in a tool definition."""

    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    enum: list[str] | None = None


class ToolDef(BaseModel):
    """Definition of a tool exposed to the LLM."""

    name: str
    description: str
    parameters: list[ToolParam]


# ── Stream events ─────────────────────────────────────────────────


class TextDelta(BaseModel):
    """Incremental text chunk from the model."""

    type: Literal["text_delta"] = "text_delta"
    content: str


class ToolCallDelta(BaseModel):
    """The model wants to call a tool."""

    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: dict[str, Any]


class ReasoningDelta(BaseModel):
    """Incremental reasoning/thinking chunk."""

    type: Literal["reasoning_delta"] = "reasoning_delta"
    content: str


class Usage(BaseModel):
    """Token usage information."""

    type: Literal["usage"] = "usage"
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class Done(BaseModel):
    """Signals the end of a streamed response."""

    type: Literal["done"] = "done"
    stop_reason: str = "end_turn"
    usage: Usage = Field(default_factory=Usage)


StreamEvent = Union[TextDelta, ToolCallDelta, ReasoningDelta, Usage, Done]


# ── Model info ────────────────────────────────────────────────────


class ModelInfo(BaseModel):
    """Metadata about a model."""

    id: str
    provider: str
    name: str
    context_window: int = 128_000
    max_output_tokens: int = 8_192
    supports_tools: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False
    input_price_per_m: float = 0.0  # USD per 1M input tokens
    output_price_per_m: float = 0.0  # USD per 1M output tokens
