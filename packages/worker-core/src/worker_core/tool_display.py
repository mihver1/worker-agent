"""Helpers for compact tool-call labels and readable file-diff displays."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Any

_MAX_PREVIEW_LINES = 120
_MAX_INLINE_CHARS = 160
_MAX_BLOCK_CHARS = 12_000
_BLOCK_PRESERVING_TOOLS = {"read", "bash", "grep", "ripgrep", "ag"}


@dataclass(slots=True)
class ToolCallDisplay:
    title: str
    body: str


@dataclass(slots=True)
class ToolResultDisplay:
    title: str
    body: str
    markdown: bool = False
    kind: str = "text"
    status_badge: str = ""
    status_variant: str = "neutral"


def _relative_label(path: str) -> str:
    normalized = str(path or "").strip()
    return normalized or "(unknown path)"


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + 1


def _inline(text: Any, *, limit: int = _MAX_INLINE_CHARS) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 1, 0)].rstrip() + "…"


def _block_stats(label: str, text: str) -> str:
    lines = _line_count(text)
    chars = len(text)
    return f"{label}: {lines} line(s), {chars} char(s)"


def _preserve_block(text: Any, *, limit: int = _MAX_BLOCK_CHARS) -> str:
    normalized = str(text or "")
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - len("\n… (truncated)"), 0)].rstrip() + "\n… (truncated)"


def _tool_result_kind(tool_name: str, content: str) -> str:
    normalized_tool = str(tool_name or "").strip().lower()
    if normalized_tool in _BLOCK_PRESERVING_TOOLS:
        return "block"
    if "\n" in str(content or ""):
        return "block"
    return "text"


def format_tool_call_display(tool_name: str, args: dict[str, Any]) -> ToolCallDisplay:
    path = str(args.get("path", "") or "").strip()
    if tool_name == "bash":
        command = str(args.get("command", "") or "")
        return ToolCallDisplay(
            title="⚙ bash",
            body=_inline(command, limit=300) or "(empty command)",
        )
    if tool_name == "read":
        title = f"⚙ read {path}" if path else "⚙ read"
        start_line = args.get("start_line")
        end_line = args.get("end_line")
        details: list[str] = []
        if start_line:
            details.append(f"start_line={start_line}")
        if end_line:
            details.append(f"end_line={end_line}")
        return ToolCallDisplay(title=title, body=", ".join(details) if details else "")
    if tool_name == "write":
        content = str(args.get("content", "") or "")
        title = f"⚙ write {path}" if path else "⚙ write"
        return ToolCallDisplay(title=title, body=_block_stats("content", content))
    if tool_name == "edit":
        search = str(args.get("search", "") or "")
        replace = str(args.get("replace", "") or "")
        title = f"⚙ edit {path}" if path else "⚙ edit"
        body = "\n".join(
            [
                _block_stats("search", search),
                _block_stats("replace", replace),
            ]
        )
        return ToolCallDisplay(title=title, body=body)
    if tool_name.startswith("lsp_"):
        title = f"⚙ {tool_name} {path}".strip() if path else f"⚙ {tool_name}"
        details: list[str] = []
        query = str(args.get("query", "") or "").strip()
        if query:
            details.append(f"query={query!r}")
        line = args.get("line")
        column = args.get("column")
        if line:
            details.append(f"line={line}")
        if column:
            details.append(f"column={column}")
        max_results = args.get("max_results")
        if max_results:
            details.append(f"max_results={max_results}")
        return ToolCallDisplay(title=title, body=", ".join(details))
    if args:
        rendered = ", ".join(f"{key}={_inline(value)!r}" for key, value in args.items())
        return ToolCallDisplay(title=f"⚙ {tool_name}", body=rendered)
    return ToolCallDisplay(title=f"⚙ {tool_name}", body="")


def build_file_diff_display(
    *,
    tool_name: str,
    path: str,
    before: str,
    after: str,
) -> dict[str, Any]:
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    diff_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=path,
            tofile=path,
            lineterm="",
            n=3,
        )
    )
    added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
    truncated = len(diff_lines) > _MAX_PREVIEW_LINES
    preview_lines = diff_lines[:_MAX_PREVIEW_LINES]
    diff_text = "\n".join(preview_lines)
    if truncated:
        diff_text += "\n…"
    return {
        "kind": "file_diff",
        "tool": tool_name,
        "path": _relative_label(path),
        "added_lines": added,
        "removed_lines": removed,
        "before_lines": len(before_lines),
        "after_lines": len(after_lines),
        "truncated": truncated,
        "diff": diff_text,
    }


def format_tool_result_display(
    *,
    tool_name: str,
    content: str,
    is_error: bool,
    display: dict[str, Any] | None = None,
) -> ToolResultDisplay:
    if isinstance(display, dict) and display.get("kind") == "file_diff":
        path = str(display.get("path", "") or "")
        added = int(display.get("added_lines", 0) or 0)
        removed = int(display.get("removed_lines", 0) or 0)
        icon = "✗" if is_error else "✓"
        title = path or tool_name or "file"
        status_badge = f"+{added}  -{removed}"
        diff_text = str(display.get("diff", "") or "").strip()
        if not diff_text:
            diff_text = _inline(content, limit=400)
            return ToolResultDisplay(
                title=title,
                body=diff_text,
                markdown=False,
                kind="text",
                status_badge=status_badge,
                status_variant="error" if is_error else "success",
            )
        return ToolResultDisplay(
            title=title,
            body=diff_text,
            markdown=False,
            kind="file_diff",
            status_badge=status_badge,
            status_variant="error" if is_error else "success",
        )

    icon = "✗" if is_error else "✓"
    path = ""
    if isinstance(display, dict):
        path = str(display.get("path", "") or "").strip()
    title_parts = [icon]
    if tool_name:
        title_parts.append(tool_name)
    if path:
        title_parts.append(path)
    title = " ".join(part for part in title_parts if part).strip()
    kind = _tool_result_kind(tool_name, content)
    body = _preserve_block(content) if kind == "block" else _inline(content, limit=400)
    return ToolResultDisplay(
        title=title,
        body=body,
        markdown=False,
        kind=kind,
        status_variant="error" if is_error else "success",
    )


__all__ = [
    "ToolCallDisplay",
    "ToolResultDisplay",
    "build_file_diff_display",
    "format_tool_call_display",
    "format_tool_result_display",
]
