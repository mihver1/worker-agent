"""The 4 built-in tools: read, write, edit, bash."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from worker_ai.models import ToolDef, ToolParam

from worker_core.tools import Tool

_MAX_READ_SIZE = 256 * 1024  # 256 KB


class ReadTool(Tool):
    """Read file contents, optionally with line ranges."""

    name = "read"
    description = (
        "Read the contents of a file. Returns the file content with line numbers. "
        "Optionally specify start_line and end_line to read a range."
    )

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir

    async def execute(self, **kwargs: Any) -> str:
        path = kwargs["path"]
        start_line = int(kwargs.get("start_line", 0))
        end_line = int(kwargs.get("end_line", 0))

        full_path = Path(self.working_dir) / path if not os.path.isabs(path) else Path(path)
        if not full_path.exists():
            return f"Error: File not found: {full_path}"
        if not full_path.is_file():
            return f"Error: Not a file: {full_path}"

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"Error reading file: {e}"

        if len(content) > _MAX_READ_SIZE:
            content = content[:_MAX_READ_SIZE] + "\n... (truncated)"

        lines = content.splitlines()
        if start_line or end_line:
            start = max(0, start_line - 1) if start_line else 0
            end = end_line if end_line else len(lines)
            lines = lines[start:end]
            offset = start
        else:
            offset = 0

        numbered = [f"{i + offset + 1}|{line}" for i, line in enumerate(lines)]
        return "\n".join(numbered)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="path", type="string", description="File path to read"),
                ToolParam(
                    name="start_line",
                    type="integer",
                    description="First line to read (1-indexed, optional)",
                    required=False,
                ),
                ToolParam(
                    name="end_line",
                    type="integer",
                    description="Last line to read (inclusive, optional)",
                    required=False,
                ),
            ],
        )


class WriteTool(Tool):
    """Create or overwrite a file."""

    name = "write"
    description = "Create a new file or overwrite an existing file with the given content."

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir

    async def execute(self, **kwargs: Any) -> str:
        path = kwargs["path"]
        content = kwargs["content"]

        full_path = Path(self.working_dir) / path if not os.path.isabs(path) else Path(path)
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
        except OSError as e:
            return f"Error writing file: {e}"

        lines = content.count("\n") + 1
        return f"Wrote {lines} lines to {full_path}"

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="path", type="string", description="File path to write"),
                ToolParam(name="content", type="string", description="Complete file content"),
            ],
        )


class EditTool(Tool):
    """Search-and-replace edit in a file."""

    name = "edit"
    description = (
        "Edit a file by replacing an exact string with a new string. "
        "The search string must match exactly (including whitespace)."
    )

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir

    async def execute(self, **kwargs: Any) -> str:
        path = kwargs["path"]
        search = kwargs["search"]
        replace = kwargs["replace"]

        full_path = Path(self.working_dir) / path if not os.path.isabs(path) else Path(path)
        if not full_path.exists():
            return f"Error: File not found: {full_path}"

        try:
            content = full_path.read_text(encoding="utf-8")
        except OSError as e:
            return f"Error reading file: {e}"

        count = content.count(search)
        if count == 0:
            return "Error: Search string not found in file."
        if count > 1:
            return f"Error: Search string found {count} times. Must be unique. Add more context."

        new_content = content.replace(search, replace, 1)
        try:
            full_path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return f"Error writing file: {e}"

        return f"Applied edit to {full_path}"

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="path", type="string", description="File path to edit"),
                ToolParam(
                    name="search",
                    type="string",
                    description="Exact string to find (must be unique in file)",
                ),
                ToolParam(name="replace", type="string", description="Replacement string"),
            ],
        )


class BashTool(Tool):
    """Execute a shell command."""

    name = "bash"
    description = (
        "Execute a shell command and return its stdout and stderr. "
        "Commands run in the project working directory."
    )

    def __init__(self, working_dir: str = ".", timeout: float = 120.0):
        self.working_dir = working_dir
        self.timeout = timeout

    async def execute(self, **kwargs: Any) -> str:
        command = kwargs["command"]
        timeout = float(kwargs.get("timeout", self.timeout))

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()  # type: ignore[union-attr]
            return f"Error: Command timed out after {timeout}s"
        except OSError as e:
            return f"Error executing command: {e}"

        output_parts: list[str] = []
        if stdout:
            output_parts.append(stdout.decode("utf-8", errors="replace"))
        if stderr:
            output_parts.append(f"STDERR:\n{stderr.decode('utf-8', errors='replace')}")

        result = "\n".join(output_parts).strip()
        if proc.returncode != 0:
            result = f"Exit code: {proc.returncode}\n{result}"

        # Truncate very long output
        if len(result) > _MAX_READ_SIZE:
            result = result[:_MAX_READ_SIZE] + "\n... (truncated)"

        return result or "(no output)"

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="command", type="string", description="Shell command to execute"),
                ToolParam(
                    name="timeout",
                    type="number",
                    description="Timeout in seconds (default: 120)",
                    required=False,
                ),
            ],
        )


def create_builtin_tools(working_dir: str = ".") -> list[Tool]:
    """Create the 4 default coding tools (read, write, edit, bash)."""
    return [
        ReadTool(working_dir),
        WriteTool(working_dir),
        EditTool(working_dir),
        BashTool(working_dir),
    ]


def create_all_tools(working_dir: str = ".") -> list[Tool]:
    """Create all 7 built-in tools including grep, find, ls."""
    from worker_core.tools.find import FindTool
    from worker_core.tools.grep import GrepTool
    from worker_core.tools.ls import LsTool

    return [
        ReadTool(working_dir),
        WriteTool(working_dir),
        EditTool(working_dir),
        BashTool(working_dir),
        GrepTool(working_dir),
        FindTool(working_dir),
        LsTool(working_dir),
    ]


def create_readonly_tools(working_dir: str = ".") -> list[Tool]:
    """Create read-only tools for exploration (read, grep, find, ls)."""
    from worker_core.tools.find import FindTool
    from worker_core.tools.grep import GrepTool
    from worker_core.tools.ls import LsTool

    return [
        ReadTool(working_dir),
        GrepTool(working_dir),
        FindTool(working_dir),
        LsTool(working_dir),
    ]
