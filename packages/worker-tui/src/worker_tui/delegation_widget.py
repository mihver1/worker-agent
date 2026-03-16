"""TUI widget for displaying single-window delegation status."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import Static
from worker_core.delegation.registry import get_registry


def _status_icon(status: str) -> str:
    return {
        "queued": "…",
        "running": "▶",
        "completed": "✓",
        "failed": "✗",
        "cancelled": "■",
    }.get(status, "?")


def _truncate(value: str, limit: int = 48) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


class DelegationStatusWidget(Static):
    """Compact status panel for current-session delegated runs."""

    DEFAULT_CSS = """
    DelegationStatusWidget {
        height: auto;
        max-height: 6;
        margin: 0 1;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }
    """

    def __init__(self, app: Any, **kwargs: Any) -> None:
        super().__init__(id="delegation-status-widget", **kwargs)
        self._worker_app = app

    async def on_mount(self) -> None:
        self.update(Text("Orchestration: idle"))
        self.set_interval(1.0, self._poll)

    async def _poll(self) -> None:
        runs, error = await self._load_runs()
        self.update(Text(self._render_text(runs, error=error)))

    async def _load_runs(self) -> tuple[list[dict[str, Any]], str]:
        session_id = self._current_session_id()
        if not session_id:
            return [], ""
        if getattr(self._worker_app, "remote_url", ""):
            try:
                payload = await self._worker_app._remote_control().request(
                    "GET",
                    f"/api/sessions/{session_id}/delegates",
                )
            except Exception as exc:
                return [], f"remote error: {exc}"
            return list(payload.get("delegates", [])), ""
        runs = [run.to_payload() for run in get_registry().list_runs(session_id)]
        return runs, ""

    def _current_session_id(self) -> str:
        if getattr(self._worker_app, "remote_url", ""):
            return str(getattr(self._worker_app, "_remote_session_id", "")).strip()
        session = getattr(self._worker_app, "_session", None)
        return str(getattr(session, "session_id", "")).strip()

    def _render_text(self, runs: list[dict[str, Any]], *, error: str = "") -> str:
        if error:
            return f"Orchestration: {error}"
        if not runs:
            return "Orchestration: idle"
        lines = ["Orchestration:"]
        for run in runs[-5:]:
            latest = str(run.get("latest_update", "")).strip()
            suffix = f" — {_truncate(latest, 24)}" if latest else ""
            lines.append(
                f"  {_status_icon(str(run.get('status', '')))} "
                f"{str(run.get('id', ''))[:8]} "
                f"{_truncate(str(run.get('task', '')))}{suffix}"
            )
        return "\n".join(lines)


__all__ = ["DelegationStatusWidget"]
