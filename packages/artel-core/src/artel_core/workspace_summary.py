"""Shared workspace summary extraction for Artel surfaces."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

_FILE_ARG_KEYS = {"path", "file", "output_path"}
_DIR_ARG_KEYS = {"cwd", "project_dir", "search_dir"}


@dataclass(slots=True)
class ActorStatusSummary:
    title: str
    detail: str = ""
    kind: str = "info"


@dataclass(slots=True)
class TaskSummary:
    title: str
    summary: str = ""
    project_dir: str = ""
    model: str = ""
    thinking_level: str = ""
    follow_mode: bool | None = None
    guidance: list[str] = field(default_factory=list)
    workspace_evidence: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FocusedArtifactSummary:
    path: str = ""
    source: str = ""
    preview: str = ""
    working_set: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TerminalContextSummary:
    command: str = ""
    output: str = ""
    exit_code: int | None = None


@dataclass(slots=True)
class DiffSnapshotSummary:
    loaded_from_git: bool = False
    source_command: str = ""
    output: str = ""
    paths: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolCallSummary:
    name: str
    arguments: str = ""
    paths: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolActivitySummary:
    calls: list[ToolCallSummary] = field(default_factory=list)
    result_content: str = ""
    result_is_error: bool = False


@dataclass(slots=True)
class RecentUpdateSummary:
    role: str
    actor_status: ActorStatusSummary
    content_excerpt: str = ""
    tool_names: list[str] = field(default_factory=list)
    tool_paths: list[str] = field(default_factory=list)
    command: str = ""


@dataclass(slots=True)
class WorkspaceSummary:
    task: TaskSummary
    focused_artifact: FocusedArtifactSummary
    terminal_context: TerminalContextSummary
    diff_snapshot: DiffSnapshotSummary
    tool_activity: list[ToolActivitySummary] = field(default_factory=list)
    recent_updates: list[RecentUpdateSummary] = field(default_factory=list)
    actor_status: ActorStatusSummary | None = None


def _attr(value: Any, name: str, default: Any = "") -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return []


def _parse_tool_arguments(arguments: Any) -> Any:
    if isinstance(arguments, (dict, list)):
        return arguments
    raw = str(arguments or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _append_path_candidate(
    candidates: list[tuple[str, bool]],
    value: Any,
    *,
    is_file: bool,
) -> None:
    if not isinstance(value, str):
        return
    normalized = value.strip()
    if not normalized or normalized.startswith(("http://", "https://")):
        return
    candidates.append((normalized, is_file))


def _walk_path_candidates(
    payload: Any,
    candidates: list[tuple[str, bool]],
    *,
    file_hint: bool = False,
) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            normalized_key = str(key or "").strip().lower()
            if normalized_key in _FILE_ARG_KEYS:
                _append_path_candidate(candidates, value, is_file=True)
                continue
            if normalized_key in _DIR_ARG_KEYS:
                _append_path_candidate(candidates, value, is_file=False)
                continue
            if normalized_key == "files":
                _walk_path_candidates(value, candidates, file_hint=True)
                continue
            if isinstance(value, (dict, list, tuple)):
                _walk_path_candidates(value, candidates, file_hint=file_hint)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            _walk_path_candidates(item, candidates, file_hint=file_hint)
    elif file_hint:
        _append_path_candidate(candidates, payload, is_file=True)


def _extract_path_candidates(tool_call: Any) -> list[tuple[str, bool]]:
    if not isinstance(tool_call, dict):
        return []
    candidates: list[tuple[str, bool]] = []
    _walk_path_candidates(_parse_tool_arguments(tool_call.get("arguments", {})), candidates)
    return candidates


def _latest_task_message(messages: Sequence[Any]) -> str:
    for message in reversed(messages):
        if str(_attr(message, "role", "") or "").strip().lower() != "user":
            continue
        content = str(_attr(message, "content", "") or "").strip()
        if not content or content.startswith("$ ") or content.startswith("Output of `"):
            continue
        return content
    return ""


def _focus_file(messages: Sequence[Any]) -> tuple[str, str, str]:
    for message in reversed(messages):
        tool_calls = _as_list(_attr(message, "tool_calls", []) or [])
        if not tool_calls:
            continue
        preview = ""
        tool_result = _attr(message, "tool_result", None)
        if isinstance(tool_result, dict):
            preview = str(tool_result.get("content", "") or "")
        if not preview:
            preview = str(_attr(message, "content", "") or "")
        for call in reversed(tool_calls):
            candidates = _extract_path_candidates(call)
            if not candidates:
                continue
            file_candidates = [path for path, is_file in candidates if is_file]
            fallback_candidates = [path for path, is_file in candidates if not is_file]
            focus_path = file_candidates[0] if file_candidates else fallback_candidates[0]
            tool_name = str(call.get("name", "tool") or "tool").strip() or "tool"
            return focus_path, tool_name, preview
    return "", "", ""


def _recent_file_activity(messages: Sequence[Any], *, limit: int = 8) -> list[str]:
    seen: set[str] = set()
    paths: list[str] = []
    for message in reversed(messages):
        tool_calls = _as_list(_attr(message, "tool_calls", []) or [])
        if not tool_calls:
            continue
        for call in reversed(tool_calls):
            candidates = _extract_path_candidates(call)
            ordered = [path for path, is_file in candidates if is_file] + [
                path for path, is_file in candidates if not is_file
            ]
            for path in ordered:
                if path in seen:
                    continue
                seen.add(path)
                paths.append(path)
                if len(paths) >= limit:
                    return paths
    return paths


def _follow_working_set_paths(messages: Sequence[Any], *, limit: int = 4) -> list[str]:
    focus_path, _tool_name, _preview = _focus_file(messages)
    recent_paths = _recent_file_activity(messages, limit=max(limit * 2, limit))
    ordered_paths: list[str] = []
    for candidate in [focus_path, *recent_paths]:
        normalized = str(candidate or "").strip()
        if not normalized or normalized in ordered_paths:
            continue
        ordered_paths.append(normalized)
        if len(ordered_paths) >= limit:
            break
    return ordered_paths


def _tool_call_names(tool_calls: Sequence[Any], *, limit: int = 2) -> list[str]:
    names: list[str] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        name = str(call.get("name", "tool") or "").strip() or "tool"
        if name in names:
            continue
        names.append(name)
        if len(names) >= limit:
            break
    return names


def _tool_call_paths(tool_calls: Sequence[Any], *, limit: int = 3) -> list[str]:
    paths: list[str] = []
    for call in tool_calls:
        candidates = _extract_path_candidates(call)
        ordered_candidates = [path for path, is_file in candidates if is_file] + [
            path for path, is_file in candidates if not is_file
        ]
        for path in ordered_candidates:
            normalized = str(path or "").strip()
            if not normalized or normalized in paths:
                continue
            paths.append(normalized)
            if len(paths) >= limit:
                return paths
    return paths


def collect_follow_file_paths(messages: Sequence[Any], *, limit: int = 8) -> list[str]:
    return _recent_file_activity(messages, limit=limit)


def render_follow_updates_note(total_updates: int, *, limit: int) -> str:
    if total_updates <= 0:
        return ""
    if total_updates > limit:
        return (
            f"Follow-first mode — showing latest {limit} updates. "
            "Full thread stays in session history."
        )
    return "Follow-first mode — showing brief updates instead of the full thread."


def collect_follow_update_messages(
    messages: Sequence[Any],
    *,
    limit: int | None = None,
) -> list[Any]:
    updates: list[Any] = []
    for message in messages:
        role = str(_attr(message, "role", "") or "").strip().lower()
        content = str(_attr(message, "content", "") or "").strip()
        if role in {"assistant", "tool", "error"}:
            updates.append(message)
            continue
        if role == "user" and content.startswith("$ "):
            updates.append(message)
    if limit is None or limit <= 0:
        return updates
    return updates[-limit:]


def _find_named_string(payload: Any, names: set[str]) -> str:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key or "").strip().lower() in names and isinstance(value, str):
                candidate = value.strip()
                if candidate:
                    return candidate
            nested = _find_named_string(value, names)
            if nested:
                return nested
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            nested = _find_named_string(item, names)
            if nested:
                return nested
    return ""


def _tool_call_command(tool_calls: Sequence[Any]) -> str:
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        command = _find_named_string(
            _parse_tool_arguments(call.get("arguments", {})),
            {"command", "cmd"},
        )
        if command:
            return command
    return ""


def _command_update_status(command: str) -> str:
    normalized = str(command or "").strip().lower()
    if not normalized:
        return "Running command"
    if any(
        token in normalized
        for token in (
            "pytest",
            "go test",
            "cargo test",
            "npm test",
            "pnpm test",
            "yarn test",
            "bun test",
            "ctest",
        )
    ):
        return "Running tests"
    if any(
        token in normalized
        for token in ("lint", "ruff", "mypy", "pyright", "tsc", "typecheck", "check")
    ):
        return "Running checks"
    if any(
        token in normalized
        for token in (
            "git diff",
            "git --no-pager diff",
            "git status",
            "git --no-pager status",
            "git show",
            "git log",
        )
    ):
        return "Reviewing diff"
    if any(
        token in normalized
        for token in (
            "rg ",
            "grep ",
            "find ",
            "fd ",
            "ls",
            "tree",
            "cat ",
            "sed ",
            "head ",
            "tail ",
        )
    ):
        return "Inspecting workspace"
    return "Running command"


def _assistant_content_status(content: str) -> str:
    normalized = str(content or "").strip().lower()
    if not normalized:
        return "Updating progress"
    if any(
        token in normalized
        for token in ("next step", "next update", "latest workspace", "prepare", "preparing")
    ):
        return "Preparing next step"
    if any(
        token in normalized
        for token in ("inspect", "opened", "checked", "looked at", "reading", "read ")
    ):
        return "Inspecting files"
    if any(token in normalized for token in ("diff", "change set", "patch", "changes")):
        return "Reviewing diff"
    if any(token in normalized for token in ("edit", "update file", "writing", "wrote", "patched")):
        return "Updating files"
    return "Updating progress"


def _tool_output_status(content: str) -> str:
    normalized = str(content or "").strip().lower()
    if not normalized:
        return "Reviewing output"
    if "passed" in normalized and "failed" not in normalized and "error" not in normalized:
        return "Tests passed"
    if any(
        token in normalized
        for token in ("failed", "traceback", "assertionerror", "exception", "error")
    ):
        return "Tests failed"
    if any(
        token in normalized
        for token in (
            "file changed",
            "diff --git",
            "working tree clean",
            "deleted:",
            "modified:",
        )
    ):
        return "Reviewing diff"
    return "Reviewing output"


def _recent_tool_names(messages: Sequence[Any], *, limit: int = 3) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    saw_result_without_call = False
    for message in reversed(messages):
        tool_calls = _as_list(_attr(message, "tool_calls", []) or [])
        if tool_calls:
            for call in reversed(tool_calls):
                name = call.get("name", "tool") if isinstance(call, dict) else "tool"
                normalized = str(name or "").strip() or "tool"
                if normalized in seen:
                    continue
                seen.add(normalized)
                names.append(normalized)
                if len(names) >= limit:
                    return names
            continue
        if isinstance(_attr(message, "tool_result", None), dict):
            saw_result_without_call = True
    if saw_result_without_call and not names:
        return ["tool result"]
    return names


def _format_path_preview(paths: Sequence[str] | None, *, limit: int = 3) -> str:
    normalized_paths: list[str] = []
    for raw_path in paths or []:
        candidate = str(raw_path or "").strip()
        if not candidate or candidate in normalized_paths:
            continue
        normalized_paths.append(candidate)
    if not normalized_paths:
        return ""
    preview = ", ".join(normalized_paths[:limit])
    if len(normalized_paths) > limit:
        preview += f" (+{len(normalized_paths) - limit} more)"
    return preview


def format_code_item_preview(items: Sequence[str] | None, *, limit: int = 2) -> str:
    normalized_items: list[str] = []
    for item in items or []:
        candidate = str(item or "").strip()
        if not candidate or candidate in normalized_items:
            continue
        normalized_items.append(candidate)
    if not normalized_items:
        return ""
    preview = ", ".join(f"`{item}`" for item in normalized_items[:limit])
    if len(normalized_items) > limit:
        preview += f" (+{len(normalized_items) - limit} more)"
    return preview


def _parse_exit_code(content: str) -> int | None:
    normalized = str(content or "").strip()
    if not normalized.startswith("exit code:"):
        return None
    raw_code = normalized.partition(":")[2].strip()
    try:
        return int(raw_code)
    except ValueError:
        return None


def _terminal_context(
    messages: Sequence[Any],
    *,
    command: str = "",
    output: str = "",
    exit_code: int | None = None,
) -> tuple[str, str, int | None]:
    normalized_command = str(command or "").strip()
    if normalized_command:
        return normalized_command, str(output or "").rstrip(), exit_code
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        role = str(_attr(message, "role", "") or "").strip().lower()
        content = str(_attr(message, "content", "") or "").strip()
        if role != "user" or not content.startswith("$ "):
            continue
        command_text = content[2:].strip()
        outputs: list[str] = []
        inferred_exit_code: int | None = None
        cursor = index + 1
        while cursor < len(messages):
            next_message = messages[cursor]
            next_role = str(_attr(next_message, "role", "") or "").strip().lower()
            next_content = str(_attr(next_message, "content", "") or "").rstrip()
            if next_role == "tool":
                if next_content:
                    outputs.append(next_content)
                cursor += 1
                continue
            if next_role == "error":
                if next_content:
                    outputs.append(next_content)
                inferred_exit_code = _parse_exit_code(next_content)
                cursor += 1
                continue
            break
        return (
            command_text,
            "\n\n".join(part for part in outputs if part).rstrip(),
            inferred_exit_code,
        )
    return "", "", None


def _status_kind(title: str) -> str:
    normalized = str(title or "").strip().lower()
    if any(token in normalized for token in ("failed", "error", "denied")):
        return "error"
    if any(token in normalized for token in ("passed", "ok", "complete", "completed")):
        return "success"
    return "info"


def _follow_workspace_evidence_lines(
    messages: Sequence[Any],
    *,
    git_snapshot_paths: Sequence[str] | None = None,
    command: str = "",
    output: str = "",
    exit_code: int | None = None,
) -> list[str]:
    lines: list[str] = []
    focus_path, _tool_name, _preview = _focus_file(messages)
    if focus_path:
        lines.append(f"current file: {focus_path}")
    git_focus = _format_path_preview(git_snapshot_paths)
    if git_focus:
        lines.append(f"git focus: {git_focus}")
    terminal_command, _terminal_output, _terminal_exit_code = _terminal_context(
        messages,
        command=command,
        output=output,
        exit_code=exit_code,
    )
    if terminal_command:
        lines.append(f"recent terminal: {terminal_command}")
    tool_names = _recent_tool_names(messages)
    if tool_names == ["tool result"]:
        lines.append("tool trail: tool result available")
    elif tool_names:
        lines.append("tool trail: " + ", ".join(tool_names))
    return lines


def has_follow_workspace_context(
    messages: Sequence[Any],
    *,
    git_snapshot_paths: Sequence[str] | None = None,
    command: str = "",
    output: str = "",
    exit_code: int | None = None,
) -> bool:
    if git_snapshot_paths:
        return True
    terminal_command, _terminal_output, _terminal_exit_code = _terminal_context(
        messages,
        command=command,
        output=output,
        exit_code=exit_code,
    )
    if terminal_command:
        return True
    for message in reversed(messages):
        tool_calls = _as_list(_attr(message, "tool_calls", []) or [])
        tool_result = _attr(message, "tool_result", None)
        if tool_calls or isinstance(tool_result, dict):
            return True
    return False


def summarize_task(
    session: Any,
    messages: Sequence[Any],
    *,
    default_project_dir: str = "",
    default_model: str = "",
    follow_mode: bool | None = None,
    git_snapshot_paths: Sequence[str] | None = None,
    command: str = "",
    output: str = "",
    exit_code: int | None = None,
) -> TaskSummary:
    task_message = _latest_task_message(messages)
    title = str(_attr(session, "title", "") or "").strip()
    if not title and task_message:
        title = task_message.splitlines()[0].strip()
    if not title:
        title = "(new task)"
    project_dir = str(_attr(session, "project_dir", "") or "").strip() or default_project_dir
    model = str(_attr(session, "model", "") or "").strip() or default_model
    thinking = str(_attr(session, "thinking_level", "") or "").strip()
    guidance: list[str] = []
    evidence: list[str] = []
    if follow_mode is False:
        guidance = [
            (
                "Task-first mode is active. Follow panels appear after file, tool, "
                "or terminal activity."
            ),
            "ask Artel to inspect or edit a file",
            "use ! cmd to send shell output to Artel",
            "use !! cmd to keep shell output local",
        ]
    elif follow_mode is True:
        evidence = _follow_workspace_evidence_lines(
            messages,
            git_snapshot_paths=git_snapshot_paths,
            command=command,
            output=output,
            exit_code=exit_code,
        )
    return TaskSummary(
        title=title,
        summary=task_message,
        project_dir=project_dir,
        model=model,
        thinking_level=thinking,
        follow_mode=follow_mode,
        guidance=guidance,
        workspace_evidence=evidence,
    )


def summarize_focused_artifact(messages: Sequence[Any]) -> FocusedArtifactSummary:
    focus_path, tool_name, preview = _focus_file(messages)
    return FocusedArtifactSummary(
        path=focus_path,
        source=tool_name,
        preview=preview,
        working_set=_follow_working_set_paths(messages),
    )


def summarize_terminal_context(
    messages: Sequence[Any],
    *,
    command: str = "",
    output: str = "",
    exit_code: int | None = None,
) -> TerminalContextSummary:
    terminal_command, terminal_output, terminal_exit_code = _terminal_context(
        messages,
        command=command,
        output=output,
        exit_code=exit_code,
    )
    return TerminalContextSummary(
        command=terminal_command,
        output=terminal_output,
        exit_code=terminal_exit_code,
    )


def summarize_diff_snapshot(
    messages: Sequence[Any],
    *,
    git_snapshot_loaded: bool = False,
    git_snapshot_command: str = "",
    git_snapshot_output: str = "",
    git_snapshot_paths: Sequence[str] | None = None,
) -> DiffSnapshotSummary:
    if git_snapshot_loaded:
        return DiffSnapshotSummary(
            loaded_from_git=True,
            source_command=str(git_snapshot_command or "").strip(),
            output=str(git_snapshot_output or "").strip() or "Working tree clean.",
            paths=[
                str(path or "").strip()
                for path in git_snapshot_paths or []
                if str(path or "").strip()
            ],
        )
    return DiffSnapshotSummary(
        loaded_from_git=False,
        paths=_recent_file_activity(messages),
    )


def summarize_tool_activity(message: Any) -> ToolActivitySummary:
    tool_calls = _as_list(_attr(message, "tool_calls", []) or [])
    calls: list[ToolCallSummary] = []
    for call in tool_calls:
        if not isinstance(call, dict):
            continue
        arguments = call.get("arguments", "")
        calls.append(
            ToolCallSummary(
                name=str(call.get("name", "tool") or "tool").strip() or "tool",
                arguments=str(arguments),
                paths=_tool_call_paths([call], limit=6),
            )
        )
    tool_result = _attr(message, "tool_result", None)
    result_content = ""
    result_is_error = False
    if isinstance(tool_result, dict):
        result_content = str(tool_result.get("content", "") or "")
        result_is_error = bool(tool_result.get("is_error", False))
    return ToolActivitySummary(
        calls=calls,
        result_content=result_content,
        result_is_error=result_is_error,
    )


def summarize_recent_update(message: Any) -> RecentUpdateSummary:
    role = str(_attr(message, "role", "assistant") or "assistant").strip().lower()
    content = str(_attr(message, "content", "") or "").strip()
    if role == "assistant":
        tool_calls = _as_list(_attr(message, "tool_calls", []) or [])
        tool_names = _tool_call_names(tool_calls)
        tool_paths = _tool_call_paths(tool_calls)
        tool_command = _tool_call_command(tool_calls)
        if tool_command:
            status = ActorStatusSummary(
                title=_command_update_status(tool_command),
                detail=tool_command,
                kind=_status_kind(_command_update_status(tool_command)),
            )
            return RecentUpdateSummary(
                role=role,
                actor_status=status,
                content_excerpt=content,
                tool_names=tool_names,
                tool_paths=tool_paths,
                command=tool_command,
            )
        if tool_names and tool_paths:
            title = "Inspecting files"
            lower_names = [name.lower() for name in tool_names]
            if any(token in name for name in lower_names for token in ("edit", "write", "patch")):
                title = "Updating files"
            elif any(token in name for name in lower_names for token in ("diff", "git")):
                title = "Reviewing diff"
            status = ActorStatusSummary(
                title=title,
                detail=_format_path_preview(tool_paths),
                kind=_status_kind(title),
            )
            return RecentUpdateSummary(
                role=role,
                actor_status=status,
                content_excerpt=content,
                tool_names=tool_names,
                tool_paths=tool_paths,
            )
        if tool_names:
            title = "Using tools"
            lower_names = [name.lower() for name in tool_names]
            if any(
                token in name
                for name in lower_names
                for token in ("read", "grep", "search", "open")
            ):
                title = "Inspecting files"
            elif any(token in name for name in lower_names for token in ("edit", "write", "patch")):
                title = "Updating files"
            elif any(token in name for name in lower_names for token in ("diff", "git")):
                title = "Reviewing diff"
            status = ActorStatusSummary(
                title=title,
                detail=", ".join(tool_names),
                kind=_status_kind(title),
            )
            return RecentUpdateSummary(
                role=role,
                actor_status=status,
                content_excerpt=content,
                tool_names=tool_names,
            )
        if content:
            title = _assistant_content_status(content)
            return RecentUpdateSummary(
                role=role,
                actor_status=ActorStatusSummary(
                    title=title,
                    detail=content,
                    kind=_status_kind(title),
                ),
                content_excerpt=content,
            )
        if isinstance(_attr(message, "tool_result", None), dict):
            return RecentUpdateSummary(
                role=role,
                actor_status=ActorStatusSummary(
                    title="Reviewing output",
                    detail="Collected tool output.",
                    kind="info",
                ),
            )
        return RecentUpdateSummary(
            role=role,
            actor_status=ActorStatusSummary(title="Updating progress", kind="info"),
        )
    if role == "tool":
        title = _tool_output_status(content)
        return RecentUpdateSummary(
            role=role,
            actor_status=ActorStatusSummary(
                title=title,
                detail=content,
                kind=_status_kind(title),
            ),
            content_excerpt=content,
        )
    if role == "error":
        title = "Command failed" if content.startswith("exit code:") else "Error reported"
        return RecentUpdateSummary(
            role=role,
            actor_status=ActorStatusSummary(
                title=title,
                detail=content,
                kind="error",
            ),
            content_excerpt=content,
        )
    if role == "user" and content.startswith("$ "):
        command = content[2:].strip()
        title = _command_update_status(command)
        return RecentUpdateSummary(
            role=role,
            actor_status=ActorStatusSummary(
                title=title,
                detail=command,
                kind=_status_kind(title),
            ),
            content_excerpt=content,
            command=command,
        )
    if content:
        return RecentUpdateSummary(
            role=role,
            actor_status=ActorStatusSummary(
                title="Update recorded",
                detail=content,
                kind="info",
            ),
            content_excerpt=content,
        )
    return RecentUpdateSummary(
        role=role,
        actor_status=ActorStatusSummary(title="Update recorded", kind="info"),
    )


def summarize_recent_updates(
    messages: Sequence[Any],
    *,
    limit: int | None = None,
) -> list[RecentUpdateSummary]:
    return [
        summarize_recent_update(message)
        for message in collect_follow_update_messages(messages, limit=limit)
    ]


def summarize_workspace(
    session: Any,
    messages: Sequence[Any],
    *,
    default_project_dir: str = "",
    default_model: str = "",
    follow_mode: bool | None = None,
    git_snapshot_loaded: bool = False,
    git_snapshot_command: str = "",
    git_snapshot_output: str = "",
    git_snapshot_paths: Sequence[str] | None = None,
    command: str = "",
    output: str = "",
    exit_code: int | None = None,
    recent_update_limit: int | None = None,
) -> WorkspaceSummary:
    resolved_follow_mode = (
        follow_mode
        if follow_mode is not None
        else has_follow_workspace_context(
            messages,
            git_snapshot_paths=git_snapshot_paths,
            command=command,
            output=output,
            exit_code=exit_code,
        )
    )
    recent_updates = summarize_recent_updates(messages, limit=recent_update_limit)
    actor_status = recent_updates[-1].actor_status if recent_updates else None
    tool_activity: list[ToolActivitySummary] = []
    for message in reversed(messages):
        activity = summarize_tool_activity(message)
        if activity.calls or activity.result_content:
            tool_activity.append(activity)
        if len(tool_activity) >= 2:
            break
    return WorkspaceSummary(
        task=summarize_task(
            session,
            messages,
            default_project_dir=default_project_dir,
            default_model=default_model,
            follow_mode=resolved_follow_mode,
            git_snapshot_paths=git_snapshot_paths,
            command=command,
            output=output,
            exit_code=exit_code,
        ),
        focused_artifact=summarize_focused_artifact(messages),
        terminal_context=summarize_terminal_context(
            messages,
            command=command,
            output=output,
            exit_code=exit_code,
        ),
        diff_snapshot=summarize_diff_snapshot(
            messages,
            git_snapshot_loaded=git_snapshot_loaded,
            git_snapshot_command=git_snapshot_command,
            git_snapshot_output=git_snapshot_output,
            git_snapshot_paths=git_snapshot_paths,
        ),
        tool_activity=tool_activity,
        recent_updates=recent_updates,
        actor_status=actor_status,
    )


__all__ = [
    "ActorStatusSummary",
    "DiffSnapshotSummary",
    "FocusedArtifactSummary",
    "RecentUpdateSummary",
    "TaskSummary",
    "TerminalContextSummary",
    "ToolActivitySummary",
    "ToolCallSummary",
    "WorkspaceSummary",
    "collect_follow_file_paths",
    "collect_follow_update_messages",
    "format_code_item_preview",
    "render_follow_updates_note",
    "has_follow_workspace_context",
    "summarize_diff_snapshot",
    "summarize_focused_artifact",
    "summarize_recent_update",
    "summarize_recent_updates",
    "summarize_task",
    "summarize_terminal_context",
    "summarize_tool_activity",
    "summarize_workspace",
]
