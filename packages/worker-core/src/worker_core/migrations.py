"""Migration system — versioned Artel bootstrap and first-run Worker state copy."""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import worker_core.config as config

logger = logging.getLogger("artel.migrations")

# Current schema version — bump when adding a new global migration
CURRENT_VERSION = 2


@dataclass
class Migration:
    """A single migration step."""

    version: int
    description: str
    fn: Callable[[Path, dict[str, Any]], None]


# ── Migration registry ────────────────────────────────────────────

_MIGRATIONS: list[Migration] = []


def migration(version: int, description: str) -> Callable:
    """Decorator to register a migration function."""

    def decorator(
        fn: Callable[[Path, dict[str, Any]], None],
    ) -> Callable[[Path, dict[str, Any]], None]:
        _MIGRATIONS.append(Migration(version=version, description=description, fn=fn))
        _MIGRATIONS.sort(key=lambda item: item.version)
        return fn

    return decorator


# ── State management ──────────────────────────────────────────────


def _state_file() -> Path:
    return config.GLOBAL_STATE_FILE


def _legacy_state_file() -> Path:
    return config.LEGACY_GLOBAL_STATE_FILE


def _read_state_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _read_state() -> dict[str, Any]:
    state_path = _state_file()
    if state_path.exists():
        return _read_state_file(state_path)
    return _read_state_file(_legacy_state_file())


def _write_state(state: dict[str, Any]) -> None:
    path = _state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def get_current_version() -> int:
    """Return the currently stored config version."""
    state = _read_state()
    version = state.get("config_version", 0)
    return version if isinstance(version, int) else 0


def set_version(version: int) -> None:
    """Update the stored config version."""
    state = _read_state()
    state["config_version"] = version
    _write_state(state)


# ── File copy helpers ─────────────────────────────────────────────


def _copy_file_if_missing(source: Path, target: Path) -> bool:
    if source == target or not source.is_file() or target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def _copy_tree_if_missing(source_dir: Path, target_dir: Path) -> list[str]:
    if source_dir == target_dir or not source_dir.is_dir():
        return []
    copied: list[str] = []
    for source_path in sorted(source_dir.rglob("*")):
        if source_path.is_dir():
            continue
        relative = source_path.relative_to(source_dir)
        target_path = target_dir / relative
        if target_path.exists():
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path)
        copied.append(relative.as_posix())
    return copied


