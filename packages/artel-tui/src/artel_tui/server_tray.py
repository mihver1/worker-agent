"""macOS menu-bar companion for the managed local Artel server."""

from __future__ import annotations

import asyncio
import contextlib
import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from artel_core.config import CONFIG_DIR, project_state_dir

from artel_tui import local_server

_ARTEL_SERVER_TRAY_ACTIVE_ENV = "ARTEL_SERVER_TRAY_ACTIVE"
PROJECT_DIR_ENV = "ARTEL_SERVER_TRAY_PROJECT_DIR"
LAUNCH_AGENT_LABEL = "dev.artel.server-tray"


@dataclass(slots=True)
class LocalServerTrayHandle:
    project_dir: str
    label: str = LAUNCH_AGENT_LABEL
    plist_path: str = ""


def tray_registry_path(project_dir: str) -> Path:
    return project_state_dir(project_dir) / "server-tray.json"


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def launch_agent_plist_path() -> Path:
    return _launch_agents_dir() / f"{LAUNCH_AGENT_LABEL}.plist"


def _load_tray_registry(project_dir: str) -> LocalServerTrayHandle | None:
    path = tray_registry_path(project_dir)
    if not path.exists():
        return None
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    label = str(data.get("label", LAUNCH_AGENT_LABEL)).strip() or LAUNCH_AGENT_LABEL
    plist_path = str(data.get("plist_path", "")).strip()
    return LocalServerTrayHandle(project_dir=project_dir, label=label, plist_path=plist_path)


def _save_tray_registry(handle: LocalServerTrayHandle) -> None:
    import json

    path = tray_registry_path(handle.project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "project_dir": handle.project_dir,
                "label": handle.label,
                "plist_path": handle.plist_path,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _remove_tray_registry(project_dir: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        tray_registry_path(project_dir).unlink()


def _local_server_running(project_dir: str) -> bool:
    handle = local_server._load_registry(project_dir)
    if handle is None:
        return False
    pid = handle.pid
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def tray_command(project_dir: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "artel_core.cli",
        "server-tray",
        "--project-dir",
        project_dir,
    ]


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _write_launch_agent_plist(project_dir: str) -> Path:
    path = launch_agent_plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": tray_command(project_dir),
        "RunAtLoad": True,
        "KeepAlive": True,
        "EnvironmentVariables": {
            PROJECT_DIR_ENV: project_dir,
            _ARTEL_SERVER_TRAY_ACTIVE_ENV: "1",
        },
        "WorkingDirectory": project_dir,
        "StandardOutPath": str(CONFIG_DIR / "server-tray.out.log"),
        "StandardErrorPath": str(CONFIG_DIR / "server-tray.err.log"),
    }
    with path.open("wb") as fh:
        plistlib.dump(payload, fh)
    return path


def _launch_agent_bootstrapped() -> bool:
    result = _launchctl("print", f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}")
    return result.returncode == 0


def ensure_server_tray(project_dir: str) -> LocalServerTrayHandle | None:
    if sys.platform != "darwin":
        return None
    if os.environ.get(_ARTEL_SERVER_TRAY_ACTIVE_ENV) == "1":
        return None
    if not _local_server_running(project_dir):
        return None

    plist_path = _write_launch_agent_plist(project_dir)
    if not _launch_agent_bootstrapped():
        _launchctl("bootstrap", f"gui/{os.getuid()}", str(plist_path))
    _launchctl("kickstart", "-k", f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}")
    handle = LocalServerTrayHandle(
        project_dir=project_dir,
        label=LAUNCH_AGENT_LABEL,
        plist_path=str(plist_path),
    )
    _save_tray_registry(handle)
    return handle


def stop_server_tray(project_dir: str) -> None:
    if sys.platform != "darwin":
        return
    _launchctl("bootout", f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}")
    with contextlib.suppress(FileNotFoundError):
        launch_agent_plist_path().unlink()
    _remove_tray_registry(project_dir)


def _server_status_text(project_dir: str) -> str:
    pids = local_server._managed_server_processes(project_dir)
    handle = local_server._load_registry(project_dir)
    if not pids:
        return "Server: stopped"
    if len(pids) > 1:
        return f"Server: duplicate processes detected ({len(pids)})"
    if handle is None:
        return f"Server: running (pid {pids[0]})"
    pid = handle.pid or pids[0]
    return f"Server: running (pid {pid})"


def run_server_tray(project_dir: str = "") -> None:
    if sys.platform != "darwin":
        raise RuntimeError("Artel server tray is only available on macOS.")

    resolved_project_dir = str(
        Path(project_dir or os.environ.get(PROJECT_DIR_ENV, os.getcwd())).resolve()
    )
    os.environ[_ARTEL_SERVER_TRAY_ACTIVE_ENV] = "1"

    try:
        import rumps
    except Exception as exc:  # pragma: no cover - macOS dependency path
        raise RuntimeError("rumps is required for the Artel macOS server tray") from exc

    class ArtelServerTrayApp(rumps.App):
        def __init__(self) -> None:
            super().__init__("Artel", quit_button=None)
            self._project_dir = resolved_project_dir
            self._status_item = rumps.MenuItem(
                _server_status_text(self._project_dir), callback=None
            )
            self._status_item.set_callback(None)
            self.menu = [
                self._status_item,
                rumps.separator,
                rumps.MenuItem("Start server", callback=self._start_server),
                rumps.MenuItem("Stop server", callback=self._stop_server),
                rumps.MenuItem("Restart server", callback=self._restart_server),
                rumps.MenuItem("Clean duplicate servers", callback=self._clean_duplicate_servers),
                rumps.MenuItem("Open project folder", callback=self._open_project_folder),
                rumps.separator,
                rumps.MenuItem("Quit tray", callback=self._quit_tray),
            ]
            self._timer = rumps.Timer(self._refresh_status, 2.0)
            self._timer.start()

        def _refresh_status(self, _sender=None) -> None:
            self._status_item.title = _server_status_text(self._project_dir)

        def _start_server(self, _sender) -> None:
            if _local_server_running(self._project_dir):
                self._refresh_status()
                return
            asyncio.run(
                local_server.ensure_managed_local_server(self._project_dir, ensure_tray=False)
            )
            self._refresh_status()

        def _stop_server(self, _sender) -> None:
            asyncio.run(local_server.stop_managed_local_server(self._project_dir))
            self._refresh_status()

        def _restart_server(self, _sender) -> None:
            asyncio.run(
                local_server.restart_managed_local_server(self._project_dir, ensure_tray=False)
            )
            self._refresh_status()

        def _clean_duplicate_servers(self, _sender) -> None:
            local_server.cleanup_duplicate_managed_servers(self._project_dir)
            self._refresh_status()

        def _open_project_folder(self, _sender) -> None:
            subprocess.Popen(["open", self._project_dir])

        def _quit_tray(self, _sender) -> None:
            stop_server_tray(self._project_dir)
            rumps.quit_application()

    ArtelServerTrayApp().run()


__all__ = [
    "LAUNCH_AGENT_LABEL",
    "LocalServerTrayHandle",
    "PROJECT_DIR_ENV",
    "ensure_server_tray",
    "launch_agent_plist_path",
    "run_server_tray",
    "stop_server_tray",
    "tray_command",
    "tray_registry_path",
]
