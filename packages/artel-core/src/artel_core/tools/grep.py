"""Grep tool — search file contents by pattern."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from artel_ai.models import ToolDef, ToolParam

from artel_core.tools import Tool

_MAX_OUTPUT = 100_000  # characters


class GrepTool(Tool):
    """Search file contents for a regex pattern.

    Uses ripgrep (rg) if available, otherwise falls back to grep -rn.
    Respects .gitignore by default.
    """

    name = "grep"
    description = (
        "Search file contents for a regex pattern. Returns matching lines with "
        "file paths and line numbers. Respects .gitignore by default."
    )

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir
        self._use_rg = shutil.which("rg") is not None

    async def execute(self, **kwargs: Any) -> str:
        pattern = kwargs["pattern"]
        path = kwargs.get("path", ".")
        include = kwargs.get("include", "")
        max_results = int(kwargs.get("max_results", 50))

        search_path = Path(self.working_dir) / path if not Path(path).is_absolute() else Path(path)
        if not search_path.exists():
            return f"Error: Path not found: {search_path}"

        if self._use_rg:
            cmd = ["rg", "--line-number", "--no-heading", "--color=never"]
            cmd += [f"--max-count={max_results}"]
            if include:
                cmd += [f"--glob={include}"]
            cmd += [pattern, str(search_path)]
        else:
            cmd = ["grep", "-rn", "--color=never"]
            if include:
                cmd += [f"--include={include}"]
            cmd += [pattern, str(search_path)]

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
            return f"Error running search: {e}"

        output = stdout.decode("utf-8", errors="replace").strip()
        if not output:
            if proc.returncode == 1:
                return "No matches found."
            if stderr:
                return f"Error: {stderr.decode('utf-8', errors='replace').strip()}"
            return "No matches found."

        # Truncate and count
        lines = output.splitlines()
        if len(lines) > max_results:
            lines = lines[:max_results]
            output = "\n".join(lines) + f"\n... ({len(lines)} matches shown)"
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
                    description="Regex pattern to search for",
                ),
                ToolParam(
                    name="path",
                    type="string",
                    description="Directory or file to search (default: project root)",
                    required=False,
                ),
                ToolParam(
                    name="include",
                    type="string",
                    description="Glob pattern for files to include (e.g. '*.py')",
                    required=False,
                ),
                ToolParam(
                    name="max_results",
                    type="integer",
                    description="Maximum number of matching lines (default: 50)",
                    required=False,
                ),
            ],
        )
