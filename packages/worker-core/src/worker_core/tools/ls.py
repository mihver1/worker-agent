"""Ls tool — list directory contents."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from worker_ai.models import ToolDef, ToolParam

from worker_core.tools import Tool

_IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", ".tox", ".mypy_cache", ".pytest_cache"}


class LsTool(Tool):
    """List directory contents with metadata.

    Shows files and directories with sizes, filtering out common
    noise directories (.git, __pycache__, node_modules, etc.).
    """

    name = "ls"
    description = (
        "List the contents of a directory. Shows files and subdirectories "
        "with sizes. Filters out .git, __pycache__, node_modules by default."
    )

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir

    async def execute(self, **kwargs: Any) -> str:
        path = kwargs.get("path", ".")
        max_depth = int(kwargs.get("max_depth", 1))
        show_hidden = kwargs.get("show_hidden", False)

        target = (
            Path(self.working_dir) / path
            if not Path(path).is_absolute()
            else Path(path)
        )
        if not target.exists():
            return f"Error: Path not found: {target}"
        if not target.is_dir():
            # Single file — show info
            stat = target.stat()
            return f"{target.name}  ({_format_size(stat.st_size)})"

        # Offload blocking directory traversal to thread pool
        lines: list[str] = await asyncio.to_thread(
            self._list_dir_sync, target, max_depth, show_hidden,
        )

        if not lines:
            return "(empty directory)"
        return "\n".join(lines)

    @staticmethod
    def _list_dir_sync(
        target: Path, max_depth: int, show_hidden: bool,
    ) -> list[str]:
        lines: list[str] = []
        LsTool._walk(target, lines, depth=0, max_depth=max_depth, show_hidden=show_hidden)
        return lines

    @staticmethod
    def _walk(
        directory: Path,
        lines: list[str],
        depth: int,
        max_depth: int,
        show_hidden: bool,
    ) -> None:
        if depth >= max_depth:
            return

        try:
            entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except PermissionError:
            lines.append(f"{'  ' * depth}(permission denied)")
            return

        for entry in entries:
            name = entry.name
            # Skip hidden files unless requested
            if not show_hidden and name.startswith("."):
                continue
            # Skip noise directories
            if entry.is_dir() and name in _IGNORE_DIRS:
                continue

            indent = "  " * depth
            if entry.is_dir():
                # Count children
                try:
                    child_count = sum(1 for _ in entry.iterdir())
                except PermissionError:
                    child_count = -1
                suffix = f"  ({child_count} items)" if child_count >= 0 else ""
                lines.append(f"{indent}{name}/{suffix}")
                if depth + 1 < max_depth:
                    LsTool._walk(entry, lines, depth + 1, max_depth, show_hidden)
            else:
                try:
                    size = entry.stat().st_size
                    lines.append(f"{indent}{name}  ({_format_size(size)})")
                except OSError:
                    lines.append(f"{indent}{name}")

            # Safety: don't output more than 500 entries
            if len(lines) > 500:
                lines.append("... (truncated at 500 entries)")
                return

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(
                    name="path",
                    type="string",
                    description="Directory path to list (default: project root)",
                    required=False,
                ),
                ToolParam(
                    name="max_depth",
                    type="integer",
                    description="How deep to recurse (default: 1, max: 5)",
                    required=False,
                ),
                ToolParam(
                    name="show_hidden",
                    type="boolean",
                    description="Show hidden files (default: false)",
                    required=False,
                ),
            ],
        )


def _format_size(size: int) -> str:
    """Format file size in human-readable form."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            if unit == "B":
                return f"{size} {unit}"
            return f"{size:.1f} {unit}"
        size //= 1024
    return f"{size:.1f} TB"
