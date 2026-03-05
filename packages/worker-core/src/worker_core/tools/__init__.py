"""Built-in tools and tool infrastructure."""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from worker_ai.models import ToolDef, ToolParam


class Tool(ABC):
    """Base class for agent tools."""

    name: str
    description: str

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        """Execute the tool and return a string result."""
        ...

    @abstractmethod
    def definition(self) -> ToolDef:
        """Return the tool definition for the LLM."""
        ...


def _python_type_to_json(annotation: Any) -> str:
    """Map Python type hints to JSON Schema types."""
    if annotation is inspect.Parameter.empty or annotation is str:
        return "string"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is bool:
        return "boolean"
    return "string"


class FunctionTool(Tool):
    """Tool created from an async function via the @tool decorator."""

    def __init__(self, fn: Callable[..., Any], *, name: str, description: str):
        self._fn = fn
        self.name = name
        self.description = description

        sig = inspect.signature(fn)
        self._params: list[ToolParam] = []
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            self._params.append(
                ToolParam(
                    name=param_name,
                    type=_python_type_to_json(param.annotation),
                    description="",
                    required=param.default is inspect.Parameter.empty,
                )
            )

    async def execute(self, **kwargs: Any) -> str:
        result = self._fn(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return str(result)

    def definition(self) -> ToolDef:
        return ToolDef(name=self.name, description=self.description, parameters=self._params)


def tool(description: str = "", *, name: str | None = None) -> Callable[..., Any]:
    """Decorator to mark an async method as an agent tool.

    Usage:
        @tool(description="Read a file from disk")
        async def read(self, path: str, start_line: int = 0) -> str:
            ...
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn._tool_meta = {"name": name or fn.__name__, "description": description}  # type: ignore[attr-defined]
        return fn

    return decorator
