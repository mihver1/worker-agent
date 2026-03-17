"""Project-scoped task board and operator notes helpers."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from artel_core.config import project_state_dir

TASKS_FILE_NAME = "tasks.md"
OPERATOR_NOTES_FILE_NAME = "operator-notes.md"
_TASK_LINE_RE = re.compile(r"^(?P<indent>\s*)- \[(?P<mark>[ xX])\] (?P<title>.*)$")
_VALID_TASK_STATUSES = {"open", "in_progress", "done", "blocked"}


def tasks_path(project_dir: str) -> Path:
    return project_state_dir(project_dir) / TASKS_FILE_NAME


def operator_notes_path(project_dir: str) -> Path:
    return project_state_dir(project_dir) / OPERATOR_NOTES_FILE_NAME


async def read_project_board_file(path: Path) -> str:
    if not path.exists():
        return ""
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


async def write_project_board_file(path: Path, content: str) -> None:
    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(path.write_text, content, encoding="utf-8")


async def append_project_board_file(path: Path, text: str) -> None:
    existing = await read_project_board_file(path)
    content = text.rstrip()
    updated = existing.rstrip() + "\n\n" + content + "\n" if existing.strip() else content + "\n"
    await write_project_board_file(path, updated)


def render_numbered_text(content: str) -> str:
    if not content:
        return ""
    lines = content.splitlines()
    return "\n".join(f"{index}|{line}" for index, line in enumerate(lines, start=1))


def normalize_task_status(status: str) -> str:
    normalized = status.strip().lower().replace("-", "_").replace(" ", "_")
    if normalized not in _VALID_TASK_STATUSES:
        raise ValueError("status must be one of: open, in_progress, done, blocked")
    return normalized


def _parse_task_line(line: str) -> tuple[int, bool, str] | None:
    match = _TASK_LINE_RE.match(line)
    if not match:
        return None
    indent = len(match.group("indent"))
    done = match.group("mark").lower() == "x"
    title = match.group("title").rstrip()
    return indent, done, title


def _task_line(indent: int, title: str, *, status: str) -> str:
    normalized = normalize_task_status(status)
    prefix = " " * max(0, indent)
    rendered_title = title.strip()
    if normalized == "blocked":
        rendered_title = f"[blocked] {rendered_title}"
    elif normalized == "in_progress":
        rendered_title = f"[in-progress] {rendered_title}"
    mark = "x" if normalized == "done" else " "
    return f"{prefix}- [{mark}] {rendered_title}".rstrip()


def _task_title_without_status_prefix(title: str) -> str:
    for prefix in ("[blocked] ", "[in-progress] "):
        if title.startswith(prefix):
            return title[len(prefix) :]
    return title


def add_task_to_markdown(
    content: str,
    title: str,
    *,
    parent_task_id: int = 0,
    status: str = "open",
) -> tuple[str, int]:
    title = title.strip()
    if not title:
        raise ValueError("title must not be empty")

    lines = content.splitlines()
    if parent_task_id <= 0:
        lines.append(_task_line(0, title, status=status))
        return "\n".join(lines) + "\n", len(lines)

    if parent_task_id > len(lines):
        raise ValueError(f"task_id {parent_task_id} is out of range")
    parent_index = parent_task_id - 1
    parent = _parse_task_line(lines[parent_index])
    if parent is None:
        raise ValueError(f"line {parent_task_id} is not a task item")
    parent_indent, _, _ = parent

    insert_at = parent_index + 1
    while insert_at < len(lines):
        parsed = _parse_task_line(lines[insert_at])
        if parsed is not None and parsed[0] <= parent_indent:
            break
        insert_at += 1

    lines.insert(insert_at, _task_line(parent_indent + 2, title, status=status))
    return "\n".join(lines) + "\n", insert_at + 1


def update_task_in_markdown(
    content: str,
    task_id: int,
    *,
    title: str | None = None,
    status: str | None = None,
) -> str:
    lines = content.splitlines()
    if task_id <= 0 or task_id > len(lines):
        raise ValueError(f"task_id {task_id} is out of range")

    index = task_id - 1
    parsed = _parse_task_line(lines[index])
    if parsed is None:
        raise ValueError(f"line {task_id} is not a task item")
    indent, done, current_title = parsed

    base_title = _task_title_without_status_prefix(current_title)
    if title is not None:
        base_title = title.strip()
        if not base_title:
            raise ValueError("title must not be empty")

    effective_status = status
    if effective_status is None:
        if current_title.startswith("[blocked] "):
            effective_status = "blocked"
        elif current_title.startswith("[in-progress] "):
            effective_status = "in_progress"
        else:
            effective_status = "done" if done else "open"

    lines[index] = _task_line(indent, base_title, status=effective_status)
    return "\n".join(lines) + ("\n" if lines else "")
