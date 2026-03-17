"""Additional first-party search tools: ag, ripgrep, and glob."""

from __future__ import annotations

import asyncio
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from artel_ai.models import ToolDef, ToolParam

from artel_core.tools import Tool

_MAX_OUTPUT = 100_000
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_SEARCH_RESULTS = 50
_DEFAULT_GLOB_RESULTS = 100
_IGNORE_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
}


def _resolve_path(working_dir: str, raw_path: str) -> tuple[Path, str]:
    value = raw_path.strip() or "."
    path = Path(value)
    if path.is_absolute():
        return path, str(path)
    return Path(working_dir) / path, value


def _coerce_positive_int(value: Any, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _truncate_lines(output: str, *, max_results: int) -> str:
    lines = output.splitlines()
    if len(lines) <= max_results:
        return output
    rendered = "\n".join(lines[:max_results])
    return f"{rendered}\n... ({len(lines)} total, showing {max_results})"


def _truncate_chars(output: str) -> str:
    if len(output) <= _MAX_OUTPUT:
        return output
    return output[:_MAX_OUTPUT] + "\n... (truncated)"


def _render_path(path: Path, working_dir: str) -> str:
    base = Path(working_dir).resolve()
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(base))
    except ValueError:
        return str(resolved)


def _should_skip_match(path: Path, search_root: Path, *, include_hidden: bool) -> bool:
    try:
        relative_parts = path.resolve().relative_to(search_root.resolve()).parts
    except ValueError:
        relative_parts = path.parts

    for part in relative_parts:
        if part in _IGNORE_DIRS:
            return True
        if not include_hidden and part.startswith("."):
            return True
    return False


class _BaseExternalSearchTool(Tool, ABC):
    """Shared subprocess logic for external search tools."""

    binary_name: str

    def __init__(
        self,
        working_dir: str = ".",
        *,
        executable: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.working_dir = working_dir
        self.executable = executable or shutil.which(self.binary_name)
        self.timeout = timeout

    async def execute(self, **kwargs: Any) -> str:
        pattern = str(kwargs.get("pattern", "")).strip()
        if not pattern:
            return "Error: Missing pattern."

        max_results = _coerce_positive_int(kwargs.get("max_results"), _DEFAULT_SEARCH_RESULTS)
        resolved_path, command_path = _resolve_path(self.working_dir, str(kwargs.get("path", ".")))
        if not resolved_path.exists():
            return f"Error: Path not found: {resolved_path}"

        if not self.executable:
            return f"Error: {self.binary_name} is not available on this system."

        command = self._build_command(
            pattern=pattern,
            search_path=command_path,
            max_results=max_results,
            kwargs=kwargs,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except TimeoutError:
            return f"Error: Search timed out after {self.timeout:.0f}s"
        except OSError as exc:
            return f"Error running {self.binary_name}: {exc}"

        output = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip()

        if not output:
            if proc.returncode == 1:
                return "No matches found."
            if error:
                return f"Error: {error}"
            return "No matches found."

        if proc.returncode not in {0, 1}:
            return f"Error: {error or output}"

        return _truncate_chars(_truncate_lines(output, max_results=max_results))

    @abstractmethod
    def _build_command(
        self,
        *,
        pattern: str,
        search_path: str,
        max_results: int,
        kwargs: dict[str, Any],
    ) -> list[str]:
        """Build the subprocess command."""


class AgTool(_BaseExternalSearchTool):
    """Search tool backed by The Silver Searcher."""

    name = "ag"
    description = (
        "Search file contents with The Silver Searcher. Returns matching lines with file paths. "
        "Only available when the ag binary is installed."
    )
    binary_name = "ag"

    def _build_command(
        self,
        *,
        pattern: str,
        search_path: str,
        max_results: int,
        kwargs: dict[str, Any],
    ) -> list[str]:
        return [
            self.executable or self.binary_name,
            "--nocolor",
            "--nogroup",
            "-m",
            str(max_results),
            "--",
            pattern,
            search_path,
        ]

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="pattern", type="string", description="Regex or text to search for"),
                ToolParam(
                    name="path",
                    type="string",
                    description="File or directory to search (default: project root)",
                    required=False,
                ),
                ToolParam(
                    name="max_results",
                    type="integer",
                    description="Maximum number of matching lines to return (default: 50)",
                    required=False,
                ),
            ],
        )


