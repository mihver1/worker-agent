"""Migration system — version tracking and config upgrades.

Tracks a ``config_version`` in ``~/.config/worker/state.json``
and runs registered migration functions when the version is behind.

Each migration is a function that takes the config directory path
and performs the necessary changes (file renames, schema updates, etc.).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from worker_core.config import CONFIG_DIR

logger = logging.getLogger("worker.migrations")

_STATE_FILE = CONFIG_DIR / "state.json"

# Current schema version — bump when adding a new migration
CURRENT_VERSION = 1


@dataclass
class Migration:
    """A single migration step."""

    version: int
    description: str
    fn: Callable[[Path], None]


# ── Migration registry ────────────────────────────────────────────

_MIGRATIONS: list[Migration] = []


def migration(version: int, description: str) -> Callable:
    """Decorator to register a migration function."""

    def decorator(fn: Callable[[Path], None]) -> Callable[[Path], None]:
        _MIGRATIONS.append(Migration(version=version, description=description, fn=fn))
        _MIGRATIONS.sort(key=lambda m: m.version)
        return fn

    return decorator


# ── State management ──────────────────────────────────────────────


def _read_state() -> dict:
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _write_state(state: dict) -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_current_version() -> int:
    """Return the currently stored config version."""
    state = _read_state()
    return state.get("config_version", 0)


def set_version(version: int) -> None:
    """Update the stored config version."""
    state = _read_state()
    state["config_version"] = version
    _write_state(state)


# ── Runner ────────────────────────────────────────────────────────


def pending_migrations() -> list[Migration]:
    """Return migrations that haven't been applied yet."""
    current = get_current_version()
    return [m for m in _MIGRATIONS if m.version > current]


def run_migrations(config_dir: Path | None = None) -> list[str]:
    """Run all pending migrations.

    Returns list of descriptions of applied migrations.
    """
    target_dir = config_dir or CONFIG_DIR
    current = get_current_version()
    applied: list[str] = []

    for m in _MIGRATIONS:
        if m.version <= current:
            continue
        logger.info("Running migration v%d: %s", m.version, m.description)
        try:
            m.fn(target_dir)
            set_version(m.version)
            applied.append(f"v{m.version}: {m.description}")
        except Exception as e:
            logger.error("Migration v%d failed: %s", m.version, e)
            break  # Stop on first failure

    return applied


def check_and_migrate() -> None:
    """Check if migrations are needed and run them.

    Called on startup — safe to call multiple times.
    """
    if get_current_version() >= CURRENT_VERSION:
        return
    applied = run_migrations()
    if applied:
        logger.info("Applied %d migration(s)", len(applied))
    else:
        # No migrations registered yet, just set the version
        set_version(CURRENT_VERSION)


# ── Built-in migrations ──────────────────────────────────────────


@migration(1, "Initial config version tracking")
def _migration_v1(config_dir: Path) -> None:
    """No-op — just establishes version tracking."""
    pass
