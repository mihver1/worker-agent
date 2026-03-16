"""Project/global rules storage and prompt/enforcement helpers."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import worker_core.config as config_mod

RuleScope = Literal["global", "project"]

_TOOL_ALIASES: dict[str, tuple[str, ...]] = {
    "read": ("read", "read tool"),
    "write": ("write", "write tool", "create file", "overwrite file"),
    "edit": ("edit", "edit tool", "modify file"),
    "bash": ("bash", "shell", "terminal", "command", "commands"),
}

_PATH_ARG_BY_TOOL = {
    "read": "path",
    "write": "path",
    "edit": "path",
}


@dataclass(slots=True)
class RuleRecord:
    id: str
    scope: RuleScope
    text: str
    enabled: bool = True
    order: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass(slots=True)
class RuleViolation:
    rule: RuleRecord
    reason: str


@dataclass(slots=True)
class RuleCollection:
    global_rules: list[RuleRecord]
    project_rules: list[RuleRecord]

    @property
    def all(self) -> list[RuleRecord]:
        return [*self.global_rules, *self.project_rules]

    @property
    def active(self) -> list[RuleRecord]:
        return [rule for rule in self.all if rule.enabled]


@dataclass(slots=True)
class SessionRuleOverrides:
    disabled_rule_ids: set[str]
    enabled_rule_ids: set[str]

    @classmethod
    def empty(cls) -> SessionRuleOverrides:
        return cls(disabled_rule_ids=set(), enabled_rule_ids=set())


def global_rules_path() -> Path:
    return config_mod.CONFIG_DIR / "rules.json"


def project_rules_path(project_dir: str) -> Path:
    return config_mod.project_state_dir(project_dir) / "rules.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_scope(scope: str) -> RuleScope:
    lowered = scope.strip().lower()
    if lowered not in {"global", "project"}:
        raise ValueError("scope must be 'global' or 'project'")
    return lowered  # type: ignore[return-value]


def _load_rules_file(path: Path, *, scope: RuleScope) -> list[RuleRecord]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []

    records: list[RuleRecord] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("id", "")).strip()
        text = str(item.get("text", "")).strip()
        if not rule_id or not text:
            continue
        item_scope = str(item.get("scope", scope)).strip().lower()
        normalized_scope = scope if item_scope not in {"global", "project"} else item_scope
        records.append(
            RuleRecord(
                id=rule_id,
                scope=normalized_scope,  # type: ignore[arg-type]
                text=text,
                enabled=bool(item.get("enabled", True)),
                order=int(item.get("order", 0) or 0),
                created_at=str(item.get("created_at", "")).strip(),
                updated_at=str(item.get("updated_at", "")).strip(),
            )
        )
    records.sort(key=lambda rule: (rule.order, rule.created_at or "", rule.id))
    return _normalize_rule_orders(records)


def _normalize_rule_orders(rules: list[RuleRecord]) -> list[RuleRecord]:
    for index, rule in enumerate(rules, start=1):
        rule.order = index
    return rules


def _write_rules_file(path: Path, rules: list[RuleRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_rule_orders(list(rules))
    payload = [asdict(rule) for rule in normalized]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_rules(project_dir: str = "") -> RuleCollection:
    global_records = _load_rules_file(global_rules_path(), scope="global")
    project_records = (
        _load_rules_file(project_rules_path(project_dir), scope="project") if project_dir else []
    )
    return RuleCollection(global_rules=global_records, project_rules=project_records)


def list_rules(project_dir: str = "") -> list[RuleRecord]:
    return load_rules(project_dir).all


def move_rule(
    rule_id: str,
    *,
    project_dir: str = "",
    position: int | None = None,
    offset: int | None = None,
) -> RuleRecord:
    needle = rule_id.strip()
    if not needle:
        raise ValueError("Missing rule id")
    current = get_rule(needle, project_dir)
    if current is None:
        raise ValueError(f"Rule '{needle}' not found")
    path = global_rules_path() if current.scope == "global" else project_rules_path(project_dir)
    records = _load_rules_file(path, scope=current.scope)
    current_index = next(
        (index for index, record in enumerate(records) if record.id == needle), None
    )
    if current_index is None:
        raise ValueError(f"Rule '{needle}' not found")
    record = records.pop(current_index)
    if position is not None:
        target_index = max(0, min(len(records), position - 1))
    elif offset is not None:
        target_index = max(0, min(len(records), current_index + offset))
    else:
        raise ValueError("Either position or offset is required")
    records.insert(target_index, record)
    record.updated_at = _now()
    _write_rules_file(path, records)
    return get_rule(needle, project_dir) or record


def active_rules(
    project_dir: str = "", overrides: SessionRuleOverrides | None = None
) -> list[RuleRecord]:
    rules = list_rules(project_dir)
    if overrides is None:
        return [rule for rule in rules if rule.enabled]
    active: list[RuleRecord] = []
    for rule in rules:
        if rule.id in overrides.disabled_rule_ids:
            continue
        if rule.enabled or rule.id in overrides.enabled_rule_ids:
            active.append(rule)
    return active


def get_rule(rule_id: str, project_dir: str = "") -> RuleRecord | None:
    needle = rule_id.strip()
    if not needle:
        return None
    for rule in list_rules(project_dir):
        if rule.id == needle:
            return rule
    return None


def add_rule(
    *,
    scope: str,
    text: str,
    project_dir: str = "",
    enabled: bool = True,
) -> RuleRecord:
    normalized_scope = _normalize_scope(scope)
    normalized_text = text.strip()
    if not normalized_text:
        raise ValueError("Rule text cannot be empty")
    path = global_rules_path() if normalized_scope == "global" else project_rules_path(project_dir)
    if normalized_scope == "project" and not project_dir:
        raise ValueError("project_dir is required for project-scoped rules")
    records = _load_rules_file(path, scope=normalized_scope)
    now = _now()
    rule = RuleRecord(
        id=f"rule-{uuid.uuid4().hex[:8]}",
        scope=normalized_scope,
        text=normalized_text,
        enabled=enabled,
        order=len(records) + 1,
        created_at=now,
        updated_at=now,
    )
    records.append(rule)
    _write_rules_file(path, records)
    return rule


def update_rule(
    rule_id: str,
    *,
    project_dir: str = "",
    text: str | None = None,
    enabled: bool | None = None,
    scope: str | None = None,
) -> RuleRecord:
    needle = rule_id.strip()
    if not needle:
        raise ValueError("Missing rule id")
    current = get_rule(needle, project_dir)
    if current is None:
        raise ValueError(f"Rule '{needle}' not found")

    target_scope = _normalize_scope(scope) if scope is not None else current.scope
    target_path = (
        global_rules_path() if target_scope == "global" else project_rules_path(project_dir)
    )
    source_path = (
        global_rules_path() if current.scope == "global" else project_rules_path(project_dir)
    )
    source_records = _load_rules_file(source_path, scope=current.scope)
    source_records = [record for record in source_records if record.id != needle]

    updated_text = current.text if text is None else text.strip()
    if not updated_text:
        raise ValueError("Rule text cannot be empty")
    target_records = _load_rules_file(target_path, scope=target_scope)
    target_order = current.order if target_scope == current.scope else (len(target_records) + 1)
    updated = RuleRecord(
        id=current.id,
        scope=target_scope,
        text=updated_text,
        enabled=current.enabled if enabled is None else enabled,
        order=target_order,
        created_at=current.created_at or _now(),
        updated_at=_now(),
    )

    _write_rules_file(source_path, source_records)
    target_records = _load_rules_file(target_path, scope=target_scope)
    target_records.append(updated)
    target_records.sort(key=lambda rule: (rule.order, rule.created_at or "", rule.id))
    _write_rules_file(target_path, target_records)
    return updated


def delete_rule(rule_id: str, project_dir: str = "") -> RuleRecord | None:
    needle = rule_id.strip()
    if not needle:
        return None
    current = get_rule(needle, project_dir)
    if current is None:
        return None
    path = global_rules_path() if current.scope == "global" else project_rules_path(project_dir)
    records = _load_rules_file(path, scope=current.scope)
    remaining = [record for record in records if record.id != needle]
    _write_rules_file(path, remaining)
    return current


def effective_rule_state(rule: RuleRecord, overrides: SessionRuleOverrides | None = None) -> str:
    if overrides is None:
        return "enabled" if rule.enabled else "disabled"
    if rule.id in overrides.disabled_rule_ids:
        return "session-disabled"
    if rule.id in overrides.enabled_rule_ids and not rule.enabled:
        return "session-enabled"
    return "enabled" if rule.enabled else "disabled"


def format_rules_for_system_prompt(
    project_dir: str = "",
    overrides: SessionRuleOverrides | None = None,
) -> str:
    rules = active_rules(project_dir, overrides)
    if not rules:
        return ""
    lines = [
        "## Active Rules",
        "These rules are mandatory. If a request conflicts with any active rule, "
        "refuse and do not perform the action or call tools to carry it out.",
        "",
    ]
    for rule in rules:
        scope_label = "global" if rule.scope == "global" else "project"
        lines.append(f"- [{scope_label}] ({rule.id}) {rule.text}")
    return "\n".join(lines)


def evaluate_rule_violation(
    tool_name: str,
    args: dict[str, object],
    project_dir: str = "",
    overrides: SessionRuleOverrides | None = None,
) -> RuleViolation | None:
    for rule in active_rules(project_dir, overrides):
        violation = _evaluate_rule_text(rule, tool_name, args, project_dir=project_dir)
        if violation is not None:
            return violation
    return None


def _evaluate_rule_text(
    rule: RuleRecord,
    tool_name: str,
    args: dict[str, object],
    *,
    project_dir: str,
) -> RuleViolation | None:
    lowered = rule.text.lower()

    tool_aliases = _TOOL_ALIASES.get(tool_name, ())
    for alias in tool_aliases:
        if any(
            phrase in lowered
            for phrase in (
                f"do not use {alias}",
                f"don't use {alias}",
                f"never use {alias}",
                f"forbid {alias}",
                f"no {alias}",
            )
        ):
            return RuleViolation(rule=rule, reason=f"Rule forbids using tool '{tool_name}'.")

    if tool_name == "bash":
        command = str(args.get("command", "")).strip().lower()
        match = re.search(r"(?:do not|don't|never) run\s+`?([^`\n]+?)`?(?:[\.;]|$)", lowered)
        if match is not None:
            forbidden = match.group(1).strip()
            if forbidden and forbidden in command:
                return RuleViolation(
                    rule=rule, reason=f"Rule forbids running command matching '{forbidden}'."
                )

    path_arg = _PATH_ARG_BY_TOOL.get(tool_name)
    if path_arg:
        raw_path = str(args.get(path_arg, "")).strip()
        if raw_path:
            target_path = Path(raw_path).expanduser()
            if not target_path.is_absolute():
                target_path = Path(project_dir or ".") / target_path
            resolved_target = target_path.resolve(strict=False)
            protected = _protected_path_from_rule(rule.text, project_dir)
            if protected is not None:
                protected_resolved = protected.resolve(strict=False)
                if (
                    resolved_target == protected_resolved
                    or protected_resolved in resolved_target.parents
                ):
                    return RuleViolation(
                        rule=rule,
                        reason=f"Rule marks '{protected_resolved}' as read-only for modifications.",
                    )
    return None


def set_rule_enabled_for_session(
    overrides: SessionRuleOverrides,
    rule_id: str,
    enabled: bool,
) -> SessionRuleOverrides:
    normalized_id = rule_id.strip()
    if not normalized_id:
        return overrides
    if enabled:
        overrides.disabled_rule_ids.discard(normalized_id)
        overrides.enabled_rule_ids.add(normalized_id)
    else:
        overrides.enabled_rule_ids.discard(normalized_id)
        overrides.disabled_rule_ids.add(normalized_id)
    return overrides


def reset_rule_for_session(
    overrides: SessionRuleOverrides,
    rule_id: str,
) -> SessionRuleOverrides:
    normalized_id = rule_id.strip()
    if not normalized_id:
        return overrides
    overrides.disabled_rule_ids.discard(normalized_id)
    overrides.enabled_rule_ids.discard(normalized_id)
    return overrides


def clear_session_rule_overrides(overrides: SessionRuleOverrides) -> SessionRuleOverrides:
    overrides.disabled_rule_ids.clear()
    overrides.enabled_rule_ids.clear()
    return overrides


def serialize_session_rule_overrides(overrides: SessionRuleOverrides) -> dict[str, Any]:
    return {
        "disabled_rule_ids": sorted(overrides.disabled_rule_ids),
        "enabled_rule_ids": sorted(overrides.enabled_rule_ids),
    }


def deserialize_session_rule_overrides(payload: dict[str, Any] | None) -> SessionRuleOverrides:
    if not isinstance(payload, dict):
        return SessionRuleOverrides.empty()
    disabled = payload.get("disabled_rule_ids", [])
    enabled = payload.get("enabled_rule_ids", [])
    return SessionRuleOverrides(
        disabled_rule_ids={str(item).strip() for item in disabled if str(item).strip()},
        enabled_rule_ids={str(item).strip() for item in enabled if str(item).strip()},
    )


def _protected_path_from_rule(text: str, project_dir: str) -> Path | None:
    patterns = (
        r"(?:do not|don't|never)\s+(?:modify|edit|change|touch|rewrite|write to)\s+"
        r"([`'\"]?[^`'\"\n]+[`'\"]?)",
        r"read-only\s*:\s*([`'\"]?[^`'\"\n]+[`'\"]?)",
        r"([`'\"][^`'\"]+[`'\"]|\S+)\s+is\s+read-only",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match is None:
            continue
        candidate = match.group(1).strip().strip("`'\"")
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            path = Path(project_dir or ".") / path
        return path
    return None


__all__ = [
    "RuleCollection",
    "RuleRecord",
    "RuleScope",
    "RuleViolation",
    "SessionRuleOverrides",
    "active_rules",
    "add_rule",
    "clear_session_rule_overrides",
    "delete_rule",
    "deserialize_session_rule_overrides",
    "effective_rule_state",
    "evaluate_rule_violation",
    "format_rules_for_system_prompt",
    "get_rule",
    "global_rules_path",
    "list_rules",
    "load_rules",
    "move_rule",
    "project_rules_path",
    "reset_rule_for_session",
    "serialize_session_rule_overrides",
    "set_rule_enabled_for_session",
    "update_rule",
]
