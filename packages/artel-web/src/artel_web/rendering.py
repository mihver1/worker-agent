"""Rendering helpers for Artel Web panels."""

from __future__ import annotations

import json
from typing import Any

from artel_core import workspace_summary


def _truncate_inline(text: str, limit: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 1, 0)].rstrip() + "…"


def _truncate_block(text: str, limit: int) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 2, 0)].rstrip() + "\n…"


def _inline_excerpt(text: str, limit: int) -> str:
    normalized = " ".join(str(text or "").replace("`", "'").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(limit - 1, 0)].rstrip() + "…"


def collect_follow_file_paths(messages: list[Any], *, limit: int = 8) -> list[str]:
    return workspace_summary.collect_follow_file_paths(messages, limit=limit)


def collect_follow_update_messages(messages: list[Any], *, limit: int | None = None) -> list[Any]:
    return workspace_summary.collect_follow_update_messages(messages, limit=limit)


def render_follow_updates_note(total_updates: int, *, limit: int) -> str:
    return workspace_summary.render_follow_updates_note(total_updates, limit=limit)


def _render_follow_status_block(title: str, detail: str = "") -> str:
    lines = [f"**{title}**"]
    normalized_detail = str(detail or "").strip()
    if normalized_detail:
        lines.extend(["", normalized_detail])
    return "\n".join(lines)


def _render_workspace_evidence_line(line: str) -> str:
    label, separator, raw_value = str(line or "").partition(": ")
    if not separator:
        return f"- {_inline_excerpt(line, 140)}"
    value = raw_value.strip()
    if not value:
        return f"- {label}:"
    if label in {"git focus", "tool trail"}:
        tokens = [token.strip() for token in value.split(",") if token.strip()]
        rendered_tokens: list[str] = []
        for token in tokens:
            if token == "tool result available" or token.startswith("(+"):
                rendered_tokens.append(token)
            else:
                rendered_tokens.append(f"`{token}`")
        return f"- {label}: {', '.join(rendered_tokens)}"
    return f"- {label}: `{value}`"


def has_follow_workspace_context(
    messages: list[Any],
    *,
    git_snapshot_paths: list[str] | None = None,
    command: str = "",
    output: str = "",
    exit_code: int | None = None,
) -> bool:
    return workspace_summary.has_follow_workspace_context(
        messages,
        git_snapshot_paths=git_snapshot_paths,
        command=command,
        output=output,
        exit_code=exit_code,
    )


def message_role_badge(role: str) -> str:
    normalized = (role or "assistant").strip().lower()
    mapping = {
        "user": "YOU",
        "assistant": "ARTEL",
        "tool": "TOOL",
        "system": "SYSTEM",
        "reasoning": "THINKING",
    }
    return mapping.get(normalized, normalized.upper())


def render_message_markdown(message: Any) -> str:
    role = getattr(message, "role", "assistant") or "assistant"
    content = getattr(message, "content", "") or ""
    reasoning = getattr(message, "reasoning", "") or ""

    lines: list[str] = [f"**{message_role_badge(role)}**"]
    if reasoning:
        lines.extend(["", "**reasoning**", "", f"```text\n{reasoning}\n```"])
    if content:
        lines.extend(["", content])
    return "\n".join(lines)


def render_follow_update_summary_markdown(summary: Any) -> str:
    detail = ""
    if summary.command:
        detail = f"`{_truncate_inline(summary.command, 120)}`"
    elif summary.tool_paths:
        detail = workspace_summary.format_code_item_preview(summary.tool_paths, limit=3)
    elif summary.tool_names:
        detail = workspace_summary.format_code_item_preview(summary.tool_names)
    elif summary.actor_status.detail:
        detail = _inline_excerpt(summary.actor_status.detail, 140)
    elif summary.content_excerpt:
        detail = _inline_excerpt(summary.content_excerpt, 140)
    elif summary.role == "tool":
        detail = "Output received."
    elif summary.role == "error":
        detail = "Error reported."
    return _render_follow_status_block(summary.actor_status.title, detail)


def render_follow_update_markdown(message: Any) -> str:
    return render_follow_update_summary_markdown(workspace_summary.summarize_recent_update(message))


def render_tool_activity_summary_markdown(summary: Any) -> str:
    lines: list[str] = []
    if summary.calls:
        lines.append("**tool calls**")
        for call in summary.calls:
            arg_preview = str(call.arguments)
            if len(arg_preview) > 160:
                arg_preview = arg_preview[:160] + "…"
            lines.append("")
            lines.append(f"- `{call.name}` — args: `{arg_preview}`")
    if summary.result_content:
        label = "tool result" if not summary.result_is_error else "tool error"
        preview = str(summary.result_content)
        if len(preview) > 600:
            preview = preview[:600] + "\n…"
        if lines:
            lines.append("")
        lines.extend([f"**{label}**", "", f"```text\n{preview}\n```"])
    return "\n".join(lines)


