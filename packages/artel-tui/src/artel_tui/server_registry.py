"""Persistent registry of Artel servers for the TUI server dock."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from artel_core import config as config_mod


@dataclass(slots=True)
class SavedArtelServer:
    """A saved remote Artel server connection."""

    name: str
    remote_url: str
    auth_token: str = ""

    @classmethod
    def from_dict(cls, payload: object) -> SavedArtelServer | None:
        if not isinstance(payload, dict):
            return None
        remote_url = str(payload.get("remote_url", "")).strip()
        if not remote_url:
            return None
        name = str(payload.get("name", "")).strip() or default_server_name(remote_url)
        auth_token = str(payload.get("auth_token", ""))
        return cls(name=name, remote_url=remote_url, auth_token=auth_token)

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def server_registry_path() -> Path:
    return config_mod.CONFIG_DIR / "servers.json"


def default_server_name(remote_url: str) -> str:
    parts = urlsplit(remote_url.replace("ws://", "http://", 1).replace("wss://", "https://", 1))
    host = (parts.hostname or "").strip()
    if host:
        port = f":{parts.port}" if parts.port else ""
        return f"Artel @ {host}{port}"
    return remote_url.strip() or "Artel server"


def load_saved_servers() -> list[SavedArtelServer]:
    path = server_registry_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    servers: list[SavedArtelServer] = []
    for item in payload:
        server = SavedArtelServer.from_dict(item)
        if server is not None:
            servers.append(server)
    return servers


def save_saved_servers(servers: list[SavedArtelServer]) -> Path:
    path = server_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    deduped: dict[str, SavedArtelServer] = {}
    for server in servers:
        deduped[server.remote_url] = SavedArtelServer(
            name=server.name.strip() or default_server_name(server.remote_url),
            remote_url=server.remote_url.strip(),
            auth_token=server.auth_token,
        )
    payload = [
        server.to_dict()
        for server in sorted(deduped.values(), key=lambda item: item.name.lower())
    ]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def upsert_saved_server(server: SavedArtelServer) -> list[SavedArtelServer]:
    current = {item.remote_url: item for item in load_saved_servers()}
    current[server.remote_url] = SavedArtelServer(
        name=server.name.strip() or default_server_name(server.remote_url),
        remote_url=server.remote_url.strip(),
        auth_token=server.auth_token,
    )
    result = sorted(current.values(), key=lambda item: item.name.lower())
    save_saved_servers(result)
    return result


def remove_saved_server(remote_url: str) -> list[SavedArtelServer]:
    needle = remote_url.strip()
    result = [server for server in load_saved_servers() if server.remote_url != needle]
    save_saved_servers(result)
    return result


__all__ = [
    "SavedArtelServer",
    "default_server_name",
    "load_saved_servers",
    "remove_saved_server",
    "save_saved_servers",
    "server_registry_path",
    "upsert_saved_server",
]
