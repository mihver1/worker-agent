"""First-class git status/diff/rollback helpers for TUI and other surfaces."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def _run_git(args: list[str], *, cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def render_git_status(*, cwd: str) -> str:
    proc = _run_git(["status", "--short", "--branch"], cwd=cwd)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return f"git status failed: {detail or f'exit code {proc.returncode}'}"
    output = proc.stdout.strip()
    if not output:
        return "Git status: clean working tree."

    lines = output.splitlines()
    header = lines[0] if lines else ""
    entries = lines[1:] if len(lines) > 1 else []
    if not entries:
        return f"Git status\n{header}\n\nWorking tree clean."

    grouped: dict[str, list[str]] = {"modified": [], "added": [], "deleted": [], "renamed": [], "untracked": [], "other": []}
    for line in entries:
        code = line[:2]
        path = line[3:] if len(line) > 3 else line
        if code == "??":
            grouped["untracked"].append(path)
        elif "R" in code:
            grouped["renamed"].append(path)
        elif "A" in code:
            grouped["added"].append(path)
        elif "D" in code:
            grouped["deleted"].append(path)
        elif "M" in code:
            grouped["modified"].append(path)
        else:
            grouped["other"].append(line)

    rendered = ["Git status", header, ""]
    order = ["modified", "added", "deleted", "renamed", "untracked", "other"]
    for key in order:
        items = grouped[key]
        if not items:
            continue
        rendered.append(f"{key.capitalize()} ({len(items)}):")
        rendered.extend(f"  - {item}" for item in items[:20])
        if len(items) > 20:
            rendered.append(f"  - … and {len(items) - 20} more")
        rendered.append("")
    return "\n".join(line for line in rendered if line is not None).strip()


def render_git_diff(*, cwd: str, pathspec: str = "") -> str:
    args = ["diff", "--", pathspec] if pathspec else ["diff"]
    proc = _run_git(args, cwd=cwd)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return f"git diff failed: {detail or f'exit code {proc.returncode}'}"
    output = proc.stdout.strip()
    if not output:
        target = pathspec or "working tree"
        return f"No unstaged diff for {target}."
    lines = output.splitlines()
    if len(lines) > 300:
        output = "\n".join(lines[:300]) + "\n…"
    target = pathspec or "working tree"
    return f"Git diff: {target}\n\n```diff\n{output}\n```"


def restore_path(*, cwd: str, pathspec: str) -> str:
    pathspec = str(pathspec or "").strip()
    if not pathspec:
        return "Usage: /rollback <path>"
    proc = _run_git(["restore", "--", pathspec], cwd=cwd)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return f"git restore failed: {detail or f'exit code {proc.returncode}'}"
    return f"Restored: {pathspec}"


def restore_all(*, cwd: str) -> str:
    proc = _run_git(["restore", "."], cwd=cwd)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return f"git restore failed: {detail or f'exit code {proc.returncode}'}"
    return "Restored all unstaged changes."


def render_git_help() -> str:
    return (
        "Git commands:\n"
        "  /git status            — summarize working tree changes\n"
        "  /git diff [path]       — show unstaged diff (optionally for one path)\n"
        "  /status                — alias for /git status\n"
        "  /diff [path]           — alias for /git diff [path]\n"
        "  /rollback <path>       — git restore one path\n"
        "  /rollback --all        — git restore .\n"
    )


__all__ = [
    "render_git_status",
    "render_git_diff",
    "restore_all",
    "restore_path",
    "render_git_help",
]
