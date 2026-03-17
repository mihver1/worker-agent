"""Skills system — on-demand knowledge packs (Claude Code style).

Skills are loaded from:
  1. ~/.config/artel/skills/   (global)
  2. .artel/skills/            (project — overrides global)
  3. Legacy Artel paths are still read as fallback during migration

Each ``.md`` file starts with an optional YAML-like frontmatter::

    ---
    name: python-testing
    description: Best practices for Python testing with pytest
    ---

    # Full skill content …

On session start **all** skill headers (name + description) are injected
into the system prompt so the LLM knows what's available.  The full
body is loaded on demand via ``/skill:name`` or the agent's own request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from artel_core.config import skill_dirs

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)---[ \t]*\n", re.DOTALL)


@dataclass
class Skill:
    """A single skill loaded from a ``.md`` file."""

    name: str
    description: str
    content: str  # full body (everything after frontmatter)
    source: Path = field(default_factory=lambda: Path())


# ── Loading ───────────────────────────────────────────────────────


def _skills_dirs(project_dir: str = "") -> list[Path]:
    """Return skills directories in priority order."""
    return skill_dirs(project_dir)


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    """Split a file into frontmatter key-values and the remaining body."""
    m = _FRONTMATTER_RE.match(raw)
    if not m:
        return {}, raw.strip()

    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()

    body = raw[m.end() :].strip()
    return meta, body


def load_skills(project_dir: str = "") -> dict[str, Skill]:
    """Load all ``.md`` skill files.

    Returns dict of ``{name: Skill}``.
    Project skills override global ones with the same name.
    """
    skills: dict[str, Skill] = {}
    for d in _skills_dirs(project_dir):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.md")):
            try:
                raw = f.read_text(encoding="utf-8")
            except OSError:
                continue

            meta, body = _parse_frontmatter(raw)
            name = meta.get("name", f.stem)
            description = meta.get("description", "")

            # If no frontmatter description, use the first non-heading line
            if not description and body:
                for line in body.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        description = stripped[:120]
                        break

            skills[name] = Skill(
                name=name,
                description=description,
                content=body,
                source=f,
            )
    return skills


def list_skills(project_dir: str = "") -> list[str]:
    """Return sorted list of available skill names."""
    return sorted(load_skills(project_dir).keys())


# ── System prompt integration ─────────────────────────────────────


def build_skills_header(skills: dict[str, Skill]) -> str:
    """Build a section listing all skills for injection into the system prompt.

    Returns empty string if no skills are available.
    """
    if not skills:
        return ""

    lines = [
        "## Available Skills",
        "Use /skill:<name> to load the full content of a skill into this session.",
        "",
    ]
    for sk in sorted(skills.values(), key=lambda s: s.name):
        desc = f" — {sk.description}" if sk.description else ""
        lines.append(f"- **{sk.name}**{desc}")

    return "\n".join(lines)


def inject_skill(system_prompt: str, skill: Skill) -> str:
    """Append a skill's full content to the current system prompt."""
    delimiter = f"\n\n---\n[Skill: {skill.name}]\n{skill.content}\n---"
    return system_prompt + delimiter
