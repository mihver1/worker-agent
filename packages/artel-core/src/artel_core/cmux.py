"""cmux terminal integration and interactive preflight helpers.

Current-surface actions are **no-ops** when not running inside cmux
(determined by the absence of ``CMUX_WORKSPACE_ID`` or missing cmux binary).
Workspace/surface management commands can still be issued from outside the
current cmux shell when the ``cmux`` binary can reach the daemon.

Env variables checked:
    CMUX_WORKSPACE_ID  — auto-set in cmux terminals; required for current-surface actions
    CMUX_SURFACE_ID    — auto-set; used as current/default surface
    CMUX_SOCKET_PATH   — override default socket (/tmp/cmux.sock)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import shutil
import socket
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger("artel.cmux")

DEFAULT_CMUX_SOCKET_PATH = "/tmp/cmux.sock"
DEFAULT_ARTEL_WORKSPACE_NAME = "artel-main"
DEFAULT_ARTEL_DASHBOARD_SURFACE_TITLE = "dashboard"
DEFAULT_ARTEL_ORCHESTRATOR_SURFACE_TITLE = "orchestrator"
EXPECTED_CMUX_CAPABILITIES = (
    "workspace",
    "surface",
    "browser",
    "set-status",
    "notify",
)

_CMUX_LIST_WORKSPACES = ("list-workspaces",)
_CMUX_NEW_WORKSPACE = ("new-workspace",)
_CMUX_SELECT_WORKSPACE = ("select-workspace",)
_CMUX_RENAME_WORKSPACE = ("rename-workspace",)
_CMUX_LIST_PANES = ("list-panes",)
_CMUX_LIST_PANE_SURFACES = ("list-pane-surfaces",)
_CMUX_NEW_SURFACE = ("new-surface",)
_CMUX_RENAME_TAB = ("rename-tab",)
_CMUX_FOCUS_PANE = ("focus-pane",)

# ── Detection ─────────────────────────────────────────────────────

_CMUX_BIN: str | None = None


@dataclass(slots=True)
class CmuxPreflightResult:
    """Structured cmux readiness result for the interactive Artel path."""

    ok: bool
    code: str = "ok"
    summary: str = ""
    details: list[str] = field(default_factory=list)
    guidance: list[str] = field(default_factory=list)
    binary_path: str = ""
    workspace: str = ""
    socket_path: str = ""
    available_capabilities: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()

    def format_message(self) -> str:
        """Render a user-facing preflight message."""
        lines: list[str] = []
        headline = self.summary.strip() or "cmux preflight failed."
        lines.append(headline)
        lines.extend(detail.strip() for detail in self.details if detail.strip())
        if self.guidance:
            lines.append("")
            lines.append("Next steps:")
            lines.extend(f"  - {item.strip()}" for item in self.guidance if item.strip())
        return "\n".join(lines).strip()


@dataclass(slots=True)
class CmuxWorkspaceRecord:
    id: str = ""
    name: str = ""
    current: bool = False
    raw: str = ""


@dataclass(slots=True)
class CmuxSurfaceRecord:
    id: str = ""
    title: str = ""
    workspace: str = ""
    current: bool = False
    raw: str = ""


@dataclass(slots=True)
class ArtelWorkspaceBootstrap:
    workspace: CmuxWorkspaceRecord | None = None
    dashboard: CmuxSurfaceRecord | None = None
    orchestrator: CmuxSurfaceRecord | None = None


def _find_cmux() -> str | None:
    """Locate the cmux binary (cached)."""
    global _CMUX_BIN
    if _CMUX_BIN is not None:
        return _CMUX_BIN or None

    path = shutil.which("cmux")
    if path:
        _CMUX_BIN = path
    else:
        fallback = "/Applications/cmux.app/Contents/Resources/bin/cmux"
        _CMUX_BIN = fallback if os.path.isfile(fallback) else ""
    return _CMUX_BIN or None


def is_cmux() -> bool:
    """Return True if we are running inside a cmux terminal session."""
    return bool(os.environ.get("CMUX_WORKSPACE_ID")) and _find_cmux() is not None


def can_manage_cmux() -> bool:
    """Return True when cmux workspace/surface commands can be issued."""
    return _find_cmux() is not None


def workspace_id() -> str:
    """Return the current cmux workspace ID."""
    return os.environ.get("CMUX_WORKSPACE_ID", "")


def surface_id() -> str:
    """Return the current cmux surface ID."""
    return os.environ.get("CMUX_SURFACE_ID", "")


def cmux_socket_path() -> str:
    """Return the configured cmux socket path."""
    return os.environ.get("CMUX_SOCKET_PATH", "").strip() or DEFAULT_CMUX_SOCKET_PATH


def probe_cmux_capabilities(help_text: str | None = None) -> set[str]:
    """Best-effort capability probe from cmux help output."""
    normalized = str(help_text or "").strip().lower()
    if not normalized:
        normalized = (_run_sync(["help"]) or _run_sync(["--help"])).lower()

    capabilities: set[str] = set()
    for capability in EXPECTED_CMUX_CAPABILITIES:
        if capability in normalized:
            capabilities.add(capability)
    return capabilities


def is_cmux_socket_reachable(socket_path: str) -> bool:
    """Return True when the configured cmux socket can accept a connection."""
    normalized = str(socket_path or "").strip()
    if not normalized or not os.path.exists(normalized):
        return False
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(0.5)
        client.connect(normalized)
    except OSError:
        return False
    finally:
        client.close()
    return True


def preflight_cmux(
    *,
    expected_capabilities: tuple[str, ...] = EXPECTED_CMUX_CAPABILITIES,
) -> CmuxPreflightResult:
    """Validate that the interactive Artel path can run inside cmux."""
    binary = _find_cmux()
    workspace = workspace_id().strip()
    socket_path = cmux_socket_path()

    if not binary:
        return CmuxPreflightResult(
            ok=False,
            code="binary_missing",
            summary="Artel interactive mode requires cmux, but the cmux binary was not found.",
            guidance=[
                "Install cmux and make sure the `cmux` binary is on PATH.",
                "If cmux is installed in a custom location, add it to PATH before running `artel`.",
                "Use non-interactive commands like `artel -p`, `artel init`, or "
                "`artel serve` outside cmux.",
            ],
        )

    if not workspace:
        return CmuxPreflightResult(
            ok=False,
            code="unsupported_environment",
            summary="Artel interactive mode must be launched inside a cmux workspace.",
            details=[f"Detected cmux binary: {binary}"],
            guidance=[
                "Start or attach to a cmux workspace, then launch `artel` from that terminal.",
                "Keep using explicit commands like `artel -p`, `artel init`, "
                "`artel serve`, `artel rpc`, or `artel acp` outside cmux.",
            ],
            binary_path=binary,
        )

    management_result = preflight_cmux_management(expected_capabilities=expected_capabilities)
    if not management_result.ok:
        management_result.workspace = workspace
        if not management_result.details:
            management_result.details = [f"Workspace: {workspace}"]
        elif not any(detail.startswith("Workspace:") for detail in management_result.details):
            management_result.details.insert(0, f"Workspace: {workspace}")
        return management_result

    return CmuxPreflightResult(
        ok=True,
        summary="cmux preflight passed.",
        binary_path=binary,
        workspace=workspace,
        socket_path=socket_path,
        available_capabilities=management_result.available_capabilities,
    )


def preflight_cmux_management(
    *,
    expected_capabilities: tuple[str, ...] = ("workspace", "surface"),
) -> CmuxPreflightResult:
    """Validate that cmux workspace/surface management can run outside a cmux shell.

    Unlike :func:`preflight_cmux`, this check does not require ``CMUX_WORKSPACE_ID``
    to be present. It is intended for utility flows that can reach the cmux daemon
    but do not inherit current-surface environment variables.
    """
    binary = _find_cmux()
    socket_path = cmux_socket_path()
    workspace = workspace_id().strip()

    if not binary:
        return CmuxPreflightResult(
            ok=False,
            code="binary_missing",
            summary="Artel cmux management requires the cmux binary, but it was not found.",
            guidance=[
                "Install cmux and make sure the `cmux` binary is on PATH.",
                "If cmux is installed in a custom location, add it to PATH before "
                "running cmux-backed commands.",
            ],
        )

    if not os.path.exists(socket_path):
        return CmuxPreflightResult(
            ok=False,
            code="socket_unavailable",
            summary=(
                "Artel found the cmux binary, but the cmux socket is unavailable "
                "for cmux management."
            ),
            details=[f"Expected socket: {socket_path}"],
            guidance=[
                "Make sure the cmux daemon is running before using cmux-backed commands.",
                "If your cmux socket lives elsewhere, export CMUX_SOCKET_PATH "
                "before running cmux-backed commands.",
                "CMUX_WORKSPACE_ID is not required here, but a reachable cmux daemon is required.",
            ],
            binary_path=binary,
            workspace=workspace,
            socket_path=socket_path,
        )

    if not is_cmux_socket_reachable(socket_path):
        return CmuxPreflightResult(
            ok=False,
            code="socket_unreachable",
            summary=(
                "Artel found the cmux socket path, but could not reach the cmux "
                "daemon for cmux management."
            ),
            details=[f"Socket: {socket_path}"],
            guidance=[
                "Restart or reattach cmux so the socket accepts connections.",
                "If your cmux socket changed, export CMUX_SOCKET_PATH before "
                "running cmux-backed commands.",
                "CMUX_WORKSPACE_ID is optional here; cmux daemon reachability is not.",
            ],
            binary_path=binary,
            workspace=workspace,
            socket_path=socket_path,
        )

    available_capabilities = tuple(sorted(probe_cmux_capabilities()))
    missing_capabilities = tuple(
        capability
        for capability in expected_capabilities
        if capability not in set(available_capabilities)
    )
    if missing_capabilities:
        return CmuxPreflightResult(
            ok=False,
            code="capabilities_missing",
            summary=(
                "Artel found cmux, but the available cmux CLI capabilities are "
                "incomplete for cmux management."
            ),
            details=[
                f"Socket: {socket_path}",
                f"Missing capabilities: {', '.join(missing_capabilities)}",
            ],
            guidance=[
                "Upgrade cmux to a build that supports workspace and surface commands.",
                "Re-run your cmux-backed command after upgrading or connecting to "
                "a compatible cmux runtime.",
            ],
            binary_path=binary,
            workspace=workspace,
            socket_path=socket_path,
            available_capabilities=available_capabilities,
            missing_capabilities=missing_capabilities,
        )

    return CmuxPreflightResult(
        ok=True,
        summary="cmux management preflight passed.",
        binary_path=binary,
        workspace=workspace,
        socket_path=socket_path,
        available_capabilities=available_capabilities,
    )


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
    except TimeoutError:
        logger.debug("cmux %s timed out", args[0])
        return ""
    except Exception as exc:
        logger.debug("cmux %s error: %s", args[0], exc)
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
    except Exception as exc:
        logger.debug("cmux sync %s error: %s", args[0], exc)
        return ""


def _parse_kv_line(line: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for token in str(line or "").strip().split():
        key, separator, value = token.partition("=")
        if not separator:
            continue
        normalized_key = key.strip().lower()
        normalized_value = value.strip().strip('"').strip("'")
        if normalized_key:
            data[normalized_key] = normalized_value
    return data


def _split_cmux_cli_line(line: str) -> list[str]:
    normalized = str(line or "").strip()
    if not normalized:
        return []
    try:
        return shlex.split(normalized)
    except ValueError:
        return normalized.split()


def _strip_bracket_suffix(token: str) -> str:
    normalized = str(token or "").strip()
    while normalized.endswith("]") and " [" in normalized:
        normalized = normalized.rsplit(" [", 1)[0].rstrip()
    return normalized


def parse_workspace_list(output: str) -> list[CmuxWorkspaceRecord]:
    """Parse best-effort cmux workspace listings into typed records."""
    records: list[CmuxWorkspaceRecord] = []
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        current = False
        if line.startswith("*"):
            current = True
            line = line[1:].strip()
        parts = _parse_kv_line(line)
        workspace_id = parts.get("id") or parts.get("workspace")
        name = parts.get("name") or parts.get("title")
        if not workspace_id and not name:
            tokens = _split_cmux_cli_line(_strip_bracket_suffix(line))
            if tokens:
                first = tokens[0].strip()
                if re.fullmatch(r"workspace:\d+", first):
                    workspace_id = first
                    name = " ".join(tokens[1:]).strip()
                elif ":" in line:
                    candidate_id, _separator, candidate_name = line.partition(":")
                    workspace_id = candidate_id.strip()
                    name = candidate_name.strip()
                else:
                    workspace_id = line
        records.append(
            CmuxWorkspaceRecord(
                id=workspace_id or "",
                name=name or "",
                current=current or parts.get("current", "").lower() in {"1", "true", "yes"},
                raw=raw_line,
            )
        )
    return records


def parse_surface_list(output: str) -> list[CmuxSurfaceRecord]:
    """Parse best-effort cmux surface listings into typed records."""
    records: list[CmuxSurfaceRecord] = []
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        current = False
        if line.startswith("*"):
            current = True
            line = line[1:].strip()
        parts = _parse_kv_line(line)
        surface_id = parts.get("id") or parts.get("surface")
        title = parts.get("title") or parts.get("name")
        workspace = parts.get("workspace")
        if not surface_id and not title:
            tokens = _split_cmux_cli_line(_strip_bracket_suffix(line))
            if tokens:
                first = tokens[0].strip()
                if re.fullmatch(r"surface:\d+", first):
                    surface_id = first
                    title = " ".join(tokens[1:]).strip()
                elif ":" in line:
                    candidate_id, _separator, candidate_title = line.partition(":")
                    surface_id = candidate_id.strip()
                    title = candidate_title.strip()
                else:
                    surface_id = line
        records.append(
            CmuxSurfaceRecord(
                id=surface_id or "",
                title=title or "",
                workspace=workspace or "",
                current=current or parts.get("current", "").lower() in {"1", "true", "yes"},
                raw=raw_line,
            )
        )
    return records


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
    source: str = "artel",
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


# ── Workspace / surface / pane management ────────────────────────


async def workspace_create(name: str = "") -> str:
    """Create a workspace and return the cmux response."""
    if not can_manage_cmux():
        return ""
    output = await _run(list(_CMUX_NEW_WORKSPACE))
    normalized = output.strip()
    if normalized.startswith("OK "):
        workspace_ref = normalized.split(None, 1)[1].strip()
    else:
        workspace_ref = normalized
    if workspace_ref and name.strip():
        await _run([*_CMUX_RENAME_WORKSPACE, "--workspace", workspace_ref, name.strip()])
    return workspace_ref


async def workspace_list() -> str:
    """List available workspaces."""
    if not can_manage_cmux():
        return ""
    return await _run(list(_CMUX_LIST_WORKSPACES))


async def workspace_list_records() -> list[CmuxWorkspaceRecord]:
    """List available workspaces as typed records."""
    return parse_workspace_list(await workspace_list())


async def workspace_select(target: str) -> str:
    """Select a workspace by id or name."""
    if not can_manage_cmux() or not target.strip():
        return ""
    return await _run([*_CMUX_SELECT_WORKSPACE, "--workspace", target.strip()])


async def surface_create(
    *,
    title: str = "",
    command: str = "",
    cwd: str = "",
    workspace: str = "",
) -> str:
    """Create a surface in the current or specified workspace."""
    if not can_manage_cmux():
        return ""
    args = list(_CMUX_NEW_SURFACE)
    if workspace:
        args.extend(["--workspace", workspace])
    output = await _run(args)
    normalized = output.strip()
    match = re.search(r"\b(surface:\d+)\b", normalized)
    surface_ref = match.group(1) if match else normalized
    if not surface_ref:
        return ""
    if title.strip():
        rename_args = [*_CMUX_RENAME_TAB, "--surface", surface_ref]
        if workspace:
            rename_args.extend(["--workspace", workspace])
        rename_args.extend(["--title", title.strip()])
        await _run(rename_args)
    if command.strip():
        text = command.strip()
        if cwd.strip():
            safe_cwd = cwd.replace("'", "'\"'\"'")
            text = f"cd '{safe_cwd}' && {command.strip()}"
        await _run(
            [
                "send",
                "--surface",
                surface_ref,
                *(["--workspace", workspace] if workspace else []),
                f"{text}\n",
            ]
        )
    return surface_ref


async def surface_list(*, workspace: str = "") -> str:
    """List surfaces in the current or specified workspace."""
    if not can_manage_cmux():
        return ""
    args = list(_CMUX_LIST_PANE_SURFACES)
    if workspace:
        args.extend(["--workspace", workspace])
    return await _run(args)


async def surface_list_records(*, workspace: str = "") -> list[CmuxSurfaceRecord]:
    """List surfaces in the current or specified workspace as typed records."""
    return parse_surface_list(await surface_list(workspace=workspace))


async def surface_focus(target: str) -> str:
    """Focus a surface by id or name."""
    if not can_manage_cmux() or not target.strip():
        return ""
    identified = await _run(["identify", "--surface", target.strip()])
    pane_match = re.search(r'"pane_ref"\s*:\s*"([^"]+)"', identified)
    pane_ref = pane_match.group(1) if pane_match else ""
    if pane_ref:
        return await _run([*_CMUX_FOCUS_PANE, "--pane", pane_ref])
    return ""


async def surface_rename(target: str, title: str) -> str:
    """Rename a surface by id or name."""
    if not can_manage_cmux() or not target.strip() or not title.strip():
        return ""
    args = [*_CMUX_RENAME_TAB, "--surface", target.strip(), "--title", title.strip()]
    return await _run(args)


async def ensure_workspace(name: str) -> CmuxWorkspaceRecord | None:
    """Return an existing workspace by name/id or create it if missing."""
    normalized = str(name or "").strip()
    if not normalized:
        return None
    for record in await workspace_list_records():
        if record.id == normalized or record.name == normalized:
            return record
    created = (await workspace_create(normalized)).strip()
    if created:
        return CmuxWorkspaceRecord(id=created, name=normalized)
    return None


async def ensure_surface(
    *,
    title: str,
    command: str = "",
    cwd: str = "",
    workspace: str = "",
) -> CmuxSurfaceRecord | None:
    """Return an existing surface by title/id or create it if missing."""
    normalized_title = str(title or "").strip()
    if not normalized_title:
        return None
    for record in await surface_list_records(workspace=workspace):
        if record.id == normalized_title or record.title == normalized_title:
            return record
    created = (
        await surface_create(
            title=normalized_title,
            command=command,
            cwd=cwd,
            workspace=workspace,
        )
    ).strip()
    if created:
        return CmuxSurfaceRecord(
            id=created,
            title=normalized_title,
            workspace=workspace,
        )
    return None


async def ensure_artel_workspace(
    *,
    workspace_name: str = DEFAULT_ARTEL_WORKSPACE_NAME,
) -> CmuxWorkspaceRecord | None:
    """Ensure the primary Artel cmux workspace exists."""
    return await ensure_workspace(workspace_name)


async def ensure_artel_dashboard_surface(
    *,
    workspace: str = DEFAULT_ARTEL_WORKSPACE_NAME,
    title: str = DEFAULT_ARTEL_DASHBOARD_SURFACE_TITLE,
    command: str = "artel web",
    cwd: str = "",
) -> CmuxSurfaceRecord | None:
    """Ensure the Artel dashboard surface exists in the target workspace."""
    return await ensure_surface(
        title=title,
        command=command,
        cwd=cwd,
        workspace=workspace,
    )


async def ensure_artel_orchestrator_surface(
    *,
    workspace: str = DEFAULT_ARTEL_WORKSPACE_NAME,
    title: str = DEFAULT_ARTEL_ORCHESTRATOR_SURFACE_TITLE,
    command: str = "artel",
    cwd: str = "",
) -> CmuxSurfaceRecord | None:
    """Ensure the Artel orchestrator surface exists in the target workspace."""
    return await ensure_surface(
        title=title,
        command=command,
        cwd=cwd,
        workspace=workspace,
    )


async def reuse_current_surface(
    *,
    title: str,
    workspace: str = "",
) -> CmuxSurfaceRecord | None:
    """Reuse the current cmux surface as a named Artel surface when available."""
    current_surface_id = surface_id().strip()
    if not current_surface_id:
        return None
    normalized_title = str(title or "").strip()
    if normalized_title:
        await surface_rename(current_surface_id, normalized_title)
    return CmuxSurfaceRecord(
        id=current_surface_id,
        title=normalized_title,
        workspace=workspace.strip() or workspace_id().strip(),
        current=True,
    )


async def bootstrap_artel_workspace(
    *,
    workspace_name: str = DEFAULT_ARTEL_WORKSPACE_NAME,
    dashboard_title: str = DEFAULT_ARTEL_DASHBOARD_SURFACE_TITLE,
    orchestrator_title: str = DEFAULT_ARTEL_ORCHESTRATOR_SURFACE_TITLE,
    dashboard_command: str = "artel web",
    orchestrator_command: str = "artel",
    cwd: str = "",
    reuse_current_for_orchestrator: bool = True,
) -> ArtelWorkspaceBootstrap:
    """Ensure the primary Artel workspace and core surfaces exist."""
    workspace_record = await ensure_artel_workspace(workspace_name=workspace_name)
    resolved_workspace = ""
    if workspace_record is not None:
        resolved_workspace = workspace_record.id or workspace_record.name or workspace_name
    else:
        resolved_workspace = workspace_name
    dashboard_record = await ensure_artel_dashboard_surface(
        workspace=resolved_workspace,
        title=dashboard_title,
        command=dashboard_command,
        cwd=cwd,
    )
    orchestrator_record: CmuxSurfaceRecord | None = None
    if reuse_current_for_orchestrator:
        current_workspace = workspace_id().strip()
        if current_workspace in (resolved_workspace, workspace_name):
            orchestrator_record = await reuse_current_surface(
                title=orchestrator_title,
                workspace=resolved_workspace,
            )
    if orchestrator_record is None:
        orchestrator_record = await ensure_artel_orchestrator_surface(
            workspace=resolved_workspace,
            title=orchestrator_title,
            command=orchestrator_command,
            cwd=cwd,
        )
    return ArtelWorkspaceBootstrap(
        workspace=workspace_record,
        dashboard=dashboard_record,
        orchestrator=orchestrator_record,
    )


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