class RipgrepTool(_BaseExternalSearchTool):
    """Search tool backed by ripgrep."""

    name = "ripgrep"
    description = (
        "Search file contents with ripgrep. Returns matching lines with file paths "
        "and line numbers. Only available when the rg binary is installed."
    )
    binary_name = "rg"

    def _build_command(
        self,
        *,
        pattern: str,
        search_path: str,
        max_results: int,
        kwargs: dict[str, Any],
    ) -> list[str]:
        command = [
            self.executable or self.binary_name,
            "--line-number",
            "--no-heading",
            "--color=never",
            f"--max-count={max_results}",
        ]
        glob_pattern = str(kwargs.get("glob_pattern", "")).strip()
        if glob_pattern:
            command.append(f"--glob={glob_pattern}")
        command.extend(["--", pattern, search_path])
        return command

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="pattern", type="string", description="Regex or text to search for"),
                ToolParam(
                    name="path",
                    type="string",
                    description="File or directory to search (default: project root)",
                    required=False,
                ),
                ToolParam(
                    name="glob_pattern",
                    type="string",
                    description="Optional ripgrep glob filter such as '*.py'",
                    required=False,
                ),
                ToolParam(
                    name="max_results",
                    type="integer",
                    description="Maximum number of matching lines to return (default: 50)",
                    required=False,
                ),
            ],
        )


class GlobTool(Tool):
    """Find files and directories via glob patterns."""

    name = "glob"
    description = (
        "Find files or directories by glob pattern. Supports recursive globs like '**/*.py' "
        "and returns matching paths."
    )

    def __init__(self, working_dir: str = ".") -> None:
        self.working_dir = working_dir

    async def execute(self, **kwargs: Any) -> str:
        pattern = str(kwargs.get("pattern", "")).strip()
        if not pattern:
            return "Error: Missing pattern."

        max_results = _coerce_positive_int(kwargs.get("max_results"), _DEFAULT_GLOB_RESULTS)
        include_hidden = _coerce_bool(kwargs.get("include_hidden", False))
        match_type = str(kwargs.get("type", "file")).strip().lower() or "file"
        if match_type not in {"file", "directory", "any"}:
            return "Error: Invalid type. Expected one of: file, directory, any."

        search_root, _ = _resolve_path(self.working_dir, str(kwargs.get("path", ".")))
        if not search_root.exists():
            return f"Error: Path not found: {search_root}"
        if not search_root.is_dir():
            return f"Error: Not a directory: {search_root}"

        try:
            matches = await asyncio.to_thread(
                self._glob_sync,
                search_root,
                pattern,
                match_type,
                include_hidden,
            )
        except (OSError, ValueError) as exc:
            return f"Error: {exc}"

        if not matches:
            return "No files found."

        rendered = [_render_path(path, self.working_dir) for path in matches[:max_results]]
        output = "\n".join(rendered)
        if len(matches) > max_results:
            output += f"\n... ({len(matches)} total, showing {max_results})"
        return _truncate_chars(output)

    @staticmethod
    def _glob_sync(
        search_root: Path,
        pattern: str,
        match_type: str,
        include_hidden: bool,
    ) -> list[Path]:
        results: set[Path] = set()
        for candidate in search_root.glob(pattern):
            resolved = candidate.resolve()
            if _should_skip_match(resolved, search_root, include_hidden=include_hidden):
                continue
            if match_type == "file" and not resolved.is_file():
                continue
            if match_type == "directory" and not resolved.is_dir():
                continue
            results.add(resolved)
        return sorted(results, key=lambda path: str(path).lower())

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(
                    name="pattern",
                    type="string",
                    description="Glob pattern to match, for example '**/*.py'",
                ),
                ToolParam(
                    name="path",
                    type="string",
                    description="Base directory to search from (default: project root)",
                    required=False,
                ),
                ToolParam(
                    name="type",
                    type="string",
                    description="Return files, directories, or both",
                    required=False,
                    enum=["file", "directory", "any"],
                    default="file",
                ),
                ToolParam(
                    name="include_hidden",
                    type="boolean",
                    description="Include hidden paths such as .github or .env files",
                    required=False,
                    default=False,
                ),
                ToolParam(
                    name="max_results",
                    type="integer",
                    description="Maximum number of matching paths to return (default: 100)",
                    required=False,
                ),
            ],
        )


def create_extra_tools(working_dir: str = ".") -> list[Tool]:
    """Create first-party extra filesystem/search tools."""

    tools: list[Tool] = []
    ag_path = shutil.which("ag")
    if ag_path:
        tools.append(AgTool(working_dir, executable=ag_path))

    rg_path = shutil.which("rg")
    if rg_path:
        tools.append(RipgrepTool(working_dir, executable=rg_path))

    tools.append(GlobTool(working_dir))
    return tools


__all__ = ["AgTool", "GlobTool", "RipgrepTool", "create_extra_tools"]
