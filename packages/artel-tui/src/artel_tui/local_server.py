"""Managed local Artel server lifecycle helpers for the default TUI flow."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from artel_core.config import (
    effective_project_server_registry_path,
    load_config,
    project_server_registry_path,
)

from artel_tui.remote_control import RemoteControlClient

_LOCAL_SERVER_HOST = "127.0.0.1"
_POLL_INTERVAL_SECONDS = 0.1
_START_TIMEOUT_SECONDS = 10.0
_LOCAL_SERVER_LOCKS: dict[str, asyncio.Lock] = {}
_TRAY_ENSURE_THREADS: set[str] = set()
_TRAY_ENSURE_THREADS_LOCK = threading.Lock()


@dataclass(slots=True)
class LocalServerHandle:
    remote_url: str
    auth_token: str
    project_dir: str
    pid: int | None = None


def managed_server_registry_path(project_dir: str) -> Path:
    return project_server_registry_path(project_dir)


def _load_registry(project_dir: str) -> LocalServerHandle | None:
    path = effective_project_server_registry_path(project_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    remote_url = str(data.get("remote_url", "")).strip()
    auth_token = str(data.get("auth_token", "")).strip()
    if not remote_url or not auth_token:
        return None
    pid_value = data.get("pid")
    pid = pid_value if isinstance(pid_value, int) else None
    return LocalServerHandle(
        remote_url=remote_url,
        auth_token=auth_token,
        project_dir=project_dir,
        pid=pid,
    )


def _save_registry(handle: LocalServerHandle) -> None:
    path = managed_server_registry_path(handle.project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(handle), indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _remove_registry(project_dir: str) -> None:
    path = managed_server_registry_path(project_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _pick_port(preferred_port: int) -> int:
    for candidate in (preferred_port, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((_LOCAL_SERVER_HOST, candidate))
            except OSError:
                continue
            return int(sock.getsockname()[1])
    raise RuntimeError("Failed to allocate a local Artel server port")


async def _server_supports_required_capabilities(client: RemoteControlClient) -> bool:
    try:
        await client.get_config_paths()
        await client.get_effective_config()
    except Exception:
        return False
    return True


async def _server_matches_project(handle: LocalServerHandle) -> bool:
    client = RemoteControlClient(handle.remote_url, auth_token=handle.auth_token)
    try:
        payload = await client.get_server_info()
    except Exception:
        return False
    reported_dir = str(payload.get("project_dir", "")).strip()
    if reported_dir:
        try:
            if Path(reported_dir).resolve() != Path(handle.project_dir).resolve():
                return False
        except OSError:
            if reported_dir != handle.project_dir:
                return False
    return await _server_supports_required_capabilities(client)


async def _wait_until_ready(
    handle: LocalServerHandle,
    process: subprocess.Popen[str],
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _START_TIMEOUT_SECONDS
    while loop.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Managed local Artel server exited with code {process.returncode}")
        if await _server_matches_project(handle):
            return
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    raise RuntimeError("Timed out while starting the managed local Artel server")


def _server_command(port: int, token: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "artel_core.cli",
        "serve",
        "--host",
        _LOCAL_SERVER_HOST,
        "--port",
        str(port),
        "--token",
        token,
    ]


def _managed_server_processes(project_dir: str) -> list[int]:
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return []
    token_pattern = re.compile(r"\bartel_core\.cli\s+serve\b")
    pids: list[int] = []
    normalized_project = str(Path(project_dir).resolve())
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) != 2:
            continue
        pid_text, command = parts
        if not pid_text.isdigit():
            continue
        if not token_pattern.search(command):
            continue
        pid = int(pid_text)
        try:
            cwd = os.path.realpath(os.readlink(f"/proc/{pid}/cwd"))
        except Exception:
            cwd = ""
        if not cwd and sys.platform == "darwin":
            try:
                proc = subprocess.run(
                    ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                for row in proc.stdout.splitlines():
                    if row.startswith("n"):
                        cwd = row[1:]
                        break
            except Exception:
                cwd = ""
        if cwd:
            try:
                if Path(cwd).resolve() != Path(normalized_project):
                    continue
            except Exception:
                if cwd != normalized_project:
                    continue
        pids.append(pid)
    return pids


def _kill_managed_server_processes(
    project_dir: str, *, include_pid: int | None = None
) -> list[int]:
    killed: list[int] = []
    candidate_pids = set(_managed_server_processes(project_dir))
    if include_pid is not None and include_pid > 0:
        candidate_pids.add(include_pid)
    for pid in sorted(candidate_pids):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
    return killed


def cleanup_duplicate_managed_servers(project_dir: str) -> list[int]:
    resolved_project_dir = str(Path(project_dir).resolve())
    pids = sorted(set(_managed_server_processes(resolved_project_dir)))
    if len(pids) <= 1:
        return []
    registry = _load_registry(resolved_project_dir)
    keep_pid = registry.pid if registry is not None and registry.pid in pids else pids[0]
    killed: list[int] = []
    for pid in pids:
        if pid == keep_pid:
            continue
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
    return killed


def _spawn_server_tray_ensure(project_dir: str) -> None:
    if sys.platform != "darwin":
        return
    with _TRAY_ENSURE_THREADS_LOCK:
        if project_dir in _TRAY_ENSURE_THREADS:
            return
        _TRAY_ENSURE_THREADS.add(project_dir)

    def _runner() -> None:
        try:
            from artel_tui.server_tray import ensure_server_tray

            with contextlib.suppress(Exception):
                ensure_server_tray(project_dir)
        finally:
            with _TRAY_ENSURE_THREADS_LOCK:
                _TRAY_ENSURE_THREADS.discard(project_dir)

    thread = threading.Thread(
        target=_runner,
        name=f"artel-server-tray-{abs(hash(project_dir))}",
        daemon=True,
    )
    thread.start()


async def ensure_managed_local_server(
    project_dir: str | None = None,
    *,
    ensure_tray: bool = True,
) -> LocalServerHandle:
    resolved_project_dir = str(Path(project_dir or os.getcwd()).resolve())
    lock = _LOCAL_SERVER_LOCKS.setdefault(resolved_project_dir, asyncio.Lock())
    async with lock:
        existing = _load_registry(resolved_project_dir)
        if existing is not None and await _server_matches_project(existing):
            if ensure_tray and sys.platform == "darwin":
                _spawn_server_tray_ensure(resolved_project_dir)
            return existing

        config = load_config(resolved_project_dir)
        auth_token = config.server.auth_token.strip() or f"artel_{secrets.token_hex(16)}"
        port = _pick_port(config.server.port)
        handle = LocalServerHandle(
            remote_url=f"ws://{_LOCAL_SERVER_HOST}:{port}",
            auth_token=auth_token,
            project_dir=resolved_project_dir,
        )

        process = subprocess.Popen(
            _server_command(port, auth_token),
            cwd=resolved_project_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ.copy(),
            text=True,
        )
        handle.pid = process.pid
        await _wait_until_ready(handle, process)
        _save_registry(handle)
        if ensure_tray and sys.platform == "darwin":
            _spawn_server_tray_ensure(resolved_project_dir)
        return handle


async def stop_managed_local_server(project_dir: str | None = None) -> None:
    resolved_project_dir = str(Path(project_dir or os.getcwd()).resolve())
    existing = _load_registry(resolved_project_dir)
    include_pid = existing.pid if existing is not None else None
    _kill_managed_server_processes(resolved_project_dir, include_pid=include_pid)
    _remove_registry(resolved_project_dir)


async def restart_managed_local_server(
    project_dir: str | None = None,
    *,
    ensure_tray: bool = True,
) -> LocalServerHandle:
    resolved_project_dir = str(Path(project_dir or os.getcwd()).resolve())
    existing = _load_registry(resolved_project_dir)
    include_pid = existing.pid if existing is not None else None
    _kill_managed_server_processes(resolved_project_dir, include_pid=include_pid)
    _remove_registry(resolved_project_dir)
    return await ensure_managed_local_server(resolved_project_dir, ensure_tray=ensure_tray)
