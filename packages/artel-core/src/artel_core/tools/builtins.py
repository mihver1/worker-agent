"""Built-in tools for coding plus task board / operator notes helpers."""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import Any

from artel_ai.models import ToolDef, ToolParam

from artel_core.board import (
    add_task_to_markdown,
    append_project_board_file,
    normalize_task_status,
    operator_notes_path,
    read_project_board_file,
    render_numbered_text,
    tasks_path,
    update_task_in_markdown,
)
from artel_core.execution import get_current_tool_execution_context
from artel_core.tool_display import build_file_diff_display
from artel_core.tools import Tool
from artel_core.worktree import run_worktree_command

_MAX_READ_SIZE = 256 * 1024  # 256 KB


async def _read_text(path: Path, **kwargs: Any) -> str:
    """Non-blocking file read."""
    return await asyncio.to_thread(path.read_text, **kwargs)


async def _write_text(path: Path, content: str, **kwargs: Any) -> None:
    """Non-blocking file write."""
    await asyncio.to_thread(path.write_text, content, **kwargs)


def _read_text_limited_sync(path: Path, max_chars: int, **kwargs: Any) -> tuple[str, bool]:
    """Read up to max_chars from a text file without loading the whole file."""
    with path.open("r", **kwargs) as f:
        content = f.read(max_chars + 1)
    truncated = len(content) > max_chars
    return content[:max_chars], truncated


def _read_numbered_range_sync(
    path: Path,
    start_line: int,
    end_line: int,
    max_chars: int,
    **kwargs: Any,
) -> tuple[str, bool]:
    """Read a line range with numbering, bounded by max_chars."""
    chunks: list[str] = []
    total_chars = 0
    truncated = False

    with path.open("r", **kwargs) as f:
        for lineno, line in enumerate(f, start=1):
            if start_line and lineno < start_line:
                continue
            if end_line and lineno > end_line:
                break

            rendered = f"{lineno}|{line.rstrip(chr(13) + chr(10))}"
            if chunks:
                rendered = "\n" + rendered

            next_size = total_chars + len(rendered)
            if next_size > max_chars:
                remaining = max_chars - total_chars
                if remaining > 0:
                    chunks.append(rendered[:remaining])
                truncated = True
                break

            chunks.append(rendered)
            total_chars = next_size

    return "".join(chunks), truncated


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
            if start_line or end_line:
                output, truncated = await asyncio.to_thread(
                    _read_numbered_range_sync,
                    full_path,
                    start_line,
                    end_line,
                    _MAX_READ_SIZE,
                    encoding="utf-8",
                    errors="replace",
                )
                if truncated:
                    output += "\n... (truncated)"
                return output

            content, truncated = await asyncio.to_thread(
                _read_text_limited_sync,
                full_path,
                _MAX_READ_SIZE,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as e:
            return f"Error reading file: {e}"

        lines = content.splitlines()
        numbered = [f"{i + 1}|{line}" for i, line in enumerate(lines)]
        result = "\n".join(numbered)
        if truncated:
            result += "\n... (truncated)"
        return result

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
        existed = full_path.exists()
        previous = ""
        if existed:
            try:
                previous = await _read_text(full_path, encoding="utf-8", errors="replace")
            except OSError:
                previous = ""
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            await _write_text(full_path, content, encoding="utf-8")
        except OSError as e:
            return f"Error writing file: {e}"

        ctx = get_current_tool_execution_context()
        if ctx is not None:
            ctx.display_payload = build_file_diff_display(
                tool_name="write",
                path=path,
                before=previous,
                after=content,
            )

        lines = content.count("\n") + 1
        action = "Created" if not existed else "Wrote"
        return f"{action} {lines} lines to {full_path}"

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
            content = await _read_text(full_path, encoding="utf-8")
        except OSError as e:
            return f"Error reading file: {e}"

        count = content.count(search)
        if count == 0:
            return "Error: Search string not found in file."
        if count > 1:
            return f"Error: Search string found {count} times. Must be unique. Add more context."

        new_content = content.replace(search, replace, 1)
        try:
            await _write_text(full_path, new_content, encoding="utf-8")
        except OSError as e:
            return f"Error writing file: {e}"

        ctx = get_current_tool_execution_context()
        if ctx is not None:
            ctx.display_payload = build_file_diff_display(
                tool_name="edit",
                path=path,
                before=content,
                after=new_content,
            )

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
        except TimeoutError:
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


class ReadTasksTool(Tool):
    """Read the shared project task board."""

    name = "read_tasks"
    description = "Read the shared project task board with numbered lines."

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir

    async def execute(self, **kwargs: Any) -> str:
        content = await read_project_board_file(tasks_path(self.working_dir))
        if not content.strip():
            return "No tasks yet."
        return render_numbered_text(content)

    def definition(self) -> ToolDef:
        return ToolDef(name=self.name, description=self.description, parameters=[])


class AddTaskTool(Tool):
    """Add a task to the shared project task board."""

    name = "add_task"
    description = (
        "Add a task to the shared project task board. Optionally nest it under a parent task."
    )

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir

    async def execute(self, **kwargs: Any) -> str:
        title = str(kwargs.get("title", "")).strip()
        parent_task_id = int(kwargs.get("parent_task_id", 0) or 0)
        status = str(kwargs.get("status", "open") or "open")
        try:
            normalized_status = normalize_task_status(status)
            path = tasks_path(self.working_dir)
            content = await read_project_board_file(path)
            updated, task_id = add_task_to_markdown(
                content,
                title,
                parent_task_id=parent_task_id,
                status=normalized_status,
            )
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_text, updated, encoding="utf-8")
            ctx = get_current_tool_execution_context()
            if ctx is not None:
                session = ctx.session
                callback = getattr(session, "board_event_callback", None)
                if callable(callback):
                    with contextlib.suppress(Exception):
                        callback(
                            "task_added",
                            {
                                "task_id": task_id,
                                "title": title,
                                "parent_task_id": parent_task_id,
                                "status": normalized_status,
                            },
                        )
            return f"Added task #{task_id}: {title}"
        except Exception as e:
            return f"Error: {e}"

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="title", type="string", description="Task title"),
                ToolParam(
                    name="parent_task_id",
                    type="integer",
                    description="Parent task line number for nesting (optional)",
                    required=False,
                ),
                ToolParam(
                    name="status",
                    type="string",
                    description="Initial task status: open, in_progress, done, or blocked",
                    required=False,
                ),
            ],
        )


