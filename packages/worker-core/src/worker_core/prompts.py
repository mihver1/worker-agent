"""Prompt template system — load .md files, {{variable}} substitution.

Templates are loaded from:
  1. ~/.config/worker/prompts/  (global)
  2. .worker/prompts/           (project — overrides global)

Each `.md` file becomes a `/filename` command in the TUI.
Use `{{variable}}` placeholders for dynamic substitution.
"""

from __future__ import annotations

import re
from pathlib import Path

from worker_core.config import CONFIG_DIR


def _prompts_dirs(project_dir: str = "") -> list[Path]:
    """Return prompt directories in priority order (global, then project)."""
    dirs = [CONFIG_DIR / "prompts"]
    if project_dir:
        dirs.append(Path(project_dir) / ".worker" / "prompts")
    return dirs


def load_prompts(project_dir: str = "") -> dict[str, str]:
    """Load all .md prompt templates.

    Returns dict of {name: content} where name is the stem without extension.
    Project prompts override global ones with the same name.
    """
    prompts: dict[str, str] = {}
    for d in _prompts_dirs(project_dir):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            name = f.stem
            try:
                prompts[name] = f.read_text(encoding="utf-8").strip()
            except OSError:
                continue
    return prompts


def list_prompts(project_dir: str = "") -> list[str]:
    """Return sorted list of available prompt template names."""
    return sorted(load_prompts(project_dir).keys())


def render_prompt(template: str, variables: dict[str, str] | None = None) -> str:
    """Substitute ``{{variable}}`` placeholders in a template string.

    Unknown variables are left as-is.
    """
    if not variables:
        return template

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return variables.get(key, match.group(0))

    return re.sub(r"\{\{(\w+)\}\}", _replace, template)