def _merge_copied_items(record: dict[str, Any], copied: list[str]) -> None:
    existing = record.get("copied", [])
    existing_items = [item for item in existing if isinstance(item, str)]
    record["copied"] = sorted(set(existing_items) | set(copied))
    record["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _copy_global_state() -> list[str]:
    copied: list[str] = []
    file_pairs = [
        ("config.toml", config.LEGACY_GLOBAL_CONFIG, config.GLOBAL_CONFIG),
        ("auth.json", config.LEGACY_AUTH_FILE, config.AUTH_FILE),
        ("sessions.db", config.LEGACY_SESSIONS_DB, config.SESSIONS_DB),
        ("extensions.lock", config.LEGACY_EXTENSIONS_MANIFEST, config.EXTENSIONS_MANIFEST),
        (
            "server-provider-overlay.json",
            config.LEGACY_SERVER_PROVIDER_OVERLAY_PATH,
            config.SERVER_PROVIDER_OVERLAY_PATH,
        ),
        ("mcp.json", config.LEGACY_GLOBAL_MCP_PATH, config.GLOBAL_MCP_PATH),
        ("SYSTEM.md", config.LEGACY_GLOBAL_SYSTEM_OVERRIDE, config.GLOBAL_SYSTEM_OVERRIDE),
        ("APPEND_SYSTEM.md", config.LEGACY_GLOBAL_APPEND_SYSTEM, config.GLOBAL_APPEND_SYSTEM),
        ("AGENTS.md", config.LEGACY_GLOBAL_AGENTS_FILE, config.GLOBAL_AGENTS_FILE),
    ]
    for label, source, target in file_pairs:
        if _copy_file_if_missing(source, target):
            copied.append(label)

    dir_pairs = [
        ("prompts", config.LEGACY_PROMPTS_DIR, config.PROMPTS_DIR),
        ("skills", config.LEGACY_SKILLS_DIR, config.SKILLS_DIR),
        ("registry_cache", config.LEGACY_REGISTRY_CACHE_DIR, config.REGISTRY_CACHE_DIR),
    ]
    for label, source_dir, target_dir in dir_pairs:
        copied.extend(
            f"{label}/{relative}" for relative in _copy_tree_if_missing(source_dir, target_dir)
        )
    return copied


def _copy_project_state(project_dir: str) -> list[str]:
    copied: list[str] = []
    file_pairs = [
        (
            "config.toml",
            config.legacy_project_config_path(project_dir),
            config.project_config_path(project_dir),
        ),
        (
            "AGENTS.md",
            config.legacy_project_agents_path(project_dir),
            config.project_agents_path(project_dir),
        ),
        (
            "SYSTEM.md",
            config.legacy_project_system_override_path(project_dir),
            config.project_system_override_path(project_dir),
        ),
        (
            "APPEND_SYSTEM.md",
            config.legacy_project_append_system_path(project_dir),
            config.project_append_system_path(project_dir),
        ),
        (
            "server.json",
            config.legacy_project_server_registry_path(project_dir),
            config.project_server_registry_path(project_dir),
        ),
        (
            "mcp.json",
            config.legacy_project_mcp_path(project_dir),
            config.project_mcp_path(project_dir),
        ),
    ]
    for label, source, target in file_pairs:
        if _copy_file_if_missing(source, target):
            copied.append(label)

    dir_pairs = [
        (
            "prompts",
            config.legacy_project_prompts_path(project_dir),
            config.project_prompts_path(project_dir),
        ),
        (
            "skills",
            config.legacy_project_skills_path(project_dir),
            config.project_skills_path(project_dir),
        ),
    ]
    for label, source_dir, target_dir in dir_pairs:
        copied.extend(
            f"{label}/{relative}" for relative in _copy_tree_if_missing(source_dir, target_dir)
        )
    return copied


def _record_global_migration(state: dict[str, Any], copied: list[str]) -> None:
    migrations = state.setdefault("artel_migrations", {})
    record = migrations.setdefault("worker_global_to_artel", {})
    record["source"] = str(config.LEGACY_CONFIG_DIR)
    record["target"] = str(config.CONFIG_DIR)
    if config.LEGACY_GLOBAL_STATE_FILE.exists():
        record["merged_state"] = True
    _merge_copied_items(record, copied)


def _record_project_migration(
    state: dict[str, Any],
    *,
    project_dir: str,
    copied: list[str],
) -> None:
    project_migrations = state.setdefault("artel_project_migrations", {})
    record = project_migrations.setdefault(project_dir, {})
    record["source"] = str(config.legacy_project_state_dir(project_dir))
    record["target"] = str(config.project_state_dir(project_dir))
    _merge_copied_items(record, copied)


# ── Runner ────────────────────────────────────────────────────────


def pending_migrations() -> list[Migration]:
    """Return migrations that haven't been applied yet."""
    current = get_current_version()
    return [migration for migration in _MIGRATIONS if migration.version > current]


def run_migrations(config_dir: Path | None = None) -> list[str]:
    """Run all pending global migrations and return their descriptions."""
    target_dir = config_dir or config.CONFIG_DIR
    state = _read_state()
    current = state.get("config_version", 0)
    if not isinstance(current, int):
        current = 0
    applied: list[str] = []

    for item in _MIGRATIONS:
        if item.version <= current:
            continue
        logger.info("Running migration v%d: %s", item.version, item.description)
        item.fn(target_dir, state)
        state["config_version"] = item.version
        _write_state(state)
        applied.append(f"v{item.version}: {item.description}")
        current = item.version

    return applied


def migrate_project_state(project_dir: str | None) -> list[str]:
    """Copy legacy project-local Worker state into .artel for the given project."""
    if not project_dir:
        return []

    resolved_project_dir = str(Path(project_dir).resolve(strict=False))
    legacy_root = config.legacy_project_state_dir(resolved_project_dir)
    target_root = config.project_state_dir(resolved_project_dir)
    if legacy_root == target_root or not legacy_root.exists():
        return []

    copied = _copy_project_state(resolved_project_dir)
    state = _read_state()
    _record_project_migration(
        state,
        project_dir=resolved_project_dir,
        copied=copied,
    )
    _write_state(state)
    return copied


def check_and_migrate(project_dir: str | None = None) -> None:
    """Check whether Artel migrations are needed and apply them safely."""
    applied = run_migrations() if get_current_version() < CURRENT_VERSION else []
    if applied:
        logger.info("Applied %d global migration(s)", len(applied))

    project_copied = migrate_project_state(project_dir)
    if project_copied:
        logger.info(
            "Migrated %d project-local artifact(s) into %s",
            len(project_copied),
            config.project_state_dir(project_dir or ""),
        )


# ── Built-in migrations ──────────────────────────────────────────


@migration(1, "Initial config version tracking")
def _migration_v1(config_dir: Path, state: dict[str, Any]) -> None:
    """No-op — establishes version tracking for the migration system."""


@migration(2, "Copy legacy Worker global state into Artel config roots")
def _migration_v2(config_dir: Path, state: dict[str, Any]) -> None:
    copied = _copy_global_state()
    _record_global_migration(state, copied)
