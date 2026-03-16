"""In-process registry for delegated single-window Artel runs."""

from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress
from pathlib import Path

from worker_core.delegation.models import DelegatedRun, now_timestamp, truncate_text


def _is_within_project(project_dir: str, candidate: str) -> bool:
    if not project_dir:
        return True
    try:
        return Path(candidate).resolve().is_relative_to(Path(project_dir).resolve())
    except (OSError, ValueError):
        return candidate == project_dir


class DelegationRegistry:
    """Track delegated runs for all parent sessions in the current process."""

    def __init__(self) -> None:
        self._runs: dict[str, DelegatedRun] = {}
        self._runs_by_session: dict[str, list[str]] = {}
        self._subscribers: list[asyncio.Queue[dict[str, object]]] = []

    def create_run(
        self,
        *,
        parent_session_id: str,
        task: str,
        context: str,
        model: str,
        project_dir: str,
        mode: str,
    ) -> DelegatedRun:
        run = DelegatedRun(
            id=uuid.uuid4().hex[:12],
            parent_session_id=parent_session_id,
            task=task,
            context=context,
            model=model,
            project_dir=project_dir,
            mode=mode,
        )
        self._runs[run.id] = run
        self._runs_by_session.setdefault(parent_session_id, []).append(run.id)
        self._publish("created", run)
        return run

    def bind_task(self, run_id: str, task_handle: asyncio.Task[object]) -> None:
        self._runs[run_id].task_handle = task_handle

    def get_run(self, run_id: str) -> DelegatedRun | None:
        return self._runs.get(run_id)

    def get_session_run(self, parent_session_id: str, run_id: str) -> DelegatedRun | None:
        run = self._runs.get(run_id)
        if run is None or run.parent_session_id != parent_session_id:
            return None
        return run

    def list_runs(self, parent_session_id: str) -> list[DelegatedRun]:
        return [
            self._runs[run_id]
            for run_id in self._runs_by_session.get(parent_session_id, [])
            if run_id in self._runs
        ]

    def list_project_runs(self, project_dir: str = "") -> list[DelegatedRun]:
        runs = list(self._runs.values())
        if not project_dir:
            return runs
        return [run for run in runs if _is_within_project(project_dir, run.project_dir)]

    def mark_running(self, run_id: str) -> DelegatedRun:
        run = self._runs[run_id]
        run.status = "running"
        run.started_at = now_timestamp()
        run.latest_update = "started"
        self._publish("updated", run)
        return run

    def append_event(self, run_id: str, message: str) -> None:
        run = self._runs[run_id]
        rendered = truncate_text(message, 240)
        run.events.append(rendered)
        run.latest_update = rendered
        if len(run.events) > 20:
            run.events = run.events[-20:]
        self._publish("updated", run)

    def mark_completed(self, run_id: str, result: str) -> DelegatedRun:
        run = self._runs[run_id]
        run.status = "completed"
        run.result = result
        run.latest_update = truncate_text(result, 240)
        run.finished_at = now_timestamp()
        run.done_event.set()
        self._publish("completed", run)
        return run

    def mark_failed(self, run_id: str, error: str) -> DelegatedRun:
        run = self._runs[run_id]
        run.status = "failed"
        run.error = error
        run.latest_update = truncate_text(error, 240)
        run.finished_at = now_timestamp()
        run.done_event.set()
        self._publish("failed", run)
        return run

    def mark_cancelled(self, run_id: str) -> DelegatedRun:
        run = self._runs[run_id]
        run.status = "cancelled"
        run.latest_update = "cancelled"
        run.finished_at = now_timestamp()
        run.done_event.set()
        self._publish("cancelled", run)
        return run

    async def wait(self, run_id: str) -> DelegatedRun:
        run = self._runs[run_id]
        await run.done_event.wait()
        return run

    def cancel(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.task_handle is None or run.task_handle.done():
            return False
        run.task_handle.cancel()
        if run.status in {"queued", "running"}:
            run.status = "cancelled"
            run.latest_update = "cancelled"
            run.finished_at = now_timestamp()
            run.done_event.set()
            self._publish("cancelled", run)
        return True

    def subscribe(self) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        if queue in self._subscribers:
            self._subscribers.remove(queue)

    def _publish(self, event_type: str, run: DelegatedRun) -> None:
        payload = {
            "type": event_type,
            "run": run.to_payload(include_result=True, include_events=True),
        }
        for queue in list(self._subscribers):
            with suppress(Exception):
                queue.put_nowait(payload)


_REGISTRY: DelegationRegistry | None = None


def get_registry() -> DelegationRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = DelegationRegistry()
    return _REGISTRY


def reset_registry() -> None:
    global _REGISTRY
    _REGISTRY = DelegationRegistry()


__all__ = ["DelegationRegistry", "get_registry", "reset_registry"]