def render_tool_activity_markdown(message: Any) -> str:
    return render_tool_activity_summary_markdown(workspace_summary.summarize_tool_activity(message))


def render_follow_task_summary_markdown(summary: Any) -> str:
    lines = [f"**{summary.title}**"]
    task_summary = _truncate_block(summary.summary, 360)
    if task_summary and task_summary != summary.title:
        lines.extend(["", task_summary])
    details: list[str] = []
    if summary.project_dir:
        details.append(f"- project: `{summary.project_dir}`")
    if summary.model:
        details.append(f"- model: `{summary.model}`")
    if summary.thinking_level and summary.thinking_level != "off":
        details.append(f"- thinking: `{summary.thinking_level}`")
    if details:
        lines.extend(["", *details])
    if summary.follow_mode is False and summary.guidance:
        lines.extend(["", summary.guidance[0], ""])
        lines.extend(
            [
                f"- {summary.guidance[1]}",
                "- use `! cmd` to send shell output to Artel",
                "- use `!! cmd` to keep shell output local",
            ]
        )
    elif summary.follow_mode is True and summary.workspace_evidence:
        lines.extend(
            [
                "",
                "**Workspace evidence**",
                "",
                *[_render_workspace_evidence_line(line) for line in summary.workspace_evidence],
            ]
        )
    return "\n".join(lines)


def render_follow_task_markdown(
    session: Any,
    messages: list[Any],
    *,
    default_project_dir: str = "",
    default_model: str = "",
    follow_mode: bool | None = None,
    git_snapshot_paths: list[str] | None = None,
    command: str = "",
    output: str = "",
    exit_code: int | None = None,
) -> str:
    return render_follow_task_summary_markdown(
        workspace_summary.summarize_task(
            session,
            messages,
            default_project_dir=default_project_dir,
            default_model=default_model,
            follow_mode=follow_mode,
            git_snapshot_paths=git_snapshot_paths,
            command=command,
            output=output,
            exit_code=exit_code,
        )
    )


def render_follow_file_summary_markdown(summary: Any) -> str:
    if not summary.path:
        return (
            "No file is in focus yet.\n\n"
            "The next file-oriented tool call will pin the workspace here."
        )
    lines = [f"**{summary.path}**"]
    if summary.source:
        lines.extend(["", f"- source: `{summary.source}`"])
    if len(summary.working_set) > 1:
        lines.extend(["", "- working set:"])
        lines.extend(f"  - `{path}`" for path in summary.working_set)
    preview_block = _truncate_block(summary.preview, 1200)
    if preview_block:
        lines.extend(["", "```text", preview_block, "```"])
    return "\n".join(lines)


def render_follow_file_markdown(messages: list[Any]) -> str:
    return render_follow_file_summary_markdown(
        workspace_summary.summarize_focused_artifact(messages)
    )


def render_follow_diff_summary_markdown(summary: Any) -> str:
    if summary.loaded_from_git:
        lines = ["**Git workspace snapshot**"]
        if summary.paths:
            lines.extend(
                [
                    "",
                    "- focus paths:",
                    *[f"  - `{path}`" for path in summary.paths],
                ]
            )
        if summary.source_command:
            lines.extend(["", f"- source: `{_truncate_inline(summary.source_command, 160)}`"])
        lines.extend(["", "```text", _truncate_block(summary.output, 1400), "```"])
        return "\n".join(lines)
    if not summary.paths:
        return "No change set is visible yet.\n\nRecent file-oriented tool calls will show up here."
    return "\n".join(
        [
            "**Recent change set**",
            "",
            "```diff",
            *[f"+ {path}" for path in summary.paths],
            "```",
        ]
    )


def render_follow_diff_markdown(
    messages: list[Any],
    *,
    git_snapshot_loaded: bool = False,
    git_snapshot_command: str = "",
    git_snapshot_output: str = "",
    git_snapshot_paths: list[str] | None = None,
) -> str:
    return render_follow_diff_summary_markdown(
        workspace_summary.summarize_diff_snapshot(
            messages,
            git_snapshot_loaded=git_snapshot_loaded,
            git_snapshot_command=git_snapshot_command,
            git_snapshot_output=git_snapshot_output,
            git_snapshot_paths=git_snapshot_paths,
        )
    )