class UpdateTaskTool(Tool):
    """Update task title or status."""

    name = "update_task"
    description = "Update a task on the shared project task board by task id (line number)."

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir

    async def execute(self, **kwargs: Any) -> str:
        try:
            task_id = int(kwargs.get("task_id", 0) or 0)
            title_value = kwargs.get("title")
            title = None if title_value is None else str(title_value)
            status_value = kwargs.get("status")
            status = None if status_value is None else normalize_task_status(str(status_value))
            path = tasks_path(self.working_dir)
            content = await read_project_board_file(path)
            updated = update_task_in_markdown(content, task_id, title=title, status=status)
            await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(path.write_text, updated, encoding="utf-8")
            ctx = get_current_tool_execution_context()
            if ctx is not None:
                session = ctx.session
                callback = getattr(session, "board_event_callback", None)
                if callable(callback):
                    with contextlib.suppress(Exception):
                        callback(
                            "task_updated",
                            {
                                "task_id": task_id,
                                "title": title,
                                "status": status,
                            },
                        )
            return f"Updated task #{task_id}"
        except Exception as e:
            return f"Error: {e}"

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="task_id", type="integer", description="Task id / line number"),
                ToolParam(
                    name="title",
                    type="string",
                    description="Replacement task title (optional)",
                    required=False,
                ),
                ToolParam(
                    name="status",
                    type="string",
                    description="New task status: open, in_progress, done, or blocked (optional)",
                    required=False,
                ),
            ],
        )


class ReadOperatorNotesTool(Tool):
    """Read operator notes."""

    name = "read_operator_notes"
    description = "Read the operator notes scratchpad with numbered lines."

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir

    async def execute(self, **kwargs: Any) -> str:
        content = await read_project_board_file(operator_notes_path(self.working_dir))
        if not content.strip():
            return "Operator notes are empty."
        return render_numbered_text(content)

    def definition(self) -> ToolDef:
        return ToolDef(name=self.name, description=self.description, parameters=[])


class AppendOperatorNoteTool(Tool):
    """Append a note to operator notes."""

    name = "append_operator_note"
    description = "Append a short note to the operator notes scratchpad."

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir

    async def execute(self, **kwargs: Any) -> str:
        text = str(kwargs.get("text", "")).strip()
        if not text:
            return "Error: text must not be empty"
        await append_project_board_file(operator_notes_path(self.working_dir), text)
        ctx = get_current_tool_execution_context()
        if ctx is not None:
            session = ctx.session
            callback = getattr(session, "board_event_callback", None)
            if callable(callback):
                with contextlib.suppress(Exception):
                    callback("operator_notes_appended", {"text": text})
        return "Appended operator note."

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(name="text", type="string", description="Note text to append"),
            ],
        )


