"""Managed local Worker server lifecycle helpers for the default TUI flow."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from worker_core.config import load_config

from worker_tui.remote_control import RemoteControlClient

_LOCAL_SERVER_HOST = "127.0.0.1"
_POLL_INTERVAL_SECONDS = 0.1
_START_TIMEOUT_SECONDS = 10.0


@dataclass(slots=True)
class LocalServerHandle:
    remote_url: str
    auth_token: str
    project_dir: str
    pid: int | None = None


def managed_server_registry_path(project_dir: str) -> Path:
    return Path(project_dir) / ".worker" / "server.json"


def _load_registry(project_dir: str) -> LocalServerHandle | None:
    path = managed_server_registry_path(project_dir)
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


def _pick_port(preferred_port: int) -> int:
    for candidate in (preferred_port, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((_LOCAL_SERVER_HOST, candidate))
            except OSError:
                continue
            return int(sock.getsockname()[1])
    raise RuntimeError("Failed to allocate a local Worker server port")


async def _server_matches_project(handle: LocalServerHandle) -> bool:
    client = RemoteControlClient(handle.remote_url, auth_token=handle.auth_token)
    try:
        payload = await client.get_server_info()
    except Exception:
        return False
    reported_dir = str(payload.get("project_dir", "")).strip()
    if not reported_dir:
        return True
    try:
        return Path(reported_dir).resolve() == Path(handle.project_dir).resolve()
    except OSError:
        return reported_dir == handle.project_dir


async def _wait_until_ready(
    handle: LocalServerHandle,
    process: subprocess.Popen[str],
) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _START_TIMEOUT_SECONDS
    while loop.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Managed local Worker server exited with code {process.returncode}"
            )
        if await _server_matches_project(handle):
            return
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    raise RuntimeError("Timed out while starting the managed local Worker server")


def _server_command(port: int, token: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "worker_core.cli",
        "serve",
        "--host",
        _LOCAL_SERVER_HOST,
        "--port",
        str(port),
        "--token",
        token,
    ]


async def ensure_managed_local_server(project_dir: str | None = None) -> LocalServerHandle:
    resolved_project_dir = str(Path(project_dir or os.getcwd()).resolve())
    existing = _load_registry(resolved_project_dir)
    if existing is not None and await _server_matches_project(existing):
        return existing

    config = load_config(resolved_project_dir)
    auth_token = config.server.auth_token.strip() or f"wkr_{secrets.token_hex(16)}"
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
    return handle
