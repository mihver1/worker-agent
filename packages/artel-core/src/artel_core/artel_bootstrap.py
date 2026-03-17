"""Early application bootstrap helpers for Artel startup."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from artel_core.cmux import CmuxPreflightResult
from artel_core.migrations import check_and_migrate


@dataclass(slots=True)
class ArtelBootstrapResult:
    project_dir: str
    cmux_required: bool
    cmux_preflight: CmuxPreflightResult | None = None


def resolve_project_dir(project_dir: str | None = None) -> str:
    """Resolve the project directory used for Artel startup and migrations."""
    return str(Path(project_dir or os.getcwd()).resolve(strict=False))


def command_requires_cmux(
    command_name: str | None,
    *,
    prompt: str | None = None,
) -> bool:
    """cmux is no longer required for Artel startup."""
    del command_name, prompt
    return False


def bootstrap_artel(
    project_dir: str | None = None,
    *,
    command_name: str | None = None,
    prompt: str | None = None,
) -> ArtelBootstrapResult:
    """Run first-run Artel bootstrap steps and return resolved startup metadata."""
    resolved_project_dir = resolve_project_dir(project_dir)
    check_and_migrate(project_dir=resolved_project_dir)

    cmux_required = command_requires_cmux(command_name, prompt=prompt)
    return ArtelBootstrapResult(
        project_dir=resolved_project_dir,
        cmux_required=cmux_required,
        cmux_preflight=None,
    )
