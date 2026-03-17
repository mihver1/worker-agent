"""Scheduled task storage and cron/interval helpers for Artel."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import artel_core.config as config_mod

ScheduleScope = Literal["global", "project"]
ScheduleKind = Literal["interval", "cron"]
ScheduleExecutionMode = Literal["readonly", "inherit"]
ScheduleSessionMode = Literal["reuse", "new"]
ScheduleOverlapPolicy = Literal["skip", "allow", "cancel_previous"]
ScheduleRunMissedPolicy = Literal["none", "latest", "all"]
ScheduleRunStatus = Literal["idle", "running", "succeeded", "failed", "skipped", "cancelled"]

_MONTH_NAMES = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_WEEKDAY_NAMES = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


@dataclass(slots=True)
class ScheduleRecord:
    id: str
    scope: ScheduleScope
    enabled: bool = True
    kind: ScheduleKind = "interval"
    every_seconds: int = 0
    cron: str = ""
    timezone: str = "UTC"
    prompt: str = ""
    prompt_name: str = ""
    arg: str = ""
    project_dir: str = ""
    model: str = ""
    session_mode: ScheduleSessionMode = "reuse"
    execution_mode: ScheduleExecutionMode = "readonly"
    overlap_policy: ScheduleOverlapPolicy = "skip"
    max_runtime_seconds: int = 0
    run_missed: ScheduleRunMissedPolicy = "none"
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class LoadedSchedules:
    schedules: dict[str, ScheduleRecord]
    sources: list[Path]


@dataclass(slots=True)
class ScheduleStateRecord:
    schedule_id: str
    last_run_at: str = ""
    next_run_at: str = ""
    last_status: ScheduleRunStatus = "idle"
    last_error: str = ""
    last_result_preview: str = ""
    last_session_id: str = ""
    last_run_id: str = ""
    running_run_ids: list[str] = field(default_factory=list)
    total_runs: int = 0
    total_successes: int = 0
    total_failures: int = 0
    total_skips: int = 0
    updated_at: str = ""


@dataclass(slots=True)
class ScheduleRunRecord:
    id: str
    schedule_id: str
    trigger: str
    status: ScheduleRunStatus
    started_at: str = ""
    scheduled_for: str = ""
    finished_at: str = ""
    session_id: str = ""
    error: str = ""
    result_preview: str = ""


class ScheduleRegistry:
    """Read/write Artel scheduled-task config across global and project scopes."""

    def load_global_config(self) -> list[ScheduleRecord]:
        return _load_schedule_file(global_schedules_path(), scope="global")

    def load_project_config(self, project_dir: str) -> list[ScheduleRecord]:
        return _load_schedule_file(project_schedules_path(project_dir), scope="project")

    def load_merged_config(self, project_dir: str) -> LoadedSchedules:
        merged: dict[str, ScheduleRecord] = {}
        sources: list[Path] = []

        global_path = global_schedules_path()
        if global_path.exists():
            sources.append(global_path)
            for record in _load_schedule_file(global_path, scope="global"):
                merged[record.id] = record

        if project_dir:
            project_path = project_schedules_path(project_dir)
            if project_path.exists():
                sources.append(project_path)
                for record in _load_schedule_file(project_path, scope="project"):
                    merged[record.id] = record

        return LoadedSchedules(schedules=merged, sources=sources)

    def write_global_config(self, schedules: list[ScheduleRecord]) -> Path:
        target = global_schedules_path()
        _write_schedule_file(target, schedules)
        return target

    def write_project_config(self, project_dir: str, schedules: list[ScheduleRecord]) -> Path:
        target = project_schedules_path(project_dir)
        _write_schedule_file(target, schedules)
        return target


def global_schedules_path() -> Path:
    return config_mod.CONFIG_DIR / "schedules.json"


def project_schedules_path(project_dir: str) -> Path:
    return config_mod.project_state_dir(project_dir) / "schedules.json"


def load_schedules(project_dir: str = "") -> list[ScheduleRecord]:
    registry = ScheduleRegistry()
    loaded = registry.load_merged_config(project_dir)
    return sorted(loaded.schedules.values(), key=lambda item: (item.scope, item.id))


def get_schedule(schedule_id: str, project_dir: str = "") -> ScheduleRecord | None:
    registry = ScheduleRegistry()
    loaded = registry.load_merged_config(project_dir)
    return loaded.schedules.get(schedule_id.strip())


def add_schedule(
    *,
    scope: str,
    schedule_id: str,
    project_dir: str = "",
    enabled: bool = True,
    kind: str = "interval",
    every_seconds: int = 0,
    cron: str = "",
    timezone: str = "UTC",
    prompt: str = "",
    prompt_name: str = "",
    arg: str = "",
    model: str = "",
    session_mode: str = "reuse",
    execution_mode: str = "readonly",
    overlap_policy: str = "skip",
    max_runtime_seconds: int = 0,
    run_missed: str = "none",
    target_project_dir: str = "",
) -> ScheduleRecord:
    normalized_scope = _normalize_scope(scope)
    normalized_id = schedule_id.strip()
    if not normalized_id:
        raise ValueError("Schedule id cannot be empty")
    path = (
        global_schedules_path()
        if normalized_scope == "global"
        else project_schedules_path(project_dir)
    )
    if normalized_scope == "project" and not project_dir:
        raise ValueError("project_dir is required for project-scoped schedules")
    records = _load_schedule_file(path, scope=normalized_scope)
    if any(record.id == normalized_id for record in records):
        raise ValueError(f"Schedule '{normalized_id}' already exists")
    now = _now()
    record = _validated_record(
        ScheduleRecord(
            id=normalized_id,
            scope=normalized_scope,
            enabled=enabled,
            kind=_normalize_kind(kind),
            every_seconds=every_seconds,
            cron=cron.strip(),
            timezone=timezone.strip() or "UTC",
            prompt=prompt,
            prompt_name=prompt_name.strip(),
            arg=arg,
            project_dir=target_project_dir.strip(),
            model=model.strip(),
            session_mode=_normalize_session_mode(session_mode),
            execution_mode=_normalize_execution_mode(execution_mode),
            overlap_policy=_normalize_overlap_policy(overlap_policy),
            max_runtime_seconds=max_runtime_seconds,
            run_missed=_normalize_run_missed_policy(run_missed),
            created_at=now,
            updated_at=now,
        )
    )
    records.append(record)
    _write_schedule_file(path, records)
    return record


def update_schedule(
    schedule_id: str,
    *,
    project_dir: str = "",
    scope: str | None = None,
    enabled: bool | None = None,
    kind: str | None = None,
    every_seconds: int | None = None,
    cron: str | None = None,
    timezone: str | None = None,
    prompt: str | None = None,
    prompt_name: str | None = None,
    arg: str | None = None,
    model: str | None = None,
    session_mode: str | None = None,
    execution_mode: str | None = None,
    overlap_policy: str | None = None,
    max_runtime_seconds: int | None = None,
    run_missed: str | None = None,
    target_project_dir: str | None = None,
) -> ScheduleRecord:
    needle = schedule_id.strip()
    if not needle:
        raise ValueError("Missing schedule id")
    current = get_schedule(needle, project_dir)
    if current is None:
        raise ValueError(f"Schedule '{needle}' not found")

    target_scope = _normalize_scope(scope) if scope is not None else current.scope
    source_path = (
        global_schedules_path()
        if current.scope == "global"
        else project_schedules_path(project_dir)
    )
    target_path = (
        global_schedules_path() if target_scope == "global" else project_schedules_path(project_dir)
    )
    if target_scope == "project" and not project_dir:
        raise ValueError("project_dir is required for project-scoped schedules")

    source_records = [
        record
        for record in _load_schedule_file(source_path, scope=current.scope)
        if record.id != needle
    ]
    if source_path != target_path:
        _write_schedule_file(source_path, source_records)
        target_records = _load_schedule_file(target_path, scope=target_scope)
    else:
        target_records = source_records

    updated = _validated_record(
        ScheduleRecord(
            id=current.id,
            scope=target_scope,
            enabled=current.enabled if enabled is None else enabled,
            kind=current.kind if kind is None else _normalize_kind(kind),
            every_seconds=current.every_seconds if every_seconds is None else every_seconds,
            cron=current.cron if cron is None else cron.strip(),
            timezone=current.timezone if timezone is None else (timezone.strip() or "UTC"),
            prompt=current.prompt if prompt is None else prompt,
            prompt_name=current.prompt_name if prompt_name is None else prompt_name.strip(),
            arg=current.arg if arg is None else arg,
            project_dir=current.project_dir
            if target_project_dir is None
            else target_project_dir.strip(),
            model=current.model if model is None else model.strip(),
            session_mode=current.session_mode
            if session_mode is None
            else _normalize_session_mode(session_mode),
            execution_mode=current.execution_mode
            if execution_mode is None
            else _normalize_execution_mode(execution_mode),
            overlap_policy=current.overlap_policy
            if overlap_policy is None
            else _normalize_overlap_policy(overlap_policy),
            max_runtime_seconds=(
                current.max_runtime_seconds if max_runtime_seconds is None else max_runtime_seconds
            ),
            run_missed=current.run_missed
            if run_missed is None
            else _normalize_run_missed_policy(run_missed),
            created_at=current.created_at,
            updated_at=_now(),
        )
    )
    target_records = [record for record in target_records if record.id != needle]
    target_records.append(updated)
    _write_schedule_file(target_path, target_records)
    return updated


def delete_schedule(schedule_id: str, project_dir: str = "") -> ScheduleRecord | None:
    needle = schedule_id.strip()
    if not needle:
        return None
    current = get_schedule(needle, project_dir)
    if current is None:
        return None
    path = (
        global_schedules_path()
        if current.scope == "global"
        else project_schedules_path(project_dir)
    )
    kept = [
        record for record in _load_schedule_file(path, scope=current.scope) if record.id != needle
    ]
    _write_schedule_file(path, kept)
    return current


def serialize_schedule(record: ScheduleRecord) -> dict[str, Any]:
    return asdict(record)


def serialize_schedule_state(record: ScheduleStateRecord) -> dict[str, Any]:
    return asdict(record)


def serialize_schedule_run(record: ScheduleRunRecord) -> dict[str, Any]:
    return asdict(record)


def timezone_for_schedule(record: ScheduleRecord) -> ZoneInfo:
    name = record.timezone.strip() or "UTC"
    try:
        return ZoneInfo(name)
    except Exception as exc:  # pragma: no cover - guarded by validation
        raise ValueError(f"Unknown timezone: {name}") from exc


def next_schedule_time(record: ScheduleRecord, after: datetime) -> datetime | None:
    if record.kind == "interval":
        if record.every_seconds <= 0:
            return None
        return after + timedelta(seconds=record.every_seconds)
    return _next_cron_time(record, after)


def render_prompt_variables(arg: str) -> dict[str, str]:
    text = arg.strip()
    if not text:
        return {}
    if "=" not in text:
        return {"input": text}
    variables: dict[str, str] = {}
    for part in text.split():
        if "=" in part:
            key, _, value = part.partition("=")
            variables[key] = value
        else:
            variables.setdefault("input", "")
            variables["input"] += (" " if variables.get("input") else "") + part
    return variables


def _normalize_scope(scope: str) -> ScheduleScope:
    lowered = scope.strip().lower()
    if lowered not in {"global", "project"}:
        raise ValueError("scope must be 'global' or 'project'")
    return lowered  # type: ignore[return-value]


def _normalize_kind(kind: str) -> ScheduleKind:
    lowered = kind.strip().lower()
    if lowered not in {"interval", "cron"}:
        raise ValueError("kind must be one of: interval, cron")
    return lowered  # type: ignore[return-value]


def _normalize_execution_mode(value: str) -> ScheduleExecutionMode:
    lowered = value.strip().lower() or "readonly"
    if lowered not in {"readonly", "inherit"}:
        raise ValueError("execution_mode must be one of: readonly, inherit")
    return lowered  # type: ignore[return-value]


def _normalize_session_mode(value: str) -> ScheduleSessionMode:
    lowered = value.strip().lower() or "reuse"
    if lowered not in {"reuse", "new"}:
        raise ValueError("session_mode must be one of: reuse, new")
    return lowered  # type: ignore[return-value]


def _normalize_overlap_policy(value: str) -> ScheduleOverlapPolicy:
    lowered = value.strip().lower() or "skip"
    if lowered not in {"skip", "allow", "cancel_previous"}:
        raise ValueError("overlap_policy must be one of: skip, allow, cancel_previous")
    return lowered  # type: ignore[return-value]


def _normalize_run_missed_policy(value: str) -> ScheduleRunMissedPolicy:
    lowered = value.strip().lower() or "none"
    if lowered not in {"none", "latest", "all"}:
        raise ValueError("run_missed must be one of: none, latest, all")
    return lowered  # type: ignore[return-value]


def _validated_record(record: ScheduleRecord) -> ScheduleRecord:
    if not record.id.strip():
        raise ValueError("Schedule id cannot be empty")
    if bool(record.prompt.strip()) == bool(record.prompt_name.strip()):
        raise ValueError("Provide exactly one of prompt or prompt_name")
    if record.kind == "interval":
        if record.every_seconds <= 0:
            raise ValueError("every_seconds must be > 0 for interval schedules")
        record.cron = ""
    else:
        if not record.cron.strip():
            raise ValueError("cron expression is required for cron schedules")
        _parse_cron_expression(record.cron)
        record.every_seconds = 0
    if record.max_runtime_seconds < 0:
        raise ValueError("max_runtime_seconds cannot be negative")
    timezone_for_schedule(record)
    return record


def _load_schedule_file(path: Path, *, scope: ScheduleScope) -> list[ScheduleRecord]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw_items = payload.get("schedules", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        return []
    records: list[ScheduleRecord] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        try:
            record = _validated_record(
                ScheduleRecord(
                    id=str(item.get("id", "")).strip(),
                    scope=_normalize_scope(str(item.get("scope", scope))),
                    enabled=bool(item.get("enabled", True)),
                    kind=_normalize_kind(str(item.get("kind", "interval") or "interval")),
                    every_seconds=int(item.get("every_seconds", 0) or 0),
                    cron=str(item.get("cron", "") or "").strip(),
                    timezone=str(item.get("timezone", "UTC") or "UTC").strip(),
                    prompt=str(item.get("prompt", "") or ""),
                    prompt_name=str(item.get("prompt_name", "") or "").strip(),
                    arg=str(item.get("arg", "") or ""),
                    project_dir=str(item.get("project_dir", "") or "").strip(),
                    model=str(item.get("model", "") or "").strip(),
                    session_mode=_normalize_session_mode(
                        str(item.get("session_mode", "reuse") or "reuse")
                    ),
                    execution_mode=_normalize_execution_mode(
                        str(item.get("execution_mode", "readonly") or "readonly")
                    ),
                    overlap_policy=_normalize_overlap_policy(
                        str(item.get("overlap_policy", "skip") or "skip")
                    ),
                    max_runtime_seconds=int(item.get("max_runtime_seconds", 0) or 0),
                    run_missed=_normalize_run_missed_policy(
                        str(item.get("run_missed", "none") or "none")
                    ),
                    created_at=str(item.get("created_at", "") or ""),
                    updated_at=str(item.get("updated_at", "") or ""),
                )
            )
        except Exception:
            continue
        records.append(record)
    records.sort(key=lambda item: item.id)
    return records


def _write_schedule_file(path: Path, schedules: list[ScheduleRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schedules": [asdict(record) for record in sorted(schedules, key=lambda item: item.id)]
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def global_schedule_state_path() -> Path:
    return config_mod.CONFIG_DIR / "schedules-state.json"


def project_schedule_state_path(project_dir: str) -> Path:
    return config_mod.project_state_dir(project_dir) / "schedules-state.json"


def load_schedule_states(project_dir: str = "") -> dict[str, ScheduleStateRecord]:
    merged: dict[str, ScheduleStateRecord] = {}
    for path in (
        global_schedule_state_path(),
        project_schedule_state_path(project_dir) if project_dir else None,
    ):
        if path is None or not path.exists():
            continue
        for key, value in _load_schedule_state_file(path).items():
            merged[key] = value
    return merged


def load_schedule_states_for_scope(
    project_dir: str, *, scope: ScheduleScope
) -> dict[str, ScheduleStateRecord]:
    path = (
        global_schedule_state_path()
        if scope == "global"
        else project_schedule_state_path(project_dir)
    )
    return _load_schedule_state_file(path)


def write_schedule_states(
    project_dir: str, states: dict[str, ScheduleStateRecord], *, scope: ScheduleScope = "project"
) -> Path:
    path = (
        global_schedule_state_path()
        if scope == "global"
        else project_schedule_state_path(project_dir)
    )
    _write_schedule_state_file(path, states)
    return path


def _load_schedule_state_file(path: Path) -> dict[str, ScheduleStateRecord]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw_items = payload.get("schedules", {}) if isinstance(payload, dict) else {}
    if not isinstance(raw_items, dict):
        return {}
    result: dict[str, ScheduleStateRecord] = {}
    for schedule_id, item in raw_items.items():
        if not isinstance(item, dict):
            continue
        result[str(schedule_id)] = ScheduleStateRecord(
            schedule_id=str(item.get("schedule_id", schedule_id) or schedule_id),
            last_run_at=str(item.get("last_run_at", "") or ""),
            next_run_at=str(item.get("next_run_at", "") or ""),
            last_status=str(item.get("last_status", "idle") or "idle"),
            last_error=str(item.get("last_error", "") or ""),
            last_result_preview=str(item.get("last_result_preview", "") or ""),
            last_session_id=str(item.get("last_session_id", "") or ""),
            last_run_id=str(item.get("last_run_id", "") or ""),
            running_run_ids=[str(value) for value in item.get("running_run_ids", [])]
            if isinstance(item.get("running_run_ids"), list)
            else [],
            total_runs=int(item.get("total_runs", 0) or 0),
            total_successes=int(item.get("total_successes", 0) or 0),
            total_failures=int(item.get("total_failures", 0) or 0),
            total_skips=int(item.get("total_skips", 0) or 0),
            updated_at=str(item.get("updated_at", "") or ""),
        )
    return result


def _write_schedule_state_file(path: Path, states: dict[str, ScheduleStateRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schedules": {key: asdict(value) for key, value in sorted(states.items())}}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_timestamp(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _parse_cron_expression(
    expr: str,
) -> tuple[set[int], set[int], set[int], set[int], set[int], bool, bool]:
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError("cron must have 5 fields: minute hour day month weekday")
    minute_raw, hour_raw, day_raw, month_raw, weekday_raw = parts
    minutes = _parse_field(minute_raw, 0, 59)
    hours = _parse_field(hour_raw, 0, 23)
    days = _parse_field(day_raw, 1, 31)
    months = _parse_field(month_raw, 1, 12, names=_MONTH_NAMES)
    weekdays = _parse_field(weekday_raw, 0, 7, names=_WEEKDAY_NAMES, weekday_field=True)
    return minutes, hours, days, months, weekdays, day_raw == "*", weekday_raw == "*"


def _parse_field(
    raw: str,
    minimum: int,
    maximum: int,
    *,
    names: dict[str, int] | None = None,
    weekday_field: bool = False,
) -> set[int]:
    values: set[int] = set()
    for part in raw.split(","):
        token = part.strip().lower()
        if not token:
            raise ValueError("Invalid empty cron field component")
        if "/" in token:
            base, _, step_raw = token.partition("/")
            try:
                step = int(step_raw)
            except ValueError as exc:
                raise ValueError(f"Invalid cron step: {step_raw}") from exc
            if step <= 0:
                raise ValueError("cron step must be positive")
        else:
            base = token
            step = 1

        if base == "*":
            start = minimum
            end = maximum
        elif "-" in base:
            start_raw, _, end_raw = base.partition("-")
            start = _parse_field_value(start_raw, names=names)
            end = _parse_field_value(end_raw, names=names)
            if start > end:
                raise ValueError(f"Invalid cron range: {base}")
        else:
            start = end = _parse_field_value(base, names=names)

        if start < minimum or end > maximum:
            raise ValueError(f"Cron field out of range: {raw}")
        for value in range(start, end + 1, step):
            normalized = 0 if weekday_field and value == 7 else value
            values.add(normalized)
    return values


def _parse_field_value(raw: str, *, names: dict[str, int] | None = None) -> int:
    token = raw.strip().lower()
    if names and token in names:
        return names[token]
    return int(token)


def _next_cron_time(record: ScheduleRecord, after: datetime) -> datetime | None:
    tz = timezone_for_schedule(record)
    minutes, hours, days, months, weekdays, day_any, weekday_any = _parse_cron_expression(
        record.cron
    )
    candidate = after.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = candidate + timedelta(days=366 * 5)
    while candidate <= limit:
        if candidate.month not in months:
            candidate = (candidate.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(
                day=1
            )
            continue
        if candidate.hour not in hours:
            candidate += timedelta(hours=1)
            candidate = candidate.replace(minute=0)
            continue
        if candidate.minute not in minutes:
            candidate += timedelta(minutes=1)
            continue
        day_match = candidate.day in days
        cron_weekday = (candidate.weekday() + 1) % 7
        weekday_match = cron_weekday in weekdays
        if _cron_day_matches(day_match, weekday_match, day_any=day_any, weekday_any=weekday_any):
            return candidate.astimezone(UTC)
        candidate += timedelta(minutes=1)
    return None


def _cron_day_matches(
    day_match: bool, weekday_match: bool, *, day_any: bool, weekday_any: bool
) -> bool:
    if day_any and weekday_any:
        return True
    if day_any:
        return weekday_match
    if weekday_any:
        return day_match
    return day_match or weekday_match


__all__ = [
    "LoadedSchedules",
    "ScheduleExecutionMode",
    "ScheduleKind",
    "ScheduleOverlapPolicy",
    "ScheduleRecord",
    "ScheduleRegistry",
    "ScheduleRunMissedPolicy",
    "ScheduleRunRecord",
    "ScheduleRunStatus",
    "ScheduleScope",
    "ScheduleSessionMode",
    "ScheduleStateRecord",
    "add_schedule",
    "delete_schedule",
    "get_schedule",
    "global_schedules_path",
    "global_schedule_state_path",
    "load_schedule_states",
    "load_schedule_states_for_scope",
    "load_schedules",
    "next_schedule_time",
    "parse_timestamp",
    "project_schedule_state_path",
    "project_schedules_path",
    "render_prompt_variables",
    "serialize_schedule",
    "serialize_schedule_run",
    "serialize_schedule_state",
    "timezone_for_schedule",
    "update_schedule",
    "write_schedule_states",
]
