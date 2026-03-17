"""First-party Artel MCP config/store model with merged global and project scopes."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from artel_core.config import effective_global_mcp_path, effective_project_mcp_path

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


@dataclass(slots=True)
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    encoding: str = "utf-8"
    encoding_error_handler: str = "strict"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    sse_read_timeout: float = 300.0
    enabled: bool = True
    tool_prefix: str = ""
    include_tools: bool = True
    include_prompts: bool = True
    include_resources: bool = True
    roots: list[str] = field(default_factory=list)
    auth: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class MCPConfig:
    servers: list[MCPServerConfig] = field(default_factory=list)


@dataclass(slots=True)
class LoadedMCPConfig:
    servers: dict[str, MCPServerConfig]
    sources: list[Path]


class MCPRegistry:
    """Reads and writes Artel MCP config across global and project scopes."""

    def load_global_config(self) -> MCPConfig:
        return self._load_config_from_path(effective_global_mcp_path())

    def load_project_config(self, project_dir: str) -> MCPConfig:
        return self._load_config_from_path(
            effective_project_mcp_path(project_dir), project_dir=project_dir
        )

    def load_merged_config(self, project_dir: str) -> LoadedMCPConfig:
        paths: list[Path] = []
        merged: dict[str, dict[str, Any]] = {}

        global_path = effective_global_mcp_path()
        if global_path.exists():
            paths.append(global_path)
            self._merge_path(merged, global_path, base_dir=global_path.parent)

        if project_dir:
            project_path = effective_project_mcp_path(project_dir)
            if project_path.exists():
                paths.append(project_path)
                self._merge_path(merged, project_path, base_dir=project_path.parent)

        servers = {
            name: self._server_from_dict(name, payload)
            for name, payload in merged.items()
            if isinstance(payload, dict)
        }
        return LoadedMCPConfig(servers=servers, sources=paths)

    def write_global_config(self, config: MCPConfig) -> Path:
        target = effective_global_mcp_path()
        return self._write_config(target, config)

    def write_project_config(self, project_dir: str, config: MCPConfig) -> Path:
        path = effective_project_mcp_path(project_dir)
        target = Path(project_dir) / ".artel" / path.name if ".artel" in str(path) else path
        return self._write_config(target, config)

    def _load_config_from_path(self, path: Path, *, project_dir: str = "") -> MCPConfig:
        if not path.exists():
            return MCPConfig()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return MCPConfig()
        servers = self._raw_servers(data)
        if not isinstance(servers, dict):
            return MCPConfig()
        result: list[MCPServerConfig] = []
        for name, item in servers.items():
            if not isinstance(item, dict):
                continue
            base_dir = path.parent
            if project_dir and path == effective_project_mcp_path(project_dir):
                base_dir = path.parent
            resolved = _resolve_server_dict(item, base_dir=base_dir)
            try:
                result.append(self._server_from_dict(str(name).strip(), resolved))
            except ValueError:
                continue
        return MCPConfig(servers=result)

    def _merge_path(self, merged: dict[str, dict[str, Any]], path: Path, *, base_dir: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        raw_servers = self._raw_servers(data)
        if not isinstance(raw_servers, dict):
            return
        for server_name, server_value in raw_servers.items():
            if not isinstance(server_value, dict):
                continue
            resolved = _resolve_server_dict(server_value, base_dir=base_dir)
            current = merged.setdefault(str(server_name), {})
            _deep_merge(current, resolved)

    def _raw_servers(self, data: Any) -> dict[str, Any] | None:
        if not isinstance(data, dict):
            return None
        raw_servers = data.get("mcpServers")
        if isinstance(raw_servers, dict):
            return raw_servers
        servers_list = data.get("servers")
        if isinstance(servers_list, list):
            result: dict[str, Any] = {}
            for item in servers_list:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "") or "").strip()
                if not name:
                    continue
                payload = dict(item)
                payload.pop("name", None)
                result[name] = payload
            return result
        if isinstance(servers_list, dict):
            return servers_list
        return None

    def _server_from_dict(self, name: str, payload: dict[str, Any]) -> MCPServerConfig:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Missing server name")
        return MCPServerConfig(
            name=normalized_name,
            transport=str(payload.get("transport", "stdio") or "stdio"),
            command=str(payload.get("command", "") or "").strip(),
            args=[str(arg) for arg in payload.get("args", [])]
            if isinstance(payload.get("args"), list)
            else [],
            env={str(k): str(v) for k, v in payload.get("env", {}).items()}
            if isinstance(payload.get("env"), dict)
            else {},
            cwd=str(payload.get("cwd")) if payload.get("cwd") is not None else None,
            encoding=str(payload.get("encoding", "utf-8") or "utf-8"),
            encoding_error_handler=str(payload.get("encoding_error_handler", "strict") or "strict"),
            url=str(payload.get("url", "") or "").strip(),
            headers={str(k): str(v) for k, v in payload.get("headers", {}).items()}
            if isinstance(payload.get("headers"), dict)
            else {},
            timeout=float(payload.get("timeout", 30.0) or 30.0),
            sse_read_timeout=float(payload.get("sse_read_timeout", 300.0) or 300.0),
            enabled=bool(payload.get("enabled", True)),
            tool_prefix=str(payload.get("tool_prefix", "") or ""),
            include_tools=bool(payload.get("include_tools", True)),
            include_prompts=bool(payload.get("include_prompts", True)),
            include_resources=bool(payload.get("include_resources", True)),
            roots=[str(item) for item in payload.get("roots", [])]
            if isinstance(payload.get("roots"), list)
            else [],
            auth=dict(payload.get("auth", {})) if isinstance(payload.get("auth"), dict) else {},
        )

    def _write_config(self, target: Path, config: MCPConfig) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "servers": [
                {
                    "name": server.name,
                    "transport": server.transport,
                    "command": server.command,
                    "args": server.args,
                    "env": server.env,
                    "cwd": server.cwd,
                    "encoding": server.encoding,
                    "encoding_error_handler": server.encoding_error_handler,
                    "url": server.url,
                    "headers": server.headers,
                    "timeout": server.timeout,
                    "sse_read_timeout": server.sse_read_timeout,
                    "enabled": server.enabled,
                    "tool_prefix": server.tool_prefix,
                    "include_tools": server.include_tools,
                    "include_prompts": server.include_prompts,
                    "include_resources": server.include_resources,
                    "roots": server.roots,
                    "auth": server.auth,
                }
                for server in config.servers
            ]
        }
        target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return target


def _resolve_server_dict(raw_server: dict[str, Any], *, base_dir: Path) -> dict[str, Any]:
    resolved = _expand_value(raw_server)
    if not isinstance(resolved, dict):
        return {}

    transport = resolved.get("transport")
    if isinstance(transport, str):
        normalized_transport = transport.replace("-", "_").strip().lower()
        if normalized_transport in {"stdio", "streamable_http", "sse"}:
            resolved["transport"] = normalized_transport

    command = resolved.get("command")
    if isinstance(command, str):
        resolved["command"] = os.path.expanduser(command)

    cwd = resolved.get("cwd")
    if isinstance(cwd, str) and cwd:
        resolved["cwd"] = str(_resolve_path(base_dir, cwd))

    url = resolved.get("url")
    if isinstance(url, str):
        resolved["url"] = url.strip()

    roots = resolved.get("roots")
    if isinstance(roots, list):
        resolved["roots"] = [
            str(_resolve_path(base_dir, item)) for item in roots if isinstance(item, str)
        ]

    return resolved


def _expand_value(value: Any) -> Any:
    if isinstance(value, str):
        return _expand_string(value)
    if isinstance(value, list):
        return [_expand_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_value(item) for key, item in value.items()}
    return value


def _expand_string(value: str) -> str:
    expanded = os.path.expanduser(value)
    return _ENV_PATTERN.sub(lambda match: os.environ.get(match.group(1), ""), expanded)


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


__all__ = ["LoadedMCPConfig", "MCPConfig", "MCPRegistry", "MCPServerConfig"]
