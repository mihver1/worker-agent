"""In-process delegation models for single-window Artel subagents."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

DelegatedRunStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


def now_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def truncate_text(value: str, limit: int = 200) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


@dataclass(slots=True)
class DelegatedRun:
    id: str
    parent_session_id: str
    task: str
    context: str
    model: str
    project_dir: str
    mode: str
    status: DelegatedRunStatus = "queued"
    created_at: str = field(default_factory=now_timestamp)
    started_at: str = ""
    finished_at: str = ""
    result: str = ""
    error: str = ""
    latest_update: str = ""
    events: list[str] = field(default_factory=list)
    done_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    task_handle: asyncio.Task[Any] | None = field(default=None, repr=False)

    def to_payload(
        self,
        *,
        include_result: bool = False,
        include_events: bool = False,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "parent_session_id": self.parent_session_id,
            "task": self.task,
            "model": self.model,
            "project_dir": self.project_dir,
            "mode": self.mode,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "latest_update": self.latest_update,
            "event_count": len(self.events),
            "result_preview": truncate_text(self.result),
        }
        if include_result:
            payload["result"] = self.result
        if include_events:
            payload["events"] = list(self.events)
        return payload


__all__ = ["DelegatedRun", "DelegatedRunStatus", "now_timestamp", "truncate_text"]
