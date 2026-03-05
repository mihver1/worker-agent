"""Example Worker extension — demonstrates tools and hooks.

Install:
    worker ext install /path/to/extensions/worker-ext-example

Or from git:
    worker ext install git+https://github.com/your-org/worker-ext-example.git
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from worker_core.extensions import CommandHandler, Extension, hook
from worker_core.tools import Tool
from worker_ai.models import ToolDef, ToolParam

logger = logging.getLogger("worker.ext.example")


class TimestampTool(Tool):
    """A simple tool that returns the current UTC timestamp."""

    name = "timestamp"
    description = "Returns the current UTC date and time."

    async def execute(self, **kwargs: Any) -> str:
        fmt = kwargs.get("format", "%Y-%m-%d %H:%M:%S")
        return datetime.now(timezone.utc).strftime(fmt)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(
                    name="format",
                    type="string",
                    description='strftime format (default: "%Y-%m-%d %H:%M:%S")',
                    required=False,
                ),
            ],
        )


class ExampleExtension(Extension):
    """Example extension showing how to add tools and hooks."""

    name = "example"
    version = "0.1.0"

    async def on_load(self) -> None:
        logger.info("Example extension loaded")

    def get_tools(self) -> list[Tool]:
        """Return extra tools to register with the agent."""
        return [TimestampTool()]

    def get_commands(self) -> dict[str, CommandHandler]:
        """Return slash commands."""
        return {"time": self._cmd_time}

    async def _cmd_time(self, arg: str) -> str | None:
        """Handle /time command."""
        fmt = arg.strip() or "%Y-%m-%d %H:%M:%S"
        return datetime.now(timezone.utc).strftime(fmt)

    @hook("before_turn")
    async def log_turn_start(self, session: Any, turn: int) -> None:
        logger.debug("Turn %d starting (messages: %d)", turn, len(session.messages))

    @hook("after_turn")
    async def log_turn_end(self, session: Any, turn: int) -> None:
        logger.debug("Turn %d completed", turn)

    @hook("on_tool_call")
    async def log_tool_call(self, session: Any, tool_name: str, args: dict) -> None:
        logger.info("Tool call: %s(%s)", tool_name, args)
