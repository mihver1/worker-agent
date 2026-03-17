"""Formatting helpers for delegated Artel runs."""

from __future__ import annotations

from collections import Counter

from artel_core.delegation.models import DelegatedRun, truncate_text


def format_run_summary(run: DelegatedRun) -> str:
    latest = run.latest_update.strip()
    suffix = f" — {truncate_text(latest, 24)}" if latest else ""
    return f"- {run.id} [{run.status}] ({run.mode}) {run.task}{suffix}"


def format_run_list(runs: list[DelegatedRun]) -> str:
    if not runs:
        return "No delegates found."
    counts = Counter(run.status for run in runs)
    summary = ", ".join(f"{status}={counts[status]}" for status in sorted(counts))
    lines = [f"Delegates: {len(runs)} total ({summary})"]
    lines.extend(format_run_summary(run) for run in runs)
    return "\n".join(lines)


def format_run_detail(run: DelegatedRun) -> str:
    lines = [
        "Delegate:",
        f"- id: {run.id}",
        f"- parent_session_id: {run.parent_session_id}",
        f"- status: {run.status}",
        f"- model: {run.model}",
        f"- mode: {run.mode}",
        f"- project_dir: {run.project_dir}",
        f"- created_at: {run.created_at}",
    ]
    if run.started_at:
        lines.append(f"- started_at: {run.started_at}")
    if run.finished_at:
        lines.append(f"- finished_at: {run.finished_at}")
    lines.append(f"- task: {run.task}")
    if run.error:
        lines.extend(["", "Error:", run.error])
    if run.result:
        lines.extend(["", "Result:", run.result])
    return "\n".join(lines)


__all__ = ["format_run_detail", "format_run_list", "format_run_summary"]
