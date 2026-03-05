"""cmux terminal integration — status, progress, notifications, log.

All functions are **no-ops** when not running inside cmux
(determined by the absence of ``CMUX_WORKSPACE_ID`` env var).

Env variables checked:
    CMUX_WORKSPACE_ID  — auto-set in cmux terminals (required)
    CMUX_SURFACE_ID    — auto-set; used as default --surface
    CMUX_SOCKET_PATH   — override default socket (/tmp/cmux.sock)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Literal

logger = logging.getLogger("worker.cmux")

# ── Detection ─────────────────────────────────────────────────────

_CMUX_BIN: str | None = None


def _find_cmux() -> str | None:
    """Locate the cmux binary (cached)."""
    global _CMUX_BIN
    if _CMUX_BIN is not None:
        return _CMUX_BIN or None

    path = shutil.which("cmux")
    if path:
        _CMUX_BIN = path
    else:
        # Fallback to well-known macOS location
        fallback = "/Applications/cmux.app/Contents/Resources/bin/cmux"
        if os.path.isfile(fallback):
            _CMUX_BIN = fallback
        else:
            _CMUX_BIN = ""  # sentinel: checked, not found
    return _CMUX_BIN or None


def is_cmux() -> bool:
    """Return True if we are running inside a cmux terminal session."""
    return bool(os.environ.get("CMUX_WORKSPACE_ID")) and _find_cmux() is not None


def workspace_id() -> str:
    """Return the current cmux workspace ID."""
    return os.environ.get("CMUX_WORKSPACE_ID", "")


def surface_id() -> str:
    """Return the current cmux surface ID."""
    return os.environ.get("CMUX_SURFACE_ID", "")


# ── Low-level command runner ──────────────────────────────────────


async def _run(args: list[str], *, timeout: float = 5.0) -> str:
    """Execute a cmux CLI command asynchronously.

    Returns stdout on success; logs and returns "" on failure.
    All invocations are fire-and-forget safe.
    """
    binary = _find_cmux()
    if not binary:
        return ""

    cmd = [binary, *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            logger.debug("cmux %s failed: %s", args[0], stderr.decode(errors="replace"))
        return stdout.decode(errors="replace").strip()
    except asyncio.TimeoutError:
        logger.debug("cmux %s timed out", args[0])
        return ""
    except Exception as e:
        logger.debug("cmux %s error: %s", args[0], e)
        return ""


def _run_sync(args: list[str]) -> str:
    """Synchronous cmux call (for non-async contexts)."""
    import subprocess

    binary = _find_cmux()
    if not binary:
        return ""

    try:
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip()
    except Exception as e:
        logger.debug("cmux sync %s error: %s", args[0], e)
        return ""


# ── Sidebar status ────────────────────────────────────────────────


async def set_status(
    key: str,
    value: str,
    *,
    icon: str = "",
    color: str = "",
) -> None:
    """Set a sidebar status entry (key=value with optional icon/color)."""
    if not is_cmux():
        return
    args = ["set-status", key, value]
    if icon:
        args.extend(["--icon", icon])
    if color:
        args.extend(["--color", color])
    await _run(args)


async def clear_status(key: str) -> None:
    """Clear a sidebar status entry."""
    if not is_cmux():
        return
    await _run(["clear-status", key])


async def list_status() -> str:
    """List all sidebar status entries."""
    if not is_cmux():
        return ""
    return await _run(["list-status"])


# ── Progress ──────────────────────────────────────────────────────


async def set_progress(value: float, *, label: str = "") -> None:
    """Set sidebar progress bar (0.0–1.0)."""
    if not is_cmux():
        return
    args = ["set-progress", f"{value:.2f}"]
    if label:
        args.extend(["--label", label])
    await _run(args)


async def clear_progress() -> None:
    """Clear sidebar progress bar."""
    if not is_cmux():
        return
    await _run(["clear-progress"])


# ── Notifications ─────────────────────────────────────────────────


async def notify(
    title: str,
    *,
    subtitle: str = "",
    body: str = "",
) -> None:
    """Send a desktop notification via cmux."""
    if not is_cmux():
        return
    args = ["notify", "--title", title]
    if subtitle:
        args.extend(["--subtitle", subtitle])
    if body:
        args.extend(["--body", body])
    await _run(args)


# ── Log ───────────────────────────────────────────────────────────

LogLevel = Literal["debug", "info", "warn", "error"]


async def log(
    message: str,
    *,
    level: LogLevel = "info",
    source: str = "worker",
) -> None:
    """Append an entry to the cmux sidebar log."""
    if not is_cmux():
        return
    await _run(["log", "--level", level, "--source", source, "--", message])


async def clear_log() -> None:
    """Clear the cmux sidebar log."""
    if not is_cmux():
        return
    await _run(["clear-log"])


# ── Workspace / pane management ───────────────────────────────────


async def new_split(
    direction: Literal["left", "right", "up", "down"] = "right",
) -> str:
    """Open a new terminal split pane. Returns the new pane ID."""
    if not is_cmux():
        return ""
    return await _run(["new-split", direction])


async def new_pane(
    *,
    pane_type: Literal["terminal", "browser"] = "terminal",
    direction: Literal["left", "right", "up", "down"] = "right",
    url: str = "",
) -> str:
    """Open a new pane (terminal or browser). Returns pane ID."""
    if not is_cmux():
        return ""
    args = ["new-pane", "--type", pane_type, "--direction", direction]
    if url:
        args.extend(["--url", url])
    return await _run(args)


# ── Browser ───────────────────────────────────────────────────────


async def browser_open(url: str = "") -> str:
    """Open a browser split in the current workspace."""
    if not is_cmux():
        return ""
    args = ["browser", "open"]
    if url:
        args.append(url)
    return await _run(args)


async def browser_navigate(url: str) -> str:
    """Navigate an existing browser surface to a URL."""
    if not is_cmux():
        return ""
    return await _run(["browser", "navigate", url])


async def browser_snapshot(*, interactive: bool = False, compact: bool = True) -> str:
    """Take a DOM snapshot of the current browser surface."""
    if not is_cmux():
        return ""
    args = ["browser", "snapshot"]
    if interactive:
        args.append("--interactive")
    if compact:
        args.append("--compact")
    return await _run(args, timeout=15.0)


# ── Convenience ───────────────────────────────────────────────────


async def send_text(text: str) -> None:
    """Send text to the current surface."""
    if not is_cmux():
        return
    await _run(["send", text])


async def send_key(key: str) -> None:
    """Send a key press to the current surface."""
    if not is_cmux():
        return
    await _run(["send-key", key])


async def read_screen(*, scrollback: bool = False, lines: int = 0) -> str:
    """Read the current terminal screen content."""
    if not is_cmux():
        return ""
    args = ["read-screen"]
    if scrollback:
        args.append("--scrollback")
    if lines > 0:
        args.extend(["--lines", str(lines)])
    return await _run(args, timeout=10.0)
