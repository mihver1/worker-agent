"""Find tool — search for files by name or glob pattern."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from artel_ai.models import ToolDef, ToolParam

from artel_core.tools import Tool

_MAX_OUTPUT = 100_000


class FindTool(Tool):
    """Find files by name or glob pattern.

    Uses fd if available, otherwise falls back to find.
    Respects .gitignore by default.
    """

    name = "find"
    description = (
        "Find files by name or glob pattern. Returns matching file paths. "
        "Respects .gitignore by default."
    )

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir
        self._use_fd = shutil.which("fd") is not None

    async def execute(self, **kwargs: Any) -> str:
        pattern = kwargs.get("pattern", "")
        path = kwargs.get("path", ".")
        file_type = kwargs.get("type", "")
        max_results = int(kwargs.get("max_results", 100))

        search_path = Path(self.working_dir) / path if not Path(path).is_absolute() else Path(path)
        if not search_path.exists():
            return f"Error: Path not found: {search_path}"

        if self._use_fd:
            cmd = ["fd", "--color=never"]
            if file_type == "file":
                cmd += ["--type", "f"]
            elif file_type == "directory":
                cmd += ["--type", "d"]
            if max_results:
                cmd += [f"--max-results={max_results}"]
            if pattern:
                cmd.append(pattern)
            cmd.append(str(search_path))
        else:
            cmd = ["find", str(search_path)]
            if file_type == "file":
                cmd += ["-type", "f"]
            elif file_type == "directory":
                cmd += ["-type", "d"]
            if pattern:
                cmd += ["-name", pattern]
            # Exclude common noise
            cmd += [
                "-not",
                "-path",
                "*/.git/*",
                "-not",
                "-path",
                "*/__pycache__/*",
                "-not",
                "-path",
                "*/node_modules/*",
            ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            return "Error: Search timed out after 30s"
        except OSError as e:
            return f"Error running find: {e}"

        output = stdout.decode("utf-8", errors="replace").strip()
        if not output:
            return "No files found."

        lines = output.splitlines()
        if len(lines) > max_results:
            total = len(lines)
            lines = lines[:max_results]
            output = "\n".join(lines) + f"\n... ({total} total, showing {max_results})"
        else:
            output = "\n".join(lines)

        if len(output) > _MAX_OUTPUT:
            output = output[:_MAX_OUTPUT] + "\n... (truncated)"

        return output

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(
                    name="pattern",
                    type="string",
                    description="File name pattern (regex for fd, glob for find)",
                    required=False,
                ),
                ToolParam(
                    name="path",
                    type="string",
                    description="Directory to search in (default: project root)",
                    required=False,
                ),
                ToolParam(
                    name="type",
                    type="string",
                    description="Filter by type: 'file' or 'directory'",
                    required=False,
                ),
                ToolParam(
                    name="max_results",
                    type="integer",
                    description="Maximum number of results (default: 100)",
                    required=False,
                ),
            ],
        )