def render_follow_terminal_summary_markdown(summary: Any) -> str:
    if not summary.command:
        return (
            "No terminal context yet.\n\n"
            "`! cmd` sends shell output to Artel and `!! cmd` keeps it local."
        )
    lines = [f"- command: `{summary.command}`"]
    if summary.exit_code is not None:
        lines.append(f"- exit code: `{summary.exit_code}`")
    lines.extend(
        [
            "",
            "```text",
            _truncate_block(summary.output or "(no output)", 1200),
            "```",
        ]
    )
    return "\n".join(lines)


def render_follow_terminal_markdown(
    messages: list[Any],
    *,
    command: str = "",
    output: str = "",
    exit_code: int | None = None,
) -> str:
    return render_follow_terminal_summary_markdown(
        workspace_summary.summarize_terminal_context(
            messages,
            command=command,
            output=output,
            exit_code=exit_code,
        )
    )


def render_follow_tool_activity_summaries_markdown(summaries: list[Any]) -> str:
    blocks: list[str] = []
    for summary in summaries:
        block = render_tool_activity_summary_markdown(summary)
        if block:
            blocks.append(block)
    if not blocks:
        return "No tool activity yet."
    return "\n\n---\n\n".join(blocks)


def render_follow_tool_activity_markdown(messages: list[Any]) -> str:
    summaries: list[Any] = []
    for message in reversed(messages):
        summary = workspace_summary.summarize_tool_activity(message)
        if summary.calls or summary.result_content:
            summaries.append(summary)
        if len(summaries) >= 2:
            break
    return render_follow_tool_activity_summaries_markdown(summaries)


def render_tree_markdown(nodes: list[Any]) -> str:
    if not nodes:
        return "No message tree available."
    lines = ["### Session tree"]
    for index, node in enumerate(nodes):
        content = (getattr(node, "content", "") or "").replace("\n", " ").strip()
        if len(content) > 80:
            content = content[:80] + "…"
        lines.append(
            f"- `{index}` id={getattr(node, 'id', 0)} parent={getattr(node, 'parent_id', None)} "
            f"[{getattr(node, 'role', '')}] {content}"
        )
    return "\n".join(lines)


def render_prompts_markdown(prompts: list[Any]) -> str:
    if not prompts:
        return "No prompts available."
    lines = ["### Prompts"]
    for prompt in prompts:
        name = getattr(prompt, "name", "")
        preview = getattr(prompt, "preview", "")
        lines.append(f"- **{name}** — {preview}")
    return "\n".join(lines)


def render_skills_markdown(skills: list[Any]) -> str:
    if not skills:
        return "No skills available."
    lines = ["### Skills"]
    for skill in skills:
        name = getattr(skill, "name", "")
        description = getattr(skill, "description", "")
        lines.append(f"- **{name}** — {description}")
    return "\n".join(lines)


def render_extension_commands_markdown(commands: list[Any]) -> str:
    if not commands:
        return "No extension commands available for this session."
    lines = ["### Extension commands"]
    for command in commands:
        lines.append(f"- `/{getattr(command, 'name', '')}`")
    return "\n".join(lines)


def render_installed_extensions_markdown(extensions: list[Any]) -> str:
    if not extensions:
        return "No installed extensions found."
    lines = ["### Installed extensions"]
    for extension in extensions:
        name = getattr(extension, "name", "")
        version = getattr(extension, "version", "")
        source = getattr(extension, "source", "")
        details = f"v{version}" if version else "version unknown"
        if source:
            lines.append(f"- **{name}** — {details}  \n  source: `{source}`")
        else:
            lines.append(f"- **{name}** — {details}")
    return "\n".join(lines)


def render_extension_batch_update_markdown(result: Any) -> str:
    entries = getattr(result, "results", []) or []
    if not entries:
        return "No extension updates were reported."
    lines = ["### Extension update results"]
    for entry in entries:
        status = "ok" if bool(getattr(entry, "ok", False)) else "failed"
        lines.append(
            f"- **{getattr(entry, 'name', '')}** — {status}; {getattr(entry, 'message', '')}"
        )
    return "\n".join(lines)


def render_providers_markdown(providers: list[Any]) -> str:
    if not providers:
        return "No providers available."
    lines = ["### Providers"]
    for provider in providers:
        lines.append(
            f"- **{getattr(provider, 'id', '')}** ({getattr(provider, 'name', '')}) — "
            f"{getattr(provider, 'status', '')}; {getattr(provider, 'hint', '')}"
        )
    return "\n".join(lines)


