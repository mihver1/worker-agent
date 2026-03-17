"""WebSocket server for remote Artel access."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shlex
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import websockets
from artel_ai.models import ImageAttachment, Message
from artel_ai.oauth import (
    OAuthToken,
    RemoteOAuthChallenge,
    TokenStore,
    complete_remote_oauth_challenge,
    start_remote_oauth_challenge,
)
from artel_core.agent import AgentEventType, AgentSession
from artel_core.board import (
    operator_notes_path,
    read_project_board_file,
    tasks_path,
    write_project_board_file,
)
from artel_core.bootstrap import (
    bootstrap_runtime,
    create_agent_session_from_bootstrap,
    provider_requires_api_key,
)
from artel_core.config import (
    CONFIG_DIR,
    GLOBAL_CONFIG,
    ArtelConfig,
    ProviderConfig,
    effective_global_config_path,
    effective_project_config_path,
    effective_server_provider_overlay_path,
    generate_global_config,
    generate_project_config,
    load_config,
    persist_server_auth_token,
    project_agents_path,
    project_config_path,
    resolve_model,
)
from artel_core.delegation.registry import get_registry as get_delegation_registry
from artel_core.extensions import ExtensionContext, load_server_extensions_async
from artel_core.extensions_admin import (
    add_registry,
    install_extension,
    list_installed_extensions,
    list_registry_entries,
    remove_extension,
    remove_registry,
    search_extensions,
    update_all_extensions,
    update_extension,
)
from artel_core.mcp import MCPConfig, MCPRegistry, MCPServerConfig
from artel_core.mcp_runtime import McpRuntimeManager
from artel_core.prompts import load_prompts, render_prompt
from artel_core.provider_resolver import (
    get_effective_model_info,
    get_effective_provider_catalog,
)
from artel_core.provider_setup import collect_provider_setup_entries
from artel_core.rules import (
    SessionRuleOverrides,
    add_rule,
    clear_session_rule_overrides,
    delete_rule,
    list_rules,
    move_rule,
    reset_rule_for_session,
    serialize_session_rule_overrides,
    set_rule_enabled_for_session,
    update_rule,
)
from artel_core.schedules import (
    ScheduleRecord,
    ScheduleRegistry,
    ScheduleRunRecord,
    ScheduleStateRecord,
    add_schedule,
    delete_schedule,
    load_schedule_states_for_scope,
    next_schedule_time,
    parse_timestamp,
    render_prompt_variables,
    serialize_schedule,
    serialize_schedule_run,
    serialize_schedule_state,
    update_schedule,
    write_schedule_states,
)
from artel_core.sessions import SessionInfo, SessionStore
from artel_core.skills import load_skills
from websockets.asyncio.server import ServerConnection

from artel_server.provider_overlay import (
    is_rejected_placeholder_api_key,
    load_provider_overlay,
    merge_provider_overlay,
    save_provider_overlay,
    upsert_provider_overlay,
)

logger = logging.getLogger("artel.server")


def _schedule_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class ServerState:
    config: ArtelConfig
    sessions: dict[str, AgentSession] = field(default_factory=dict)
    session_provider_models: dict[str, str] = field(default_factory=dict)
    session_projects: dict[str, str] = field(default_factory=dict)
    session_thinking_levels: dict[str, str] = field(default_factory=dict)
    session_rule_overrides: dict[str, SessionRuleOverrides] = field(default_factory=dict)
    provider_overlay: dict[str, ProviderConfig] = field(default_factory=dict)
    pending_oauth: dict[str, RemoteOAuthChallenge] = field(default_factory=dict)
    permission_callbacks: dict[
        str,
        Callable[[str, dict[str, Any]], Awaitable[bool]],
    ] = field(default_factory=dict)
    pending_permissions: dict[
        str,
        tuple[str, asyncio.Future[tuple[bool, bool]]],
    ] = field(default_factory=dict)
    auto_approve_sessions: set[str] = field(default_factory=set)
    session_controllers: dict[str, SessionController] = field(default_factory=dict)
    server_extensions: list[Any] = field(default_factory=list)
    default_project_dir: str = ""
    store: SessionStore | None = None
    mcp_runtime: McpRuntimeManager | None = None
    schedule_registry: ScheduleRegistry = field(default_factory=ScheduleRegistry)
    schedule_service: ScheduleService | None = None


def _tool_kind_for_name(tool_name: str) -> str:
    mapping = {
        "read": "read",
        "write": "edit",
        "edit": "edit",
        "bash": "execute",
        "grep": "search",
        "find": "search",
        "ls": "search",
    }
    return mapping.get(tool_name, "other")


def _positive_line_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = int(stripped)
        except ValueError:
            return None
        return parsed if parsed > 0 else None
    return None


def _resolve_tool_path(path: str, *, cwd: str | None = None) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(cwd or os.getcwd()).expanduser() / candidate
    return candidate.resolve(strict=False)


def _unique_match_line(path: Path, search: str) -> int | None:
    if not search:
        return None
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    first_index = content.find(search)
    if first_index < 0:
        return None
    if content.find(search, first_index + 1) >= 0:
        return None
    return content.count("\n", 0, first_index) + 1


def _tool_location_line(tool_name: str, args: dict[str, Any], *, path: Path) -> int | None:
    if tool_name == "read":
        return _positive_line_number(args.get("start_line")) or 1
    if tool_name == "write":
        return 1
    if tool_name == "edit":
        search = args.get("search")
        if isinstance(search, str):
            return _unique_match_line(path, search)
    return None


def _tool_locations(
    tool_name: str,
    args: dict[str, Any],
    *,
    cwd: str | None = None,
) -> list[dict[str, Any]] | None:
    if tool_name not in {"read", "write", "edit"}:
        return None
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    resolved_path = _resolve_tool_path(path, cwd=cwd)
    location: dict[str, Any] = {"path": str(resolved_path)}
    line = _tool_location_line(tool_name, args, path=resolved_path)
    if line is not None:
        location["line"] = line
    return [location]


def _agent_event_payload(event: Any, *, cwd: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": event.type.value}
    if event.type in {
        AgentEventType.TEXT_DELTA,
        AgentEventType.REASONING_DELTA,
    }:
        payload["content"] = event.content
    elif event.type == AgentEventType.TOOL_CALL:
        payload["tool"] = event.tool_name
        payload["args"] = event.tool_args
        payload["call_id"] = event.tool_call_id
        payload["kind"] = _tool_kind_for_name(event.tool_name)
        locations = _tool_locations(event.tool_name, event.tool_args, cwd=cwd)
        if locations:
            payload["locations"] = locations
    elif event.type == AgentEventType.TOOL_RESULT:
        payload["tool"] = event.tool_name
        payload["call_id"] = event.tool_call_id
        payload["output"] = event.content
        payload["is_error"] = event.is_error
        if event.display is not None:
            payload["display"] = event.display
    elif event.type == AgentEventType.DONE:
        if event.usage:
            payload["usage"] = {
                "input": event.usage.input_tokens,
                "output": event.usage.output_tokens,
            }
    elif event.type == AgentEventType.ERROR:
        payload["error"] = event.error
    return payload


class ScheduleService:
    """In-process scheduler for server-side scheduled prompts/tasks."""

    def __init__(self, state: ServerState) -> None:
        self.state = state
        self._loop_task: asyncio.Task[None] | None = None
        self._reload_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._stop_requested = False
        self._run_history: dict[str, list[ScheduleRunRecord]] = {}
        self._run_tasks: dict[str, asyncio.Task[None]] = {}
        self._schedule_states: dict[str, ScheduleStateRecord] = {}
        self._schedule_tasks: dict[str, set[str]] = {}
        self._schedule_scope: dict[str, str] = {}
        self._loaded_schedules: dict[str, ScheduleRecord] = {}
        self._history_path = (
            Path(self.state.default_project_dir) / ".artel" / "schedules-history.json"
        )

    async def start(self) -> None:
        await self.reload()
        self._stop_requested = False
        self._loop_task = asyncio.create_task(self._run_loop(), name="artel-scheduler")

    async def stop(self) -> None:
        self._stop_requested = True
        self._reload_event.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._loop_task
        for task in list(self._run_tasks.values()):
            task.cancel()
        for task in list(self._run_tasks.values()):
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._run_tasks.clear()

    async def reload(self) -> dict[str, Any]:
        async with self._lock:
            loaded = self.state.schedule_registry.load_merged_config(self.state.default_project_dir)
            self._loaded_schedules = loaded.schedules
            self._schedule_scope = {
                schedule_id: record.scope for schedule_id, record in loaded.schedules.items()
            }
            self._load_state_files()
            self._load_history_file()
            now = datetime.now(UTC)
            for schedule_id, record in loaded.schedules.items():
                state_record = self._schedule_states.setdefault(
                    schedule_id, ScheduleStateRecord(schedule_id=schedule_id)
                )
                state_record.next_run_at = self._compute_next_run(record, state_record, now=now)
                state_record.updated_at = _schedule_now()
            for schedule_id in list(self._schedule_states):
                if schedule_id not in loaded.schedules:
                    self._schedule_states.pop(schedule_id, None)
                    self._schedule_tasks.pop(schedule_id, None)
            self._persist_states()
        self._reload_event.set()
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        schedules_payload: list[dict[str, Any]] = []
        for schedule_id, record in sorted(self._loaded_schedules.items()):
            state_record = self._schedule_states.get(
                schedule_id, ScheduleStateRecord(schedule_id=schedule_id)
            )
            schedules_payload.append(
                {
                    "schedule": serialize_schedule(record),
                    "state": serialize_schedule_state(state_record),
                    "runs": [
                        serialize_schedule_run(run)
                        for run in self._run_history.get(schedule_id, [])
                    ],
                }
            )
        next_run_at = ""
        candidates = [
            state_record.next_run_at
            for schedule_id, state_record in self._schedule_states.items()
            if self._loaded_schedules.get(schedule_id) is not None and state_record.next_run_at
        ]
        if candidates:
            next_run_at = min(candidates)
        return {
            "enabled": bool(self.state.config.server.scheduler_enabled),
            "count": len(self._loaded_schedules),
            "next_run_at": next_run_at,
            "schedules": schedules_payload,
        }

    async def run_now(self, schedule_id: str, *, trigger: str = "manual") -> dict[str, Any]:
        record = self._loaded_schedules.get(schedule_id)
        if record is None:
            raise RuntimeError(f"Schedule '{schedule_id}' not found")
        await self._trigger_schedule(record, trigger=trigger, scheduled_for=datetime.now(UTC))
        return self.snapshot()

    async def _run_loop(self) -> None:
        try:
            while not self._stop_requested:
                record, wait_seconds = self._next_due_schedule()
                if record is None:
                    self._reload_event.clear()
                    await self._reload_event.wait()
                    continue
                if wait_seconds > 0:
                    self._reload_event.clear()
                    try:
                        await asyncio.wait_for(self._reload_event.wait(), timeout=wait_seconds)
                        continue
                    except TimeoutError:
                        pass
                await self._trigger_due_schedule(record)
        except asyncio.CancelledError:
            raise

    async def _trigger_due_schedule(self, record: ScheduleRecord) -> None:
        state_record = self._schedule_states.setdefault(
            record.id, ScheduleStateRecord(schedule_id=record.id)
        )
        now = datetime.now(UTC)
        due_at = parse_timestamp(state_record.next_run_at) or now
        missed: list[datetime] = []
        if due_at <= now:
            if record.run_missed == "all":
                current = due_at
                limit = 25
                while current <= now and limit > 0:
                    missed.append(current)
                    current = next_schedule_time(record, current) or (now + timedelta(days=366))
                    limit -= 1
            elif record.run_missed == "latest":
                latest = due_at
                current = due_at
                limit = 25
                while current <= now and limit > 0:
                    latest = current
                    current = next_schedule_time(record, current) or (now + timedelta(days=366))
                    limit -= 1
                missed.append(latest)
            else:
                missed.append(now)
        if not missed:
            missed.append(now)
        for scheduled_for in missed:
            await self._trigger_schedule(record, trigger="schedule", scheduled_for=scheduled_for)

    def _next_due_schedule(self) -> tuple[ScheduleRecord | None, float]:
        now = datetime.now(UTC)
        best_record: ScheduleRecord | None = None
        best_time: datetime | None = None
        for schedule_id, record in self._loaded_schedules.items():
            if not record.enabled:
                continue
            state_record = self._schedule_states.setdefault(
                schedule_id, ScheduleStateRecord(schedule_id=schedule_id)
            )
            next_run = (
                parse_timestamp(state_record.next_run_at) if state_record.next_run_at else None
            )
            if next_run is None:
                next_text = self._compute_next_run(record, state_record, now=now)
                state_record.next_run_at = next_text
                next_run = parse_timestamp(next_text) if next_text else None
            if next_run is None:
                continue
            if best_time is None or next_run < best_time:
                best_time = next_run
                best_record = record
        if best_record is None or best_time is None:
            return None, 0.0
        return best_record, max(0.0, (best_time - now).total_seconds())

    async def _trigger_schedule(
        self, record: ScheduleRecord, *, trigger: str, scheduled_for: datetime
    ) -> None:
        schedule_id = record.id
        state_record = self._schedule_states.setdefault(
            schedule_id, ScheduleStateRecord(schedule_id=schedule_id)
        )
        state_record.next_run_at = ""
        running_ids = [
            run_id
            for run_id in state_record.running_run_ids
            if run_id in self._run_tasks and not self._run_tasks[run_id].done()
        ]
        state_record.running_run_ids = running_ids
        if running_ids:
            if record.overlap_policy == "skip":
                self._append_run(
                    ScheduleRunRecord(
                        id=f"schedule-run-{uuid.uuid4().hex[:8]}",
                        schedule_id=schedule_id,
                        trigger=trigger,
                        status="skipped",
                        started_at=_schedule_now(),
                        finished_at=_schedule_now(),
                        scheduled_for=_format_timestamp(scheduled_for),
                        error="Skipped due to overlap policy.",
                    )
                )
                state_record.last_status = "skipped"
                state_record.last_error = "Skipped due to overlap policy."
                state_record.total_skips += 1
                state_record.total_runs += 1
                state_record.last_run_at = _schedule_now()
                state_record.next_run_at = self._compute_next_run(
                    record, state_record, now=scheduled_for
                )
                state_record.updated_at = _schedule_now()
                self._persist_states(record.scope)
                return
            if record.overlap_policy == "cancel_previous":
                for run_id in running_ids:
                    task = self._run_tasks.get(run_id)
                    if task is not None:
                        task.cancel()
        run_id = f"schedule-run-{uuid.uuid4().hex[:8]}"
        run_record = ScheduleRunRecord(
            id=run_id,
            schedule_id=schedule_id,
            trigger=trigger,
            status="running",
            started_at=_schedule_now(),
            scheduled_for=_format_timestamp(scheduled_for),
        )
        state_record.running_run_ids.append(run_id)
        state_record.last_status = "running"
        state_record.last_run_id = run_id
        state_record.total_runs += 1
        state_record.updated_at = _schedule_now()
        self._append_run(run_record)
        task = asyncio.create_task(
            self._execute_schedule(record, run_record), name=f"artel-schedule-{schedule_id}"
        )
        self._run_tasks[run_id] = task
        self._schedule_tasks.setdefault(schedule_id, set()).add(run_id)
        task.add_done_callback(lambda done, _run_id=run_id: self._run_tasks.pop(_run_id, None))
        self._persist_states(record.scope)

    async def _execute_schedule(
        self, record: ScheduleRecord, run_record: ScheduleRunRecord
    ) -> None:
        schedule_id = record.id
        state_record = self._schedule_states.setdefault(
            schedule_id, ScheduleStateRecord(schedule_id=schedule_id)
        )
        project_dir = _request_project_dir(self.state, record.project_dir)
        model = record.model.strip()
        session_id = self._scheduled_session_id(record, run_record)
        content = self._render_content(record, project_dir)
        try:
            session = await self._prepare_scheduled_session(record, session_id, project_dir, model)
            controller = _get_session_controller(self.state, session_id)
            await controller.start(content)
            task = controller._run_task
            assert task is not None
            if record.max_runtime_seconds > 0:
                await asyncio.wait_for(asyncio.shield(task), timeout=record.max_runtime_seconds)
            else:
                await asyncio.shield(task)
            run_record.status = "succeeded"
            run_record.result_preview = self._result_preview(session)
            run_record.session_id = session_id
            state_record.total_successes += 1
            state_record.last_result_preview = run_record.result_preview
            state_record.last_error = ""
            state_record.last_session_id = session_id
        except TimeoutError:
            controller = self.state.session_controllers.get(session_id)
            if controller is not None:
                with suppress(Exception):
                    await controller.abort()
            run_record.status = "failed"
            run_record.error = f"Timed out after {record.max_runtime_seconds} seconds"
            state_record.total_failures += 1
            state_record.last_error = run_record.error
        except asyncio.CancelledError:
            controller = self.state.session_controllers.get(session_id)
            if controller is not None:
                with suppress(Exception):
                    await controller.abort()
            run_record.status = "cancelled"
            run_record.error = "Cancelled"
            state_record.last_error = run_record.error
            raise
        except Exception as exc:
            run_record.status = "failed"
            run_record.error = str(exc)
            state_record.total_failures += 1
            state_record.last_error = str(exc)
        finally:
            run_record.finished_at = _schedule_now()
            state_record.last_status = run_record.status
            state_record.last_run_at = run_record.started_at
            state_record.last_run_id = run_record.id
            if run_record.session_id:
                state_record.last_session_id = run_record.session_id
            state_record.running_run_ids = [
                item for item in state_record.running_run_ids if item != run_record.id
            ]
            state_record.next_run_at = self._compute_next_run(
                record, state_record, now=datetime.now(UTC)
            )
            state_record.updated_at = _schedule_now()
            self._persist_states(record.scope)

    async def _prepare_scheduled_session(
        self,
        record: ScheduleRecord,
        session_id: str,
        project_dir: str,
        model: str,
    ) -> AgentSession:
        await _ensure_session_idle(self.state, session_id)
        session_config = self.state.config.model_copy(deep=True)
        if record.session_mode == "new":
            previous = self.state.sessions.pop(session_id, None)
            if previous is not None:
                with suppress(Exception):
                    await previous.provider.close()
            self.state.session_provider_models.pop(session_id, None)
            self.state.session_projects.pop(session_id, None)
            self.state.session_thinking_levels.pop(session_id, None)
        if record.execution_mode == "readonly":
            session_config.permissions.edit = "deny"
            session_config.permissions.write = "deny"
            session_config.permissions.bash = "deny"
            session_config.permissions.bash_commands = {}
        if model:
            if "/" not in model:
                raise RuntimeError("Schedule model must be in provider/model-id format")
            provider_name, model_id = model.split("/", 1)
            return await _create_server_session(
                self.state,
                session_id,
                provider_name=provider_name,
                model_id=model_id,
                project_dir=project_dir,
                session_config=session_config,
            )
        return await _create_server_session(
            self.state,
            session_id,
            project_dir=project_dir,
            session_config=session_config,
        )

    def _render_content(self, record: ScheduleRecord, project_dir: str) -> str:
        if record.prompt.strip():
            return record.prompt
        prompts = load_prompts(project_dir)
        template = prompts.get(record.prompt_name)
        if not template:
            raise RuntimeError(f"Prompt '{record.prompt_name}' not found")
        return render_prompt(template, render_prompt_variables(record.arg))

    def _result_preview(self, session: AgentSession) -> str:
        for message in reversed(session.messages[1:]):
            if getattr(message.role, "value", "") == "assistant" and message.content.strip():
                return message.content.strip()[:400]
        return ""

    def _compute_next_run(
        self, record: ScheduleRecord, state_record: ScheduleStateRecord, *, now: datetime
    ) -> str:
        anchor = now
        last_run = parse_timestamp(state_record.last_run_at)
        if last_run is not None and last_run > anchor:
            anchor = last_run
        if state_record.next_run_at:
            existing = parse_timestamp(state_record.next_run_at)
            if existing is not None and existing > now and record.kind == "interval":
                anchor = max(now, existing - timedelta(seconds=record.every_seconds))
        if record.run_missed != "none" and last_run is not None:
            candidate = next_schedule_time(record, last_run)
            if candidate is not None and candidate <= now:
                return _format_timestamp(now)
        candidate = next_schedule_time(record, anchor)
        return _format_timestamp(candidate) if candidate is not None else ""

    def _append_run(self, run_record: ScheduleRunRecord) -> None:
        runs = self._run_history.setdefault(run_record.schedule_id, [])
        runs.append(run_record)
        if len(runs) > 100:
            del runs[:-100]
        self._persist_history_file()

    def _load_state_files(self) -> None:
        self._schedule_states = {}
        for scope in ("global", "project"):
            states = load_schedule_states_for_scope(self.state.default_project_dir, scope=scope)  # type: ignore[arg-type]
            for schedule_id, record in states.items():
                self._schedule_states[schedule_id] = record

    def _load_history_file(self) -> None:
        self._run_history = {}
        if not self._history_path.exists():
            return
        try:
            payload = json.loads(self._history_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        raw_items = payload.get("schedules", {}) if isinstance(payload, dict) else {}
        if not isinstance(raw_items, dict):
            return
        for schedule_id, values in raw_items.items():
            if not isinstance(values, list):
                continue
            records: list[ScheduleRunRecord] = []
            for item in values:
                if not isinstance(item, dict):
                    continue
                records.append(
                    ScheduleRunRecord(
                        id=str(item.get("id", "") or ""),
                        schedule_id=str(item.get("schedule_id", schedule_id) or schedule_id),
                        trigger=str(item.get("trigger", "") or ""),
                        status=str(item.get("status", "idle") or "idle"),
                        started_at=str(item.get("started_at", "") or ""),
                        scheduled_for=str(item.get("scheduled_for", "") or ""),
                        finished_at=str(item.get("finished_at", "") or ""),
                        session_id=str(item.get("session_id", "") or ""),
                        error=str(item.get("error", "") or ""),
                        result_preview=str(item.get("result_preview", "") or ""),
                    )
                )
            if records:
                self._run_history[str(schedule_id)] = records[-100:]

    def _persist_history_file(self) -> None:
        self._history_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schedules": {
                schedule_id: [serialize_schedule_run(run) for run in runs[-100:]]
                for schedule_id, runs in sorted(self._run_history.items())
            }
        }
        self._history_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def _persist_states(self, scope: str | None = None) -> None:
        scopes = [scope] if scope in {"global", "project"} else ["global", "project"]
        for current_scope in scopes:
            scoped = {
                schedule_id: state_record
                for schedule_id, state_record in self._schedule_states.items()
                if self._schedule_scope.get(schedule_id, "project") == current_scope
            }
            write_schedule_states(self.state.default_project_dir, scoped, scope=current_scope)  # type: ignore[arg-type]

    def _scheduled_session_id(self, record: ScheduleRecord, run_record: ScheduleRunRecord) -> str:
        if record.session_mode == "reuse":
            return f"schedule:{record.id}"
        return f"schedule:{record.id}:{run_record.id}"


class SessionController:
    """Own an agent run in the background and fan events out to subscribers."""

    def __init__(self, state: ServerState, session_id: str) -> None:
        self.state = state
        self.session_id = session_id
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._run_task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._run_task is not None and not self._run_task.done()

    @property
    def has_subscribers(self) -> bool:
        return bool(self._subscribers)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    async def publish(self, payload: dict[str, Any]) -> None:
        for queue in tuple(self._subscribers):
            queue.put_nowait(payload)

    async def ensure_idle(self) -> None:
        task = self._run_task
        if task is None:
            return
        if not task.done():
            raise RuntimeError("Session is busy")
        with suppress(Exception):
            task.result()

    async def start(
        self,
        content: str,
        attachments: list[ImageAttachment] | None = None,
    ) -> None:
        async with self._start_lock:
            if self.running:
                raise RuntimeError("Session is busy")
            self._run_task = asyncio.create_task(
                self._run(content, attachments=attachments),
                name=f"artel-session-{self.session_id}",
            )

    async def abort(self) -> None:
        session = self.state.sessions.get(self.session_id)
        if session is not None:
            session.abort()
        task = self._run_task
        if task is not None:
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(task)

    async def _run(
        self,
        content: str,
        attachments: list[ImageAttachment] | None = None,
    ) -> None:
        try:
            session = self.state.sessions.get(self.session_id)
            if session is None:
                session = await _create_server_session(self.state, self.session_id)
            await self.publish({"type": "status", "state": "thinking", "busy": True})
            asyncio.create_task(self._maybe_generate_title(session, content))
            run_iter = (
                session.run(content, attachments=attachments)
                if attachments
                else session.run(content)
            )
            async for event in run_iter:
                await self.publish(_agent_event_payload(event, cwd=session.project_dir))
        except Exception as exc:
            logger.exception("Background session run failed for %s", self.session_id)
            await self.publish({"type": "error", "error": str(exc)})
        finally:
            await self.publish({"type": "status", "state": "idle", "busy": False})

    async def _maybe_generate_title(self, session: AgentSession, content: str) -> None:
        if self.state.store is None:
            return
        session_info = await self.state.store.get_session(self.session_id)
        if session_info is None or session_info.title:
            return
        try:
            title = await session.generate_title(content)
        except Exception:
            return
        title = title.strip()
        if not title:
            return
        await self.state.store.rename_session(self.session_id, title)
        await self.publish(
            {
                "type": "session_updated",
                "session": await _serialize_session(self.state, self.session_id),
            }
        )


def _get_session_controller(state: ServerState, session_id: str) -> SessionController:
    controller = state.session_controllers.get(session_id)
    if controller is None:
        controller = SessionController(state, session_id)
        state.session_controllers[session_id] = controller
    return controller


async def _ensure_session_idle(state: ServerState, session_id: str) -> None:
    await _get_session_controller(state, session_id).ensure_idle()


def _session_model_label(
    state: ServerState,
    session_id: str,
    session: AgentSession | None,
    session_info: SessionInfo | None = None,
) -> str:
    if session_id in state.session_provider_models:
        return state.session_provider_models[session_id]
    if session_info is not None and session_info.model:
        return session_info.model
    if session is not None and session.model:
        return session.model
    return state.config.agent.model


def _session_project_dir(
    state: ServerState,
    session_id: str,
    session: AgentSession | None,
    session_info: SessionInfo | None = None,
) -> str:
    if session_id in state.session_projects:
        return state.session_projects[session_id]
    if session_info is not None and session_info.project_dir:
        return session_info.project_dir
    if session is not None and session.project_dir:
        return session.project_dir
    if state.default_project_dir:
        return state.default_project_dir
    return os.getcwd()


def _session_thinking_level(
    state: ServerState,
    session_id: str,
    session: AgentSession | None,
    session_info: SessionInfo | None = None,
) -> str:
    if session_id in state.session_thinking_levels:
        return state.session_thinking_levels[session_id]
    if session is not None:
        return session.thinking_level
    if session_info is not None and session_info.thinking_level:
        return session_info.thinking_level
    return state.config.agent.thinking


def _request_project_dir(state: ServerState, requested: str = "") -> str:
    candidate = requested.strip()
    if candidate:
        return str(Path(candidate).expanduser().resolve(strict=False))
    if state.default_project_dir:
        return str(Path(state.default_project_dir).expanduser().resolve(strict=False))
    return os.getcwd()


def _serialize_rule(rule: Any) -> dict[str, Any]:
    return {
        "id": str(rule.id),
        "scope": str(rule.scope),
        "text": str(rule.text),
        "enabled": bool(rule.enabled),
        "order": int(getattr(rule, "order", 0) or 0),
        "created_at": str(getattr(rule, "created_at", "")),
        "updated_at": str(getattr(rule, "updated_at", "")),
    }


def _session_rule_overrides(state: ServerState, session_id: str) -> SessionRuleOverrides:
    return state.session_rule_overrides.setdefault(session_id, SessionRuleOverrides.empty())


async def _stored_session_context(
    state: ServerState,
    session_id: str,
) -> tuple[SessionInfo | None, list[Message]]:
    if state.store is None:
        return None, []
    session_info = await state.store.get_session(session_id)
    if session_info is None:
        return None, []
    return session_info, await state.store.get_messages(session_id)


async def _persist_session_record(
    state: ServerState,
    session_id: str,
    *,
    model: str,
    project_dir: str,
    thinking_level: str,
) -> None:
    if state.store is None:
        return
    session_info = await state.store.get_session(session_id)
    if session_info is None:
        await state.store.create_session(
            session_id,
            model,
            project_dir=project_dir,
            thinking_level=thinking_level,
        )
        return
    if session_info.model != model:
        await state.store.update_session_model(session_id, model)
    if session_info.project_dir != project_dir:
        await state.store.update_session_project(session_id, project_dir)
    if session_info.thinking_level != thinking_level:
        await state.store.update_session_thinking(session_id, thinking_level)


def _refresh_provider_config_from_sources(state: ServerState, provider_id: str) -> None:
    refreshed_config = load_config(state.default_project_dir or os.getcwd())
    merge_provider_overlay(refreshed_config, state.provider_overlay)
    refreshed_provider = refreshed_config.providers.get(provider_id)
    if refreshed_provider is None:
        state.config.providers.pop(provider_id, None)
        return
    state.config.providers[provider_id] = refreshed_provider


async def _initialize_session_state(
    state: ServerState,
    session_id: str,
    *,
    model: str,
    project_dir: str,
    thinking_level: str | None = None,
) -> None:
    state.session_provider_models[session_id] = model
    state.session_projects[session_id] = project_dir
    if thinking_level is not None:
        state.session_thinking_levels[session_id] = thinking_level
    session_info = await state.store.get_session(session_id) if state.store is not None else None
    effective_thinking = thinking_level or _session_thinking_level(
        state,
        session_id,
        state.sessions.get(session_id),
        session_info,
    )
    await _persist_session_record(
        state,
        session_id,
        model=model,
        project_dir=project_dir,
        thinking_level=effective_thinking,
    )


async def _serialize_session(
    state: ServerState,
    session_id: str,
    session_info: SessionInfo | None = None,
) -> dict[str, Any]:
    if session_info is None and state.store is not None:
        session_info = await state.store.get_session(session_id)
    session = state.sessions.get(session_id)
    message_count = len(session.messages) if session is not None else 0
    if message_count == 0 and session_info is not None and state.store is not None:
        message_count = await state.store.count_messages(session_id) + 1
    exists = (
        session is not None
        or session_info is not None
        or session_id in state.session_provider_models
        or session_id in state.session_projects
        or session_id in state.session_thinking_levels
    )
    return {
        "id": session_id,
        "title": session_info.title if session_info is not None else "",
        "model": _session_model_label(state, session_id, session, session_info),
        "project_dir": _session_project_dir(state, session_id, session, session_info),
        "thinking_level": _session_thinking_level(state, session_id, session, session_info),
        "rule_overrides": serialize_session_rule_overrides(
            _session_rule_overrides(state, session_id)
        ),
        "messages": message_count,
        "created_at": session_info.created_at if session_info is not None else "",
        "updated_at": session_info.updated_at if session_info is not None else "",
        "exists": exists,
    }


def _serialize_message(message: Message) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "role": message.role.value,
        "content": message.content,
    }
    if message.reasoning:
        payload["reasoning"] = message.reasoning
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": tool_call.id,
                "name": tool_call.name,
                "arguments": tool_call.arguments,
            }
            for tool_call in message.tool_calls
        ]
    if message.tool_result is not None:
        payload["tool_result"] = {
            "tool_call_id": message.tool_result.tool_call_id,
            "content": message.tool_result.content,
            "is_error": message.tool_result.is_error,
            "display": message.tool_result.display,
        }
    if message.attachments:
        payload["attachments"] = [
            {
                "path": attachment.path,
                "mime_type": attachment.mime_type,
                "name": attachment.name,
            }
            for attachment in message.attachments
        ]
    return payload


async def _session_history_messages(state: ServerState, session_id: str) -> list[Message]:
    session = state.sessions.get(session_id)
    if session is not None:
        return list(session.messages[1:])
    if state.store is not None:
        session_info = await state.store.get_session(session_id)
        if session_info is not None:
            return await state.store.get_messages(session_id)
    if (
        session_id in state.session_provider_models
        or session_id in state.session_projects
        or session_id in state.session_thinking_levels
    ):
        return []
    raise RuntimeError("Session not found")


async def _get_or_create_server_session(
    state: ServerState,
    session_id: str,
) -> AgentSession:
    session = state.sessions.get(session_id)
    if session is not None:
        return session
    if len(state.sessions) >= state.config.server.max_sessions:
        raise RuntimeError(f"Maximum sessions reached ({state.config.server.max_sessions})")
    return await _create_server_session(state, session_id)


async def _session_extension_commands(state: ServerState, session_id: str) -> list[str]:
    session = await _get_or_create_server_session(state, session_id)
    return sorted(session.hooks.commands)


async def _run_session_extension_command(
    state: ServerState,
    session_id: str,
    command_name: str,
    arg: str,
) -> tuple[str | None, dict[str, Any]]:
    session = await _get_or_create_server_session(state, session_id)
    handler = session.hooks.commands.get(command_name)
    if handler is None:
        raise RuntimeError(f"Unknown command: {command_name}")
    result = await handler(arg)
    return result, await _serialize_session(state, session_id)


async def _list_serialized_sessions(
    state: ServerState,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    if state.store is None:
        return [await _serialize_session(state, sid) for sid in state.sessions]
    stored_sessions = await state.store.list_sessions(limit=limit)
    payload = [await _serialize_session(state, info.id, info) for info in stored_sessions]
    seen = {info.id for info in stored_sessions}
    extra_ids = (
        set(state.sessions)
        | set(state.session_provider_models)
        | set(state.session_projects)
        | set(state.session_thinking_levels)
    )
    for session_id in sorted(extra_ids):
        if session_id not in seen:
            payload.append(await _serialize_session(state, session_id))
    return payload


async def _request_tool_permission(
    state: ServerState,
    session_id: str,
    tool_name: str,
    args: dict[str, Any],
) -> bool:
    if session_id in state.auto_approve_sessions:
        return True
    controller = _get_session_controller(state, session_id)
    if not controller.has_subscribers:
        return False

    request_id = secrets.token_hex(16)
    future: asyncio.Future[tuple[bool, bool]] = asyncio.get_running_loop().create_future()
    state.pending_permissions[request_id] = (session_id, future)
    await controller.publish(
        {
            "type": "permission_request",
            "request_id": request_id,
            "tool": tool_name,
            "args": args,
        }
    )
    try:
        approved, remember = await future
    finally:
        state.pending_permissions.pop(request_id, None)
    if approved and remember:
        state.auto_approve_sessions.add(session_id)
    return approved


def _server_permission_callback(
    state: ServerState,
    session_id: str,
) -> Callable[[str, dict[str, Any]], Awaitable[bool]]:
    async def _callback(tool_name: str, args: dict[str, Any]) -> bool:
        return await _request_tool_permission(state, session_id, tool_name, args)

    return _callback


async def _create_server_session(
    state: ServerState,
    session_id: str,
    *,
    provider_name: str | None = None,
    model_id: str | None = None,
    project_dir: str | None = None,
    prior_messages: list[Any] | None = None,
    session_config: ArtelConfig | None = None,
) -> AgentSession:
    from artel_core.cli import _resolve_api_key

    stored_info, stored_messages = await _stored_session_context(state, session_id)
    effective_config = session_config or state.config
    resolved_provider, resolved_model = (
        (provider_name, model_id)
        if provider_name is not None and model_id is not None
        else (
            stored_info.model.split("/", 1)
            if stored_info is not None and "/" in stored_info.model
            else resolve_model(effective_config)
        )
    )
    resolved_project_dir = project_dir or _session_project_dir(
        state,
        session_id,
        None,
        stored_info,
    )
    runtime = await bootstrap_runtime(
        effective_config,
        resolved_provider,
        resolved_model,
        project_dir=resolved_project_dir,
        resolve_api_key=_resolve_api_key,
        include_extensions=True,
        runtime="server",
    )
    permission_callback = state.permission_callbacks.get(session_id)
    if permission_callback is None:
        permission_callback = _server_permission_callback(state, session_id)
    session = create_agent_session_from_bootstrap(
        effective_config,
        runtime,
        project_dir=resolved_project_dir,
        store=state.store,
        session_id=session_id,
        permission_callback=permission_callback,
    )
    session.rule_overrides = _session_rule_overrides(state, session_id)
    session.refresh_system_prompt()
    controller = _get_session_controller(state, session_id)
    session.board_event_callback = (  # type: ignore[attr-defined]
        lambda kind, payload: asyncio.create_task(
            controller.publish({"type": "board_event", "event": kind, "payload": payload})
        )
    )
    session.thinking_level = _session_thinking_level(
        state,
        session_id,
        None,
        stored_info,
    )  # type: ignore[assignment]
    messages_to_restore = prior_messages if prior_messages is not None else stored_messages
    if messages_to_restore:
        session.messages.extend(messages_to_restore)
    state.sessions[session_id] = session
    model_label = f"{resolved_provider}/{resolved_model}"
    await _initialize_session_state(
        state,
        session_id,
        model=model_label,
        project_dir=resolved_project_dir,
        thinking_level=_session_thinking_level(state, session_id, session, stored_info),
    )
    return session


async def _switch_server_session_model(
    state: ServerState,
    session_id: str,
    model_str: str,
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    if "/" not in model_str:
        raise RuntimeError("Format: provider/model-id")
    provider_name, model_id = model_str.split("/", 1)
    model_info = await get_effective_model_info(state.config, provider_name, model_id)
    if model_info is None:
        raise RuntimeError(f"Model '{model_id}' not found for provider '{provider_name}'.")

    previous = state.sessions.pop(session_id, None)
    stored_info, stored_messages = await _stored_session_context(state, session_id)
    prior_messages = previous.messages[1:] if previous is not None else stored_messages
    project_dir = _session_project_dir(state, session_id, previous, stored_info)
    if previous is not None:
        with suppress(Exception):
            await previous.provider.close()
    if previous is None and not prior_messages:
        await _initialize_session_state(
            state,
            session_id,
            model=model_str,
            project_dir=project_dir,
        )
        return await _serialize_session(state, session_id)

    await _create_server_session(
        state,
        session_id,
        provider_name=provider_name,
        model_id=model_id,
        project_dir=project_dir,
        prior_messages=prior_messages,
    )
    return await _serialize_session(state, session_id)


async def _resolve_session_project_dir(
    state: ServerState,
    session_id: str,
    project_dir: str,
) -> str:
    requested = project_dir.strip()
    if not requested:
        raise RuntimeError("Missing project_dir")

    stored_info, _ = await _stored_session_context(state, session_id)
    current_dir = _session_project_dir(
        state,
        session_id,
        state.sessions.get(session_id),
        stored_info,
    )
    expanded = Path(requested).expanduser()
    candidate = expanded if expanded.is_absolute() else Path(current_dir) / expanded
    resolved = candidate.resolve()
    if not resolved.exists():
        raise RuntimeError(f"Directory not found: {resolved}")
    if not resolved.is_dir():
        raise RuntimeError(f"Not a directory: {resolved}")
    return str(resolved)


async def _set_server_session_project(
    state: ServerState,
    session_id: str,
    project_dir: str,
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    resolved_project_dir = await _resolve_session_project_dir(state, session_id, project_dir)
    previous = state.sessions.pop(session_id, None)
    stored_info, stored_messages = await _stored_session_context(state, session_id)
    if previous is None and not stored_messages:
        model_label = _session_model_label(state, session_id, previous, stored_info)
        await _initialize_session_state(
            state,
            session_id,
            model=model_label,
            project_dir=resolved_project_dir,
        )
        return await _serialize_session(state, session_id)

    prior_messages = previous.messages[1:] if previous is not None else stored_messages
    model_label = _session_model_label(state, session_id, previous, stored_info)
    if "/" in model_label:
        provider_name, model_id = model_label.split("/", 1)
    else:
        provider_name, model_id = resolve_model(state.config)
    if previous is not None:
        with suppress(Exception):
            await previous.provider.close()
    await _create_server_session(
        state,
        session_id,
        provider_name=provider_name,
        model_id=model_id,
        project_dir=resolved_project_dir,
        prior_messages=prior_messages,
    )
    return await _serialize_session(state, session_id)


async def _set_server_session_thinking(
    state: ServerState,
    session_id: str,
    thinking_level: str,
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    valid = ("off", "minimal", "low", "medium", "high", "xhigh")
    level = thinking_level.strip().lower()
    if level not in valid:
        raise RuntimeError(f"Invalid thinking level: {thinking_level}")
    state.session_thinking_levels[session_id] = level
    session = state.sessions.get(session_id)
    if session is not None:
        session.thinking_level = level  # type: ignore[assignment]
    stored_info, _ = await _stored_session_context(state, session_id)
    await _persist_session_record(
        state,
        session_id,
        model=_session_model_label(state, session_id, session, stored_info),
        project_dir=_session_project_dir(state, session_id, session, stored_info),
        thinking_level=level,
    )
    return await _serialize_session(state, session_id)


async def _rename_server_session(
    state: ServerState,
    session_id: str,
    title: str,
) -> dict[str, Any]:
    if not title.strip():
        raise RuntimeError("Missing title")
    model_label = _session_model_label(state, session_id, state.sessions.get(session_id))
    project_dir = _session_project_dir(state, session_id, state.sessions.get(session_id))
    await _initialize_session_state(
        state,
        session_id,
        model=model_label,
        project_dir=project_dir,
    )
    if state.store is None:
        raise RuntimeError("Session store not available")
    await state.store.rename_session(session_id, title.strip())
    return await _serialize_session(state, session_id)


async def _session_tree_nodes(
    state: ServerState,
    session_id: str,
) -> list[dict[str, Any]]:
    if state.store is None:
        raise RuntimeError("Session store not available")
    return await state.store.get_message_nodes(session_id)


async def _compact_server_session(
    state: ServerState,
    session_id: str,
    custom_prompt: str = "",
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    session = state.sessions.get(session_id)
    if session is None:
        session = await _create_server_session(state, session_id)
    summary = await session.compact(custom_prompt)
    return {
        "summary": summary,
        "session": await _serialize_session(state, session_id),
    }


async def _fork_server_session(
    state: ServerState,
    session_id: str,
    up_to_message_idx: int | None = None,
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    if state.store is None:
        raise RuntimeError("Session store not available")
    new_session_id = str(uuid.uuid4())
    await state.store.fork_session(
        session_id,
        new_session_id,
        up_to_message_idx=up_to_message_idx,
    )
    info = await state.store.get_session(new_session_id)
    if info is not None:
        state.session_provider_models[new_session_id] = info.model
        state.session_projects[new_session_id] = info.project_dir
        if info.thinking_level:
            state.session_thinking_levels[new_session_id] = info.thinking_level
    return {
        "session_id": new_session_id,
        "session": await _serialize_session(state, new_session_id, info),
    }


async def _inject_skill_into_session(
    state: ServerState,
    session_id: str,
    skill_name: str,
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    session = state.sessions.get(session_id)
    if session is None:
        session = await _create_server_session(state, session_id)

    from artel_core.skills import inject_skill, load_skills

    project_dir = _session_project_dir(state, session_id, session)
    skills = load_skills(project_dir)
    skill = skills.get(skill_name)
    if skill is None:
        raise RuntimeError(f"Skill '{skill_name}' not found")
    session.system_prompt = inject_skill(session.system_prompt, skill)
    session.messages[0].content = session.system_prompt
    return await _serialize_session(state, session_id)


async def _reload_server_session_runtime(
    state: ServerState,
    session_id: str,
) -> dict[str, Any]:
    await _ensure_session_idle(state, session_id)
    import importlib

    previous = state.sessions.pop(session_id, None)
    stored_info, stored_messages = await _stored_session_context(state, session_id)
    prior_messages = previous.messages[1:] if previous is not None else stored_messages
    model_label = _session_model_label(state, session_id, previous, stored_info)
    project_dir = _session_project_dir(state, session_id, previous, stored_info)
    thinking_level = _session_thinking_level(state, session_id, previous, stored_info)
    importlib.invalidate_caches()
    if previous is not None:
        with suppress(Exception):
            await previous.provider.close()
    if "/" in model_label:
        provider_name, model_id = model_label.split("/", 1)
    else:
        provider_name, model_id = resolve_model(state.config)
    state.session_thinking_levels[session_id] = thinking_level
    await _create_server_session(
        state,
        session_id,
        provider_name=provider_name,
        model_id=model_id,
        project_dir=project_dir,
        prior_messages=prior_messages,
    )
    return await _serialize_session(state, session_id)


def _extract_persistent_cd_target(command: str) -> str | None:
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not parts or parts[0] != "cd":
        return None
    if len(parts) == 1:
        return "~"
    if len(parts) == 2:
        return parts[1]
    return None


async def _run_server_shell(command: str, *, cwd: str) -> tuple[str, int]:
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode(errors="replace").rstrip()
    if len(output) > 5000:
        output = output[:5000] + f"\n... (truncated, {len(stdout)} bytes total)"
    return output, proc.returncode


async def handle_client(ws: ServerConnection, state: ServerState) -> None:
    """Handle a single WebSocket client connection."""
    logger.info("Client connected: %s", ws.remote_address)
    stream_tasks: set[asyncio.Task[None]] = set()
    ws._artel_stream_tasks = stream_tasks

    try:
        async for raw_msg in ws:
            try:
                msg = json.loads(raw_msg)
            except json.JSONDecodeError:
                await ws.send(json.dumps({"type": "error", "error": "Invalid JSON"}))
                continue

            msg_type = msg.get("type", "")

            if msg_type == "message":
                await _handle_message(ws, msg, state)
            elif msg_type == "cancel":
                session_id = str(msg.get("session_id", "")).strip()
                if not session_id:
                    await ws.send(json.dumps({"type": "error", "error": "Missing session_id"}))
                    continue
                session = state.sessions.get(session_id)
                if session is not None:
                    session.abort()
            elif msg_type == "steer":
                await _handle_steer(ws, msg, state)
            elif msg_type == "approve_tool":
                await _handle_tool_approval(ws, msg, state)
            else:
                await ws.send(json.dumps({"type": "error", "error": f"Unknown type: {msg_type}"}))

    except websockets.exceptions.ConnectionClosed:
        logger.info("Client disconnected: %s", ws.remote_address)
    finally:
        stream_tasks = getattr(ws, "_artel_stream_tasks", stream_tasks)
        for task in list(stream_tasks):
            task.cancel()
        for task in list(stream_tasks):
            with suppress(asyncio.CancelledError, Exception):
                await task
        for _request_id, (session_id, future) in list(state.pending_permissions.items()):
            controller = state.session_controllers.get(session_id)
            if future.done():
                continue
            if controller is None or not controller.has_subscribers:
                future.set_result((False, False))


def _parse_attachments(payload: Any) -> list[ImageAttachment] | None:
    if not isinstance(payload, list):
        return None
    attachments: list[ImageAttachment] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        if not path:
            continue
        attachments.append(
            ImageAttachment(
                path=path,
                mime_type=str(item.get("mime_type", "image/png") or "image/png"),
                name=str(item.get("name", "") or ""),
            )
        )
    return attachments or None


async def _handle_message(ws: ServerConnection, msg: dict[str, Any], state: ServerState) -> None:
    """Start a user message run and stream it to this websocket subscriber."""
    session_id = str(msg.get("session_id", "default")).strip() or "default"
    content = str(msg.get("content", ""))

    attachments = _parse_attachments(msg.get("attachments"))

    if os.environ.get("ARTEL_DEBUG_ATTACHMENTS", "") in {"1", "true", "yes", "on"}:
        logger.warning(
            "ws message session=%s content_len=%s attachments=%s",
            session_id,
            len(content),
            [
                {"name": attachment.name, "mime": attachment.mime_type, "path": attachment.path}
                for attachment in (attachments or [])
            ],
        )

    if not content and not attachments:
        await ws.send(json.dumps({"type": "error", "error": "Empty message"}))
        return

    session_exists = (
        session_id in state.sessions
        or session_id in state.session_provider_models
        or session_id in state.session_projects
        or (state.store is not None and await state.store.get_session(session_id) is not None)
    )
    if not session_exists and len(state.sessions) >= state.config.server.max_sessions:
        await ws.send(
            json.dumps(
                {
                    "type": "error",
                    "error": f"Maximum sessions reached ({state.config.server.max_sessions})",
                }
            )
        )
        return

    controller = _get_session_controller(state, session_id)
    queue = controller.subscribe()
    try:
        await controller.start(content, attachments=attachments)
    except Exception as exc:
        controller.unsubscribe(queue)
        await ws.send(json.dumps({"type": "error", "error": str(exc)}))
        return

    async def _stream_events() -> None:
        try:
            while True:
                payload = await queue.get()
                await ws.send(json.dumps(payload))
                if payload.get("type") == "error":
                    break
                if payload.get("type") == "status" and payload.get("busy") is False:
                    break
        finally:
            controller.unsubscribe(queue)

    task = asyncio.create_task(_stream_events(), name=f"artel-ws-stream-{session_id}")
    stream_tasks = getattr(ws, "_artel_stream_tasks", None)
    if stream_tasks is None:
        stream_tasks = set()
        ws._artel_stream_tasks = stream_tasks
    stream_tasks.add(task)
    task.add_done_callback(lambda done: stream_tasks.discard(done))


async def _handle_steer(
    ws: ServerConnection,
    msg: dict[str, Any],
    state: ServerState,
) -> None:
    session_id = str(msg.get("session_id", "default")).strip() or "default"
    content = str(msg.get("content", "")).strip()
    if not content:
        await ws.send(json.dumps({"type": "error", "error": "Empty steer message"}))
        return
    session = state.sessions.get(session_id)
    controller = state.session_controllers.get(session_id)
    if session is None or controller is None or not controller.running:
        await ws.send(json.dumps({"type": "error", "error": "Session is not running"}))
        return
    session.steer(content)
    await ws.send(json.dumps({"type": "status", "state": "steer queued", "busy": True}))


async def _handle_tool_approval(
    ws: ServerConnection,
    msg: dict[str, Any],
    state: ServerState,
) -> None:
    request_id = str(msg.get("request_id", "")).strip()
    decision = str(msg.get("decision", "")).strip().lower()
    if not request_id:
        await ws.send(json.dumps({"type": "error", "error": "Missing request_id"}))
        return
    pending = state.pending_permissions.get(request_id)
    if pending is None or pending[1].done():
        await ws.send(json.dumps({"type": "error", "error": "Permission request not found"}))
        return
    if decision not in {"once", "all", "deny"}:
        await ws.send(json.dumps({"type": "error", "error": "Invalid permission decision"}))
        return
    pending[1].set_result((decision in {"once", "all"}, decision == "all"))


# ── REST API (aiohttp) ────────────────────────────────────────────


def _create_rest_app(state: ServerState, token: str) -> Any:
    """Create aiohttp REST application for management endpoints."""
    from aiohttp import web

    @web.middleware
    async def auth_middleware(
        request: web.Request,
        handler: Any,
    ) -> web.StreamResponse:
        # Skip auth for health endpoint
        if request.path == "/api/health":
            return await handler(request)
        auth_header = request.headers.get("Authorization", "")
        if token and auth_header != f"Bearer {token}":
            return web.json_response({"error": "Unauthorized"}, status=401)
        return await handler(request)

    async def handle_health(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "sessions": len(state.sessions),
                "max_sessions": state.config.server.max_sessions,
            }
        )

    def _project_config_path() -> Path:
        return effective_project_config_path(state.default_project_dir)

    def _read_text_file(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except OSError as exc:
            return f"<error reading file: {exc}>"

    def _redact_text(text: str) -> str:
        if not text:
            return ""
        lines: list[str] = []
        secret_tokens = (
            "api_key",
            "token",
            "secret",
            "password",
            "access_key",
            "refresh_token",
        )
        for line in text.splitlines():
            if "=" in line:
                key, sep, value = line.partition("=")
                lowered = key.strip().lower().replace("-", "_")
                has_secret = any(token in lowered for token in secret_tokens)
                has_value = value.strip().strip('"').strip("'")
                if has_secret and has_value:
                    lines.append(f'{key}{sep} "***REDACTED***"')
                    continue
            lines.append(line)
        return "\n".join(lines)

    def _redact_value(value: Any, key: str = "") -> Any:
        lowered = key.lower().replace("-", "_")
        secret_tokens = (
            "api_key",
            "token",
            "secret",
            "password",
            "access_key",
            "refresh_token",
        )
        if isinstance(value, dict):
            return {str(k): _redact_value(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [_redact_value(item, key) for item in value]
        if any(token in lowered for token in secret_tokens):
            if value in ("", None, False):
                return value
            return "***REDACTED***"
        return value

    async def handle_server_info(request: web.Request) -> web.Response:
        schedule_summary = (
            state.schedule_service.snapshot()
            if state.schedule_service is not None
            else {
                "enabled": False,
                "count": 0,
                "next_run_at": "",
            }
        )
        return web.json_response(
            {
                "version": "0.1.0",
                "runtime_mode": "server",
                "project_dir": state.default_project_dir,
                "sessions_db": state.config.sessions.db_path,
                "default_model": state.config.agent.model,
                "auth_enabled": bool(token),
                "max_sessions": state.config.server.max_sessions,
                "loaded_extensions": len(state.server_extensions),
                "provider_overlay_path": str(effective_server_provider_overlay_path()),
                "scheduler": schedule_summary,
            }
        )

    async def handle_config_paths(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "global_config": str(effective_global_config_path()),
                "project_config": str(_project_config_path()),
                "sessions_db": state.config.sessions.db_path,
                "provider_overlay": str(effective_server_provider_overlay_path()),
                "config_dir": str(CONFIG_DIR),
            }
        )

    async def handle_config_effective(request: web.Request) -> web.Response:
        effective = state.config.model_dump(exclude_none=True)
        return web.json_response({"config": _redact_value(effective)})

    async def handle_server_diagnostics(request: web.Request) -> web.Response:
        scheduler_snapshot = (
            state.schedule_service.snapshot() if state.schedule_service is not None else None
        )
        return web.json_response(
            {
                "active_sessions": len(state.sessions),
                "loaded_extensions": len(state.server_extensions),
                "pending_oauth": len(state.pending_oauth),
                "permission_requests": len(state.pending_permissions),
                "auto_approve_sessions": len(state.auto_approve_sessions),
                "project_dir_exists": Path(state.default_project_dir).exists(),
                "global_config_exists": effective_global_config_path().exists(),
                "project_config_exists": _project_config_path().exists(),
                "provider_overlay_exists": effective_server_provider_overlay_path().exists(),
                "sessions_db_exists": Path(state.config.sessions.db_path).exists(),
                "scheduler": scheduler_snapshot,
            }
        )

    async def handle_config_raw(request: web.Request) -> web.Response:
        scope = str(request.query.get("scope", "project")).strip().lower() or "project"
        if scope == "global":
            path = effective_global_config_path()
        elif scope == "project":
            path = _project_config_path()
        else:
            return web.json_response({"error": "Invalid scope"}, status=400)
        return web.json_response(
            {
                "scope": scope,
                "path": str(path),
                "exists": path.exists(),
                "content": _redact_text(_read_text_file(path)),
            }
        )

    async def handle_config_init(request: web.Request) -> web.Response:
        generate_global_config()
        generate_project_config(state.default_project_dir)
        return web.json_response(
            {
                "ok": True,
                "message": "Initialized Artel config.",
                "global_config": str(GLOBAL_CONFIG),
                "project_config": str(project_config_path(state.default_project_dir)),
                "agents_path": str(project_agents_path(state.default_project_dir)),
            }
        )

    async def handle_providers(request: web.Request) -> web.Response:
        from artel_core.cli import _resolve_api_key

        entries = await collect_provider_setup_entries(state.config, _resolve_api_key)
        return web.json_response(
            {
                "providers": [
                    {
                        "id": entry.id,
                        "name": entry.name,
                        "status": entry.status,
                        "hint": entry.hint,
                    }
                    for entry in entries
                ]
            }
        )

    async def handle_models(request: web.Request) -> web.Response:
        from artel_core.cli import _resolve_api_key

        catalog = await get_effective_provider_catalog(state.config)
        providers_payload: list[dict[str, Any]] = []
        for provider_id, provider in catalog.items():
            requires_key = provider_requires_api_key(state.config, provider_id)
            api_key, _ = await _resolve_api_key(state.config, provider_id)
            if not api_key and requires_key:
                continue
            providers_payload.append(
                {
                    "id": provider_id,
                    "name": provider.name,
                    "models": [
                        {
                            "id": model.id,
                            "name": model.name,
                            "context_window": model.context_window,
                        }
                        for model in provider.models
                    ],
                }
            )
        return web.json_response({"providers": providers_payload})

    async def handle_prompts_list(request: web.Request) -> web.Response:
        prompts = load_prompts(state.default_project_dir)
        return web.json_response(
            {
                "prompts": [
                    {
                        "name": name,
                        "preview": content[:120].replace("\n", " "),
                    }
                    for name, content in sorted(prompts.items())
                ]
            }
        )

    async def handle_prompt_render(request: web.Request) -> web.Response:
        prompt_name = request.match_info["prompt_name"]
        prompts = load_prompts(state.default_project_dir)
        template = prompts.get(prompt_name)
        if not template:
            return web.json_response({"error": f"Prompt '{prompt_name}' not found"}, status=404)
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        arg = str(payload.get("arg", ""))
        variables: dict[str, str] = {"input": arg} if arg else {}
        if arg and "=" in arg:
            variables = {}
            for pair in arg.split():
                if "=" in pair:
                    key, _, value = pair.partition("=")
                    variables[key] = value
                else:
                    variables.setdefault("input", "")
                    variables["input"] += (" " if variables.get("input") else "") + pair
        rendered = render_prompt(template, variables)
        return web.json_response({"name": prompt_name, "content": rendered})

    async def handle_skills_list(request: web.Request) -> web.Response:
        skills = load_skills(state.default_project_dir)
        return web.json_response(
            {
                "skills": [
                    {
                        "name": skill.name,
                        "description": skill.description,
                        "source": str(skill.source),
                    }
                    for skill in sorted(skills.values(), key=lambda item: item.name)
                ]
            }
        )

    async def handle_extensions_list(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "extensions": [
                    {
                        "name": ext.name,
                        "version": ext.version,
                        "source": ext.source,
                    }
                    for ext in list_installed_extensions()
                ]
            }
        )

    async def handle_extension_install(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        source = str(payload.get("source", "")).strip()
        if not source:
            return web.json_response({"error": "Missing source"}, status=400)
        ok, message = install_extension(source)
        status = 200 if ok else 400
        return web.json_response({"ok": ok, "message": message}, status=status)

    async def handle_extension_remove(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        ok, message = remove_extension(name)
        status = 200 if ok else 400
        return web.json_response({"ok": ok, "message": message}, status=status)

    async def handle_extension_update(request: web.Request) -> web.Response:
        name = request.match_info.get("name", "")
        if name:
            ok, message = update_extension(name)
            status = 200 if ok else 400
            return web.json_response({"ok": ok, "message": message}, status=status)
        results = update_all_extensions()
        return web.json_response(
            {
                "results": [
                    {"name": ext_name, "ok": ok, "message": msg} for ext_name, ok, msg in results
                ]
            }
        )

    async def handle_extension_search(request: web.Request) -> web.Response:
        query = str(request.query.get("q", "")).strip()
        if not query:
            return web.json_response({"results": []})
        results = search_extensions(state.default_project_dir, query)
        return web.json_response(
            {
                "results": [
                    {
                        "name": item.name,
                        "description": item.description,
                        "repo": item.repo,
                        "author": item.author,
                        "registry_name": item.registry_name,
                        "tags": item.tags,
                    }
                    for item in results
                ]
            }
        )

    async def handle_extension_registries_list(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "registries": [
                    {"name": reg.name, "url": reg.url}
                    for reg in list_registry_entries(state.default_project_dir)
                ]
            }
        )

    async def handle_extension_registries_add(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        name = str(payload.get("name", "")).strip()
        url = str(payload.get("url", "")).strip()
        if not name or not url:
            return web.json_response({"error": "Missing name or url"}, status=400)
        ok, message = add_registry(name, url)
        status = 200 if ok else 400
        return web.json_response({"ok": ok, "message": message}, status=status)

    async def handle_extension_registries_remove(request: web.Request) -> web.Response:
        name = request.match_info["name"]
        ok, message = remove_registry(name)
        status = 200 if ok else 400
        return web.json_response({"ok": ok, "message": message}, status=status)

    async def handle_mcp_get(request: web.Request) -> web.Response:
        registry = MCPRegistry()
        loaded = registry.load_merged_config(state.default_project_dir)
        if state.mcp_runtime is not None:
            runtime_status = state.mcp_runtime.status_payload()
            status_text = state.mcp_runtime.status_text()
        else:
            runtime_status = {
                "available": False,
                "sources": [str(path) for path in loaded.sources],
                "servers": [],
                "summary": {
                    "connected": 0,
                    "disabled": 0,
                    "failed": 0,
                    "needs_auth": 0,
                    "timeout": 0,
                    "unavailable": len(loaded.servers),
                    "total": len(loaded.servers),
                },
            }
            status_text = "MCP runtime unavailable."
        return web.json_response(
            {
                "sources": [str(path) for path in loaded.sources],
                "servers": {name: asdict(server) for name, server in loaded.servers.items()},
                "status": status_text,
                "runtime": runtime_status,
            }
        )

    async def handle_mcp_reload(request: web.Request) -> web.Response:
        if state.mcp_runtime is None:
            state.mcp_runtime = McpRuntimeManager()
            await state.mcp_runtime.load(
                ExtensionContext(
                    project_dir=state.default_project_dir,
                    runtime="server",
                    config=state.config,
                    extras={},
                )
            )
        else:
            await state.mcp_runtime.reload()
        return web.json_response(
            {
                "status": state.mcp_runtime.status_text(),
                "runtime": state.mcp_runtime.status_payload(),
            }
        )

    async def handle_mcp_config_get(request: web.Request) -> web.Response:
        scope = str(request.query.get("scope", "effective")).strip().lower() or "effective"
        registry = MCPRegistry()
        if scope == "global":
            config = registry.load_global_config()
            return web.json_response({"servers": [asdict(server) for server in config.servers]})
        if scope == "project":
            config = registry.load_project_config(state.default_project_dir)
            return web.json_response({"servers": [asdict(server) for server in config.servers]})
        loaded = registry.load_merged_config(state.default_project_dir)
        return web.json_response(
            {
                "sources": [str(path) for path in loaded.sources],
                "servers": {name: asdict(server) for name, server in loaded.servers.items()},
            }
        )

    async def handle_mcp_config_put(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        scope = str(payload.get("scope", "project")).strip().lower() or "project"
        servers_payload = payload.get("servers", [])
        if not isinstance(servers_payload, list):
            return web.json_response({"error": "servers must be a list"}, status=400)
        servers: list[MCPServerConfig] = []
        for item in servers_payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            if not name:
                continue
            servers.append(
                MCPServerConfig(name=name, **{k: v for k, v in item.items() if k != "name"})
            )
        registry = MCPRegistry()
        if scope == "global":
            written = registry.write_global_config(MCPConfig(servers=servers))
        else:
            written = registry.write_project_config(
                state.default_project_dir, MCPConfig(servers=servers)
            )
        return web.json_response(
            {"path": str(written), "servers": [asdict(server) for server in servers]}
        )

    async def handle_mcp_server_put(request: web.Request) -> web.Response:
        scope = str(request.query.get("scope", "project")).strip().lower() or "project"
        name = request.match_info["name"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        registry = MCPRegistry()
        if scope == "global":
            current = registry.load_global_config()
        else:
            current = registry.load_project_config(state.default_project_dir)
        servers = {server.name: server for server in current.servers}
        servers[name] = MCPServerConfig(name=name, **payload)
        config = MCPConfig(servers=sorted(servers.values(), key=lambda item: item.name))
        if scope == "global":
            written = registry.write_global_config(config)
        else:
            written = registry.write_project_config(state.default_project_dir, config)
        return web.json_response({"path": str(written), "server": asdict(servers[name])})

    async def handle_mcp_server_delete(request: web.Request) -> web.Response:
        scope = str(request.query.get("scope", "project")).strip().lower() or "project"
        name = request.match_info["name"]
        registry = MCPRegistry()
        if scope == "global":
            current = registry.load_global_config()
            kept = [server for server in current.servers if server.name != name]
            written = registry.write_global_config(MCPConfig(servers=kept))
        else:
            current = registry.load_project_config(state.default_project_dir)
            kept = [server for server in current.servers if server.name != name]
            written = registry.write_project_config(
                state.default_project_dir, MCPConfig(servers=kept)
            )
        return web.json_response({"path": str(written), "deleted": name})

    async def handle_delegates_list(request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        runs = get_delegation_registry().list_runs(session_id)
        return web.json_response({"delegates": [run.to_payload() for run in runs]})

    async def handle_delegate_get(request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        run_id = request.match_info["run_id"]
        run = get_delegation_registry().get_session_run(session_id, run_id)
        if run is None:
            return web.json_response({"error": "Delegate not found"}, status=404)
        return web.json_response(
            {"delegate": run.to_payload(include_result=True, include_events=True)}
        )

    async def handle_delegate_cancel(request: web.Request) -> web.Response:
        session_id = request.match_info["session_id"]
        run_id = request.match_info["run_id"]
        run = get_delegation_registry().get_session_run(session_id, run_id)
        if run is None:
            return web.json_response({"error": "Delegate not found"}, status=404)
        cancelled = get_delegation_registry().cancel(run_id)
        return web.json_response(
            {
                "cancelled": cancelled,
                "delegate": run.to_payload(include_result=True, include_events=True),
            }
        )

    async def handle_sessions_list(request: web.Request) -> web.Response:
        sessions_info = await _list_serialized_sessions(state)
        return web.json_response({"sessions": sessions_info})

    async def handle_session_get(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        return web.json_response({"session": await _serialize_session(state, sid)})

    async def handle_session_messages_get(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            messages = await _session_history_messages(state, sid)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=404)
        return web.json_response(
            {"messages": [_serialize_message(message) for message in messages]}
        )

    async def handle_session_tree_get(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            nodes = await _session_tree_nodes(state, sid)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=404)
        return web.json_response({"nodes": nodes})

    async def handle_session_commands_get(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            commands = await _session_extension_commands(state, sid)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"commands": commands})

    async def handle_session_command_post(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        command_name = request.match_info["command_name"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        arg = str(payload.get("arg", ""))
        try:
            output, session = await _run_session_extension_command(
                state,
                sid,
                command_name,
                arg,
            )
        except RuntimeError as exc:
            status = 404 if str(exc).startswith("Unknown command: ") else 400
            return web.json_response({"error": str(exc)}, status=status)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(
            {
                "command": command_name,
                "output": output,
                "session": session,
            }
        )

    async def handle_session_model_put(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        model = str(payload.get("model", "")).strip()
        if not model:
            return web.json_response({"error": "Missing model"}, status=400)
        try:
            session = await _switch_server_session_model(state, sid, model)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"session": session})

    async def handle_session_title_put(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        title = str(payload.get("title", "")).strip()
        if not title:
            return web.json_response({"error": "Missing title"}, status=400)
        try:
            session = await _rename_server_session(state, sid, title)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"session": session})

    async def handle_session_project_put(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        project_dir = str(payload.get("project_dir", "")).strip()
        if not project_dir:
            return web.json_response({"error": "Missing project_dir"}, status=400)
        try:
            session = await _set_server_session_project(state, sid, project_dir)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"session": session})

    async def handle_session_thinking_put(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        thinking_level = str(payload.get("thinking_level", "")).strip()
        if not thinking_level:
            return web.json_response({"error": "Missing thinking_level"}, status=400)
        try:
            session = await _set_server_session_thinking(state, sid, thinking_level)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"session": session})

    async def handle_session_tasks_get(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        session = state.sessions.get(sid)
        project_dir = _session_project_dir(state, sid, session)
        content = await read_project_board_file(tasks_path(project_dir))
        return web.json_response({"content": content, "path": str(tasks_path(project_dir))})

    async def handle_session_tasks_put(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        content = str(payload.get("content", ""))
        session = state.sessions.get(sid)
        project_dir = _session_project_dir(state, sid, session)
        await write_project_board_file(tasks_path(project_dir), content)
        return web.json_response({"ok": True, "path": str(tasks_path(project_dir))})

    async def handle_session_notes_get(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        session = state.sessions.get(sid)
        project_dir = _session_project_dir(state, sid, session)
        content = await read_project_board_file(operator_notes_path(project_dir))
        return web.json_response(
            {"content": content, "path": str(operator_notes_path(project_dir))}
        )

    async def handle_session_notes_put(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        content = str(payload.get("content", ""))
        session = state.sessions.get(sid)
        project_dir = _session_project_dir(state, sid, session)
        await write_project_board_file(operator_notes_path(project_dir), content)
        return web.json_response({"ok": True, "path": str(operator_notes_path(project_dir))})

    async def handle_session_compact_post(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        prompt = str(payload.get("prompt", ""))
        try:
            result = await _compact_server_session(state, sid, prompt)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(result)

    async def handle_session_fork_post(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        message_index = payload.get("message_index")
        if message_index is not None and not isinstance(message_index, int):
            return web.json_response({"error": "Invalid message_index"}, status=400)
        try:
            result = await _fork_server_session(state, sid, message_index)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(result)

    async def handle_session_skill_post(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        skill_name = str(payload.get("skill", "")).strip()
        if not skill_name:
            return web.json_response({"error": "Missing skill"}, status=400)
        try:
            session = await _inject_skill_into_session(state, sid, skill_name)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"session": session})

    async def handle_session_reload_post(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            session = await _reload_server_session_runtime(state, sid)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"session": session})

    async def handle_session_bash(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        command = str(payload.get("command", "")).strip()
        if not command:
            return web.json_response({"error": "Missing command"}, status=400)
        cd_target = _extract_persistent_cd_target(command)
        if cd_target is not None:
            try:
                session = await _set_server_session_project(
                    state,
                    request.match_info["session_id"],
                    cd_target,
                )
            except Exception as exc:
                return web.json_response({"error": str(exc)}, status=400)
            return web.json_response(
                {
                    "command": command,
                    "output": str(session.get("project_dir", "")),
                    "exit_code": 0,
                    "session": session,
                }
            )

        stored_info, _ = await _stored_session_context(state, request.match_info["session_id"])
        cwd = _session_project_dir(
            state,
            request.match_info["session_id"],
            state.sessions.get(request.match_info["session_id"]),
            stored_info,
        )
        output, exit_code = await _run_server_shell(command, cwd=cwd)
        return web.json_response(
            {
                "command": command,
                "output": output,
                "exit_code": exit_code,
            }
        )

    async def handle_session_wt(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        arg = str(payload.get("arg", ""))
        stored_info, _ = await _stored_session_context(state, sid)
        project_dir = _session_project_dir(state, sid, state.sessions.get(sid), stored_info)
        try:
            from artel_core.worktree import run_worktree_command

            output = await asyncio.to_thread(run_worktree_command, project_dir, arg)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(
            {
                "output": output,
                "session": await _serialize_session(state, sid),
            }
        )

    async def handle_rules_list(request: web.Request) -> web.Response:
        project_dir = _request_project_dir(state, request.query.get("project_dir", ""))
        rules = [_serialize_rule(rule) for rule in list_rules(project_dir)]
        return web.json_response({"rules": rules, "project_dir": project_dir})

    async def handle_rules_post(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        scope = str(payload.get("scope", "")).strip()
        text = str(payload.get("text", "")).strip()
        enabled = bool(payload.get("enabled", True))
        project_dir = _request_project_dir(state, str(payload.get("project_dir", "")))
        try:
            rule = add_rule(scope=scope, text=text, project_dir=project_dir, enabled=enabled)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"rule": _serialize_rule(rule), "project_dir": project_dir})

    async def handle_rule_put(request: web.Request) -> web.Response:
        rule_id = request.match_info["rule_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        project_dir = _request_project_dir(state, str(payload.get("project_dir", "")))
        try:
            rule = update_rule(
                rule_id,
                project_dir=project_dir,
                text=str(payload["text"]).strip()
                if "text" in payload and payload.get("text") is not None
                else None,
                scope=str(payload["scope"]).strip()
                if "scope" in payload and payload.get("scope") is not None
                else None,
                enabled=bool(payload.get("enabled")) if "enabled" in payload else None,
            )
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"rule": _serialize_rule(rule), "project_dir": project_dir})

    async def handle_rule_move(request: web.Request) -> web.Response:
        rule_id = request.match_info["rule_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        project_dir = _request_project_dir(state, str(payload.get("project_dir", "")))
        position = payload.get("position")
        offset = payload.get("offset")
        if position is not None and not isinstance(position, int):
            return web.json_response({"error": "Invalid position"}, status=400)
        if offset is not None and not isinstance(offset, int):
            return web.json_response({"error": "Invalid offset"}, status=400)
        try:
            rule = move_rule(rule_id, project_dir=project_dir, position=position, offset=offset)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response({"rule": _serialize_rule(rule), "project_dir": project_dir})

    async def handle_rule_delete(request: web.Request) -> web.Response:
        rule_id = request.match_info["rule_id"]
        project_dir = _request_project_dir(state, request.query.get("project_dir", ""))
        rule = delete_rule(rule_id, project_dir)
        if rule is None:
            return web.json_response({"error": f"Rule '{rule_id}' not found"}, status=404)
        return web.json_response({"rule": _serialize_rule(rule), "project_dir": project_dir})

    async def handle_session_rules_get(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        overrides = serialize_session_rule_overrides(_session_rule_overrides(state, sid))
        return web.json_response({"session_id": sid, "rule_overrides": overrides})

    async def handle_session_rule_put(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        rule_id = request.match_info["rule_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        if "enabled" not in payload:
            return web.json_response({"error": "Missing enabled"}, status=400)
        raw_enabled = payload.get("enabled")
        overrides = _session_rule_overrides(state, sid)
        if rule_id == "*":
            if raw_enabled is not None:
                return web.json_response(
                    {"error": "Use enabled=null with rule_id '*' to reset all overrides"},
                    status=400,
                )
            clear_session_rule_overrides(overrides)
            enabled = None
        elif raw_enabled is None:
            reset_rule_for_session(overrides, rule_id)
            enabled = None
        else:
            enabled = bool(raw_enabled)
            set_rule_enabled_for_session(overrides, rule_id, enabled)
        session = state.sessions.get(sid)
        if session is not None:
            session.rule_overrides = overrides
            session.refresh_system_prompt()
        return web.json_response(
            {
                "session_id": sid,
                "rule_id": rule_id,
                "enabled": enabled,
                "rule_overrides": serialize_session_rule_overrides(overrides),
                "session": await _serialize_session(state, sid),
            }
        )

    async def handle_credentials_import(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        providers = payload.get("providers", [])
        if not isinstance(providers, list):
            return web.json_response({"error": "Missing providers list"}, status=400)

        imported: list[dict[str, str]] = []
        skipped: list[dict[str, str]] = []
        overlay_changed = False
        token_store = TokenStore()

        for item in providers:
            if not isinstance(item, dict):
                skipped.append({"provider": "", "reason": "Invalid provider payload."})
                continue
            provider_id = str(item.get("provider", "")).strip()
            auth = item.get("auth", {})
            settings = item.get("settings", {})
            if not provider_id or not isinstance(auth, dict) or not isinstance(settings, dict):
                skipped.append(
                    {
                        "provider": provider_id,
                        "reason": "Missing provider/auth/settings fields.",
                    }
                )
                continue

            kind = str(auth.get("kind", "")).strip()
            overlay_update = dict(settings)
            skip_reason = ""
            if kind == "api_key":
                api_key = str(auth.get("api_key", "")).strip()
                if not api_key:
                    skipped.append({"provider": provider_id, "reason": "Missing api_key."})
                    continue
                if is_rejected_placeholder_api_key(api_key):
                    overlay_update["api_key"] = ""
                    skip_reason = "Rejected placeholder API key."
                else:
                    overlay_update["api_key"] = api_key
            elif kind == "oauth_token":
                raw_token = auth.get("token")
                if not isinstance(raw_token, dict):
                    skipped.append(
                        {"provider": provider_id, "reason": "Missing OAuth token payload."}
                    )
                    continue
                token = OAuthToken(**raw_token)
                token.provider = provider_id
                token_store.save(token)
                overlay_update["api_key"] = ""
            else:
                skipped.append(
                    {
                        "provider": provider_id,
                        "reason": f"Unsupported auth kind: {kind or '(empty)'}",
                    }
                )
                continue

            if overlay_update:
                upsert_provider_overlay(
                    state.provider_overlay,
                    provider_id,
                    overlay_update,
                )
                _refresh_provider_config_from_sources(state, provider_id)
                overlay_changed = True
            if skip_reason:
                skipped.append({"provider": provider_id, "reason": skip_reason})
                continue
            imported.append({"provider": provider_id, "auth_kind": kind})

        if overlay_changed:
            save_provider_overlay(state.provider_overlay)
        return web.json_response({"imported": imported, "skipped": skipped})

    async def handle_oauth_start(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        provider = str(payload.get("provider", "")).strip()
        redirect_uri = str(payload.get("redirect_uri", "")).strip()
        if not provider:
            return web.json_response({"error": "Missing provider"}, status=400)
        try:
            challenge, descriptor = start_remote_oauth_challenge(
                provider,
                redirect_uri=redirect_uri,
            )
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

        login_id = secrets.token_hex(16)
        state.pending_oauth[login_id] = challenge
        descriptor["login_id"] = login_id
        return web.json_response(descriptor)

    async def handle_oauth_complete(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        login_id = str(payload.get("login_id", "")).strip()
        oauth_payload = payload.get("payload", {})
        if not login_id or not isinstance(oauth_payload, dict):
            return web.json_response({"error": "Missing login payload"}, status=400)

        challenge = state.pending_oauth.pop(login_id, None)
        if challenge is None:
            return web.json_response({"error": "Login session not found"}, status=404)

        try:
            token_value = await complete_remote_oauth_challenge(challenge, oauth_payload)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)

        TokenStore().save(token_value)
        return web.json_response(
            {
                "provider": challenge.provider,
                "auth_kind": "oauth_token",
                "status": "ok",
            }
        )

    async def handle_schedules_list(request: web.Request) -> web.Response:
        if state.schedule_service is None:
            return web.json_response({"enabled": False, "schedules": []})
        return web.json_response(state.schedule_service.snapshot())

    async def handle_schedules_post(request: web.Request) -> web.Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        try:
            schedule = add_schedule(
                scope=str(payload.get("scope", "project")),
                schedule_id=str(payload.get("id", "")),
                project_dir=state.default_project_dir,
                enabled=bool(payload.get("enabled", True)),
                kind=str(payload.get("kind", "interval")),
                every_seconds=int(payload.get("every_seconds", 0) or 0),
                cron=str(payload.get("cron", "") or ""),
                timezone=str(payload.get("timezone", "UTC") or "UTC"),
                prompt=str(payload.get("prompt", "") or ""),
                prompt_name=str(payload.get("prompt_name", "") or ""),
                arg=str(payload.get("arg", "") or ""),
                model=str(payload.get("model", "") or ""),
                session_mode=str(payload.get("session_mode", "reuse") or "reuse"),
                execution_mode=str(payload.get("execution_mode", "readonly") or "readonly"),
                overlap_policy=str(payload.get("overlap_policy", "skip") or "skip"),
                max_runtime_seconds=int(payload.get("max_runtime_seconds", 0) or 0),
                run_missed=str(payload.get("run_missed", "none") or "none"),
                target_project_dir=str(payload.get("project_dir", "") or ""),
            )
            if state.schedule_service is not None:
                await state.schedule_service.reload()
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(
            {
                "schedule": serialize_schedule(schedule),
                "snapshot": state.schedule_service.snapshot()
                if state.schedule_service is not None
                else {},
            }
        )

    async def handle_schedule_put(request: web.Request) -> web.Response:
        schedule_id = request.match_info["schedule_id"]
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
        try:
            schedule = update_schedule(
                schedule_id,
                project_dir=state.default_project_dir,
                scope=str(payload["scope"])
                if "scope" in payload and payload.get("scope") is not None
                else None,
                enabled=bool(payload.get("enabled")) if "enabled" in payload else None,
                kind=str(payload["kind"])
                if "kind" in payload and payload.get("kind") is not None
                else None,
                every_seconds=int(payload["every_seconds"])
                if "every_seconds" in payload and payload.get("every_seconds") is not None
                else None,
                cron=str(payload["cron"])
                if "cron" in payload and payload.get("cron") is not None
                else None,
                timezone=str(payload["timezone"])
                if "timezone" in payload and payload.get("timezone") is not None
                else None,
                prompt=str(payload["prompt"])
                if "prompt" in payload and payload.get("prompt") is not None
                else None,
                prompt_name=str(payload["prompt_name"])
                if "prompt_name" in payload and payload.get("prompt_name") is not None
                else None,
                arg=str(payload["arg"])
                if "arg" in payload and payload.get("arg") is not None
                else None,
                model=str(payload["model"])
                if "model" in payload and payload.get("model") is not None
                else None,
                session_mode=str(payload["session_mode"])
                if "session_mode" in payload and payload.get("session_mode") is not None
                else None,
                execution_mode=str(payload["execution_mode"])
                if "execution_mode" in payload and payload.get("execution_mode") is not None
                else None,
                overlap_policy=str(payload["overlap_policy"])
                if "overlap_policy" in payload and payload.get("overlap_policy") is not None
                else None,
                max_runtime_seconds=int(payload["max_runtime_seconds"])
                if "max_runtime_seconds" in payload
                and payload.get("max_runtime_seconds") is not None
                else None,
                run_missed=str(payload["run_missed"])
                if "run_missed" in payload and payload.get("run_missed") is not None
                else None,
                target_project_dir=str(payload["project_dir"])
                if "project_dir" in payload and payload.get("project_dir") is not None
                else None,
            )
            if state.schedule_service is not None:
                await state.schedule_service.reload()
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(
            {
                "schedule": serialize_schedule(schedule),
                "snapshot": state.schedule_service.snapshot()
                if state.schedule_service is not None
                else {},
            }
        )

    async def handle_schedule_delete(request: web.Request) -> web.Response:
        schedule_id = request.match_info["schedule_id"]
        schedule = delete_schedule(schedule_id, state.default_project_dir)
        if schedule is None:
            return web.json_response({"error": "Schedule not found"}, status=404)
        if state.schedule_service is not None:
            await state.schedule_service.reload()
        return web.json_response(
            {
                "schedule": serialize_schedule(schedule),
                "snapshot": state.schedule_service.snapshot()
                if state.schedule_service is not None
                else {},
            }
        )

    async def handle_schedule_run(request: web.Request) -> web.Response:
        schedule_id = request.match_info["schedule_id"]
        if state.schedule_service is None:
            return web.json_response({"error": "Scheduler is disabled"}, status=400)
        try:
            snapshot = await state.schedule_service.run_now(schedule_id)
        except Exception as exc:
            status = 404 if "not found" in str(exc).lower() else 400
            return web.json_response({"error": str(exc)}, status=status)
        return web.json_response(snapshot)

    async def handle_schedules_reload(request: web.Request) -> web.Response:
        if state.schedule_service is None:
            return web.json_response({"enabled": False, "schedules": []})
        snapshot = await state.schedule_service.reload()
        return web.json_response(snapshot)

    async def handle_session_delete(request: web.Request) -> web.Response:
        sid = request.match_info["session_id"]
        deleted = False
        controller = state.session_controllers.pop(sid, None)
        if controller is not None:
            with suppress(Exception):
                await controller.abort()
        state.auto_approve_sessions.discard(sid)
        state.permission_callbacks.pop(sid, None)
        if sid in state.sessions:
            session = state.sessions.pop(sid)
            state.session_provider_models.pop(sid, None)
            state.session_projects.pop(sid, None)
            state.session_thinking_levels.pop(sid, None)
            with suppress(Exception):
                await session.provider.close()
            mcp_runtime = getattr(session, "mcp_runtime", None)
            if mcp_runtime is not None:
                with suppress(Exception):
                    await mcp_runtime.close()
            lsp_runtime = getattr(session, "lsp_runtime", None)
            if lsp_runtime is not None:
                with suppress(Exception):
                    await lsp_runtime.close()
            deleted = True
        else:
            state.session_provider_models.pop(sid, None)
            state.session_projects.pop(sid, None)
            state.session_thinking_levels.pop(sid, None)
        if state.store is not None and await state.store.get_session(sid) is not None:
            await state.store.delete_session(sid)
            deleted = True
        if deleted:
            return web.json_response({"deleted": sid})
        return web.json_response({"error": "Session not found"}, status=404)

    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/server/info", handle_server_info)
    app.router.add_get("/api/server/diagnostics", handle_server_diagnostics)
    app.router.add_get("/api/config/paths", handle_config_paths)
    app.router.add_get("/api/config/effective", handle_config_effective)
    app.router.add_get("/api/config/raw", handle_config_raw)
    app.router.add_post("/api/config/init", handle_config_init)
    app.router.add_get("/api/providers", handle_providers)
    app.router.add_get("/api/models", handle_models)
    app.router.add_get("/api/prompts", handle_prompts_list)
    app.router.add_post("/api/prompts/{prompt_name}/render", handle_prompt_render)
    app.router.add_get("/api/skills", handle_skills_list)
    app.router.add_get("/api/rules", handle_rules_list)
    app.router.add_post("/api/rules", handle_rules_post)
    app.router.add_put("/api/rules/{rule_id}", handle_rule_put)
    app.router.add_post("/api/rules/{rule_id}/move", handle_rule_move)
    app.router.add_delete("/api/rules/{rule_id}", handle_rule_delete)
    app.router.add_get("/api/extensions", handle_extensions_list)
    app.router.add_post("/api/extensions/install", handle_extension_install)
    app.router.add_delete("/api/extensions/{name}", handle_extension_remove)
    app.router.add_post("/api/extensions/update", handle_extension_update)
    app.router.add_post("/api/extensions/{name}/update", handle_extension_update)
    app.router.add_get("/api/extensions/search", handle_extension_search)
    app.router.add_get("/api/extensions/registries", handle_extension_registries_list)
    app.router.add_post("/api/extensions/registries", handle_extension_registries_add)
    app.router.add_delete("/api/extensions/registries/{name}", handle_extension_registries_remove)
    app.router.add_get("/api/mcp", handle_mcp_get)
    app.router.add_post("/api/mcp/reload", handle_mcp_reload)
    app.router.add_get("/api/mcp/config", handle_mcp_config_get)
    app.router.add_put("/api/mcp/config", handle_mcp_config_put)
    app.router.add_put("/api/mcp/servers/{name}", handle_mcp_server_put)
    app.router.add_delete("/api/mcp/servers/{name}", handle_mcp_server_delete)
    app.router.add_post("/api/credentials/import", handle_credentials_import)
    app.router.add_post("/api/oauth/start", handle_oauth_start)
    app.router.add_post("/api/oauth/complete", handle_oauth_complete)
    app.router.add_get("/api/schedules", handle_schedules_list)
    app.router.add_post("/api/schedules", handle_schedules_post)
    app.router.add_put("/api/schedules/{schedule_id}", handle_schedule_put)
    app.router.add_delete("/api/schedules/{schedule_id}", handle_schedule_delete)
    app.router.add_post("/api/schedules/{schedule_id}/run", handle_schedule_run)
    app.router.add_post("/api/schedules/reload", handle_schedules_reload)
    app.router.add_get("/api/sessions", handle_sessions_list)
    app.router.add_get("/api/sessions/{session_id}", handle_session_get)
    app.router.add_get("/api/sessions/{session_id}/messages", handle_session_messages_get)
    app.router.add_get("/api/sessions/{session_id}/tree", handle_session_tree_get)
    app.router.add_get("/api/sessions/{session_id}/delegates", handle_delegates_list)
    app.router.add_get("/api/sessions/{session_id}/delegates/{run_id}", handle_delegate_get)
    app.router.add_post(
        "/api/sessions/{session_id}/delegates/{run_id}/cancel", handle_delegate_cancel
    )
    app.router.add_get("/api/sessions/{session_id}/commands", handle_session_commands_get)
    app.router.add_post(
        "/api/sessions/{session_id}/commands/{command_name}",
        handle_session_command_post,
    )
    app.router.add_put("/api/sessions/{session_id}/model", handle_session_model_put)
    app.router.add_put("/api/sessions/{session_id}/title", handle_session_title_put)
    app.router.add_put("/api/sessions/{session_id}/project", handle_session_project_put)
    app.router.add_put("/api/sessions/{session_id}/thinking", handle_session_thinking_put)
    app.router.add_get("/api/sessions/{session_id}/rules", handle_session_rules_get)
    app.router.add_put("/api/sessions/{session_id}/rules/{rule_id}", handle_session_rule_put)
    app.router.add_get("/api/sessions/{session_id}/tasks", handle_session_tasks_get)
    app.router.add_put("/api/sessions/{session_id}/tasks", handle_session_tasks_put)
    app.router.add_get("/api/sessions/{session_id}/notes", handle_session_notes_get)
    app.router.add_put("/api/sessions/{session_id}/notes", handle_session_notes_put)
    app.router.add_post("/api/sessions/{session_id}/compact", handle_session_compact_post)
    app.router.add_post("/api/sessions/{session_id}/fork", handle_session_fork_post)
    app.router.add_post("/api/sessions/{session_id}/skill", handle_session_skill_post)
    app.router.add_post("/api/sessions/{session_id}/reload", handle_session_reload_post)
    app.router.add_post("/api/sessions/{session_id}/bash", handle_session_bash)
    app.router.add_post("/api/sessions/{session_id}/wt", handle_session_wt)
    app.router.add_delete("/api/sessions/{session_id}", handle_session_delete)
    for ext in state.server_extensions:
        with suppress(Exception):
            ext.configure_rest_app(app)
    return app


# ── Server entrypoint ─────────────────────────────────────────────


async def run_server(
    host: str | None = None,
    port: int | None = None,
    auth_token: str = "",
    announce: Callable[[str], None] | None = None,
) -> None:
    """Start WebSocket + REST server.

    *host* and *port* override ``config.server.host`` / ``config.server.port``.
    If not provided, values from the loaded config are used.
    """
    from aiohttp import web

    project_dir = os.getcwd()
    config = load_config(project_dir)
    provider_overlay = load_provider_overlay()
    merge_provider_overlay(config, provider_overlay)
    context = ExtensionContext(project_dir=project_dir, runtime="server", config=config)
    server_extensions = await load_server_extensions_async(context=context)
    mcp_runtime = McpRuntimeManager()
    await mcp_runtime.load(context)
    store = SessionStore(config.sessions.db_path)
    await store.open()
    state = ServerState(
        config=config,
        default_project_dir=project_dir,
        provider_overlay=provider_overlay,
        server_extensions=server_extensions,
        store=store,
        mcp_runtime=mcp_runtime,
    )
    if config.server.scheduler_enabled:
        state.schedule_service = ScheduleService(state)
        await state.schedule_service.start()

    # Use explicit args → config → defaults
    host = host or config.server.host
    port = port if port is not None else config.server.port

    token = auth_token or config.server.auth_token
    generated_token_path = None
    if not token:
        token = f"artel_{secrets.token_hex(16)}"
        generated_token_path = persist_server_auth_token(token, project_dir=project_dir)
        config.server.auth_token = token
        logger.info("Generated auth token: %s", token)
        logger.info("Saved generated auth token to %s", generated_token_path)

    # Bearer auth for WebSocket
    async def ws_handler(ws: ServerConnection) -> None:
        # Check origin header for bearer token
        req_headers = ws.request.headers if ws.request else {}  # type: ignore[union-attr]
        auth_header = ""
        if hasattr(req_headers, "get"):
            auth_header = req_headers.get("Authorization", "")  # type: ignore[arg-type]
        if token and auth_header != f"Bearer {token}":
            await ws.close(4001, "Unauthorized")
            return
        await handle_client(ws, state)

    # Start REST API on port+1
    rest_port = port + 1
    rest_app = _create_rest_app(state, token)
    rest_runner = web.AppRunner(rest_app)
    await rest_runner.setup()
    rest_site = web.TCPSite(rest_runner, host, rest_port)
    await rest_site.start()

    logger.info("Artel server starting")
    logger.info("  WebSocket: ws://%s:%d", host, port)
    logger.info("  REST API:  http://%s:%d/api/", host, rest_port)
    logger.info("  Auth token: %s", token)
    logger.info("  Scheduler: %s", "enabled" if state.schedule_service is not None else "disabled")
    if announce is not None:
        announce("Artel server starting")
        announce(f"  WebSocket: ws://{host}:{port}")
        announce(f"  REST API:  http://{host}:{rest_port}/api/")
        announce(f"  Auth token: {token}")
        announce(f"  Scheduler: {'enabled' if state.schedule_service is not None else 'disabled'}")
        if generated_token_path is not None:
            announce(f"  Saved auth token to: {generated_token_path}")

    try:
        async with websockets.serve(ws_handler, host, port):  # type: ignore[attr-defined]
            await asyncio.Future()  # Run forever
    finally:
        if state.schedule_service is not None:
            with suppress(Exception):
                await state.schedule_service.stop()
        for session in list(state.sessions.values()):
            with suppress(Exception):
                await session.provider.close()
            mcp_runtime = getattr(session, "mcp_runtime", None)
            if mcp_runtime is not None:
                with suppress(Exception):
                    await mcp_runtime.close()
            lsp_runtime = getattr(session, "lsp_runtime", None)
            if lsp_runtime is not None:
                with suppress(Exception):
                    await lsp_runtime.close()
        if state.mcp_runtime is not None:
            with suppress(Exception):
                await state.mcp_runtime.close()
        await rest_runner.cleanup()
        await store.close()