class WorktreeTool(Tool):
    """Manage git worktrees via the first-party Artel worktree service."""

    name = "worktree"
    description = (
        "Manage git worktrees for the current repository. Supports creating, listing, removing, "
        "and finishing managed worktrees using the same syntax as /wt."
    )

    def __init__(self, working_dir: str = "."):
        self.working_dir = working_dir

    async def execute(self, **kwargs: Any) -> str:
        action = str(kwargs.get("action", "")).strip().lower()
        branch = str(kwargs.get("branch", "")).strip()
        target = str(kwargs.get("target", "")).strip()

        if action in {"", "create"}:
            arg = branch
        elif action in {"list", "ls"}:
            arg = "list"
        elif action in {"remove", "rm"}:
            if not target:
                return "wt error: Usage: /wt rm <uniq_subpath>"
            arg = f"rm {target}"
        elif action in {"finish", "merge"}:
            if not target:
                return "wt error: Usage: /wt finish <uniq_subpath>"
            arg = f"finish {target}"
        elif action == "help":
            arg = "help"
        else:
            return "Error: Invalid action. Expected one of: create, list, remove, finish, help."

        return await asyncio.to_thread(run_worktree_command, self.working_dir, arg)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParam(
                    name="action",
                    type="string",
                    description="Action to perform: create, list, remove, finish, or help",
                    required=False,
                    enum=["create", "list", "remove", "finish", "help"],
                    default="create",
                ),
                ToolParam(
                    name="branch",
                    type="string",
                    description="Branch name to create or check out when action=create",
                    required=False,
                ),
                ToolParam(
                    name="target",
                    type="string",
                    description="Unique managed worktree path fragment for remove/finish",
                    required=False,
                ),
            ],
        )


def create_builtin_tools(working_dir: str = ".") -> list[Tool]:
    """Create the default Artel tools."""
    from artel_core.delegation.service import DelegationService
    from artel_core.delegation.tools import (
        CancelDelegateTool,
        DelegateTaskTool,
        GetDelegateTool,
        ListDelegatesTool,
    )
    from artel_core.tools.extra_search import create_extra_tools
    from artel_core.tools.web_fetch import WebFetchTool
    from artel_core.tools.web_search import WebSearchTool

    delegation_service = lambda: DelegationService()  # noqa: E731

    return [
        ReadTool(working_dir),
        WriteTool(working_dir),
        EditTool(working_dir),
        BashTool(working_dir),
        WorktreeTool(working_dir),
        DelegateTaskTool(delegation_service),
        ListDelegatesTool(delegation_service),
        GetDelegateTool(delegation_service),
        CancelDelegateTool(delegation_service),
        *create_extra_tools(working_dir),
        WebSearchTool(),
        WebFetchTool(),
        ReadTasksTool(working_dir),
        AddTaskTool(working_dir),
        UpdateTaskTool(working_dir),
        ReadOperatorNotesTool(working_dir),
        AppendOperatorNoteTool(working_dir),
    ]


def create_all_tools(working_dir: str = ".") -> list[Tool]:
    """Create the expanded local toolset including grep/find/ls and extra search tools."""
    from artel_core.delegation.service import DelegationService
    from artel_core.delegation.tools import (
        CancelDelegateTool,
        DelegateTaskTool,
        GetDelegateTool,
        ListDelegatesTool,
    )
    from artel_core.tools.extra_search import create_extra_tools
    from artel_core.tools.find import FindTool
    from artel_core.tools.grep import GrepTool
    from artel_core.tools.ls import LsTool

    delegation_service = lambda: DelegationService()  # noqa: E731

    return [
        ReadTool(working_dir),
        WriteTool(working_dir),
        EditTool(working_dir),
        BashTool(working_dir),
        WorktreeTool(working_dir),
        DelegateTaskTool(delegation_service),
        ListDelegatesTool(delegation_service),
        GetDelegateTool(delegation_service),
        CancelDelegateTool(delegation_service),
        GrepTool(working_dir),
        FindTool(working_dir),
        LsTool(working_dir),
        *create_extra_tools(working_dir),
    ]


def create_readonly_tools(working_dir: str = ".") -> list[Tool]:
    """Create read-only tools for exploration."""
    from artel_core.tools.extra_search import create_extra_tools
    from artel_core.tools.find import FindTool
    from artel_core.tools.grep import GrepTool
    from artel_core.tools.ls import LsTool

    return [
        ReadTool(working_dir),
        WorktreeTool(working_dir),
        GrepTool(working_dir),
        FindTool(working_dir),
        LsTool(working_dir),
        *create_extra_tools(working_dir),
    ]