def render_models_markdown(provider_models: list[Any]) -> str:
    if not provider_models:
        return "No connected provider models available."
    lines = ["### Models"]
    for provider in provider_models:
        lines.append(f"- **{getattr(provider, 'name', '')}** ({getattr(provider, 'id', '')})")
        for model in getattr(provider, "models", []):
            context_window = getattr(model, "context_window", 0)
            ctx_label = f"{context_window // 1000}k ctx" if context_window else "? ctx"
            model_label = getattr(model, "full_id", "") or (
                f"{getattr(provider, 'id', '')}/{getattr(model, 'id', '')}"
            )
            lines.append(f"  - `{model_label}` ({getattr(model, 'name', '')}, {ctx_label})")
    return "\n".join(lines)


def render_server_info_markdown(info: Any) -> str:
    lines = [
        "### Server info",
        f"- version: `{getattr(info, 'version', '')}`",
        f"- runtime mode: `{getattr(info, 'runtime_mode', '')}`",
        f"- project dir: `{getattr(info, 'project_dir', '')}`",
        f"- sessions db: `{getattr(info, 'sessions_db', '')}`",
        f"- default model: `{getattr(info, 'default_model', '')}`",
        f"- auth enabled: `{getattr(info, 'auth_enabled', False)}`",
        f"- max sessions: `{getattr(info, 'max_sessions', 0)}`",
        f"- loaded extensions: `{getattr(info, 'loaded_extensions', 0)}`",
    ]
    overlay_path = getattr(info, "provider_overlay_path", "")
    if overlay_path:
        lines.append(f"- provider overlay: `{overlay_path}`")
    return "\n".join(lines)


def render_config_paths_markdown(paths: Any) -> str:
    lines = [
        "### Config paths",
        f"- config dir: `{getattr(paths, 'config_dir', '')}`",
        f"- global config: `{getattr(paths, 'global_config', '')}`",
        f"- project config: `{getattr(paths, 'project_config', '')}`",
        f"- sessions db: `{getattr(paths, 'sessions_db', '')}`",
        f"- provider overlay: `{getattr(paths, 'provider_overlay', '')}`",
    ]
    return "\n".join(lines)


def render_effective_config_markdown(config: dict[str, Any]) -> str:
    if not config:
        return "No effective config available."
    return "\n".join(
        [
            "### Effective config",
            "",
            "```json",
            json.dumps(config, indent=2, sort_keys=True),
            "```",
        ]
    )


def render_server_diagnostics_markdown(diagnostics: Any) -> str:
    lines = [
        "### Server diagnostics",
        f"- active sessions: `{getattr(diagnostics, 'active_sessions', 0)}`",
        f"- loaded extensions: `{getattr(diagnostics, 'loaded_extensions', 0)}`",
        f"- pending oauth: `{getattr(diagnostics, 'pending_oauth', 0)}`",
        f"- permission requests: `{getattr(diagnostics, 'permission_requests', 0)}`",
        f"- auto approve sessions: `{getattr(diagnostics, 'auto_approve_sessions', 0)}`",
        f"- project dir exists: `{getattr(diagnostics, 'project_dir_exists', False)}`",
        f"- global config exists: `{getattr(diagnostics, 'global_config_exists', False)}`",
        f"- project config exists: `{getattr(diagnostics, 'project_config_exists', False)}`",
        f"- provider overlay exists: `{getattr(diagnostics, 'provider_overlay_exists', False)}`",
        f"- sessions db exists: `{getattr(diagnostics, 'sessions_db_exists', False)}`",
    ]
    return "\n".join(lines)


def render_raw_config_markdown(raw_config: Any) -> str:
    return "\n".join(
        [
            f"### Raw config ({getattr(raw_config, 'scope', '') or 'unknown'})",
            f"- path: `{getattr(raw_config, 'path', '')}`",
            f"- exists: `{getattr(raw_config, 'exists', False)}`",
            "",
            "```toml",
            getattr(raw_config, "content", "") or "",
            "```",
        ]
    )


__all__ = [
    "collect_follow_update_messages",
    "collect_follow_file_paths",
    "has_follow_workspace_context",
    "message_role_badge",
    "render_extension_batch_update_markdown",
    "render_extension_commands_markdown",
    "render_follow_diff_markdown",
    "render_follow_file_markdown",
    "render_follow_task_markdown",
    "render_follow_terminal_markdown",
    "render_follow_tool_activity_markdown",
    "render_follow_update_markdown",
    "render_follow_updates_note",
    "render_installed_extensions_markdown",
    "render_config_paths_markdown",
    "render_effective_config_markdown",
    "render_message_markdown",
    "render_models_markdown",
    "render_prompts_markdown",
    "render_providers_markdown",
    "render_raw_config_markdown",
    "render_server_diagnostics_markdown",
    "render_server_info_markdown",
    "render_skills_markdown",
    "render_tool_activity_markdown",
    "render_tree_markdown",
]
