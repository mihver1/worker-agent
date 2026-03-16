"""Shared control-plane helpers for Artel client surfaces.

This module is the first step towards a shared control layer used by TUI,
Web UI, and future desktop surfaces.
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote, urlsplit, urlunsplit

import httpx


class WorkerControl(Protocol):
    """Control-plane operations shared by interactive Artel clients.

    This initial protocol covers the HTTP control surface already exposed by
    ``artel-server``. Streaming chat transport and local-runtime control will
    be added incrementally on top of this foundation.
    """

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...

    async def list_providers(self) -> dict[str, Any]: ...

    async def list_models(self) -> dict[str, Any]: ...

    async def list_extensions(self) -> dict[str, Any]: ...

    async def install_extension(self, source: str) -> dict[str, Any]: ...

    async def remove_extension(self, name: str) -> dict[str, Any]: ...

    async def update_extension(self, name: str = "") -> dict[str, Any]: ...

    async def search_extensions(self, query: str) -> dict[str, Any]: ...

    async def list_extension_registries(self) -> dict[str, Any]: ...

    async def add_extension_registry(self, name: str, url: str) -> dict[str, Any]: ...

    async def remove_extension_registry(self, name: str) -> dict[str, Any]: ...

    async def get_health(self) -> dict[str, Any]: ...

    async def get_server_info(self) -> dict[str, Any]: ...

    async def get_config_paths(self) -> dict[str, Any]: ...

    async def get_effective_config(self) -> dict[str, Any]: ...

    async def get_server_diagnostics(self) -> dict[str, Any]: ...

    async def get_raw_config(self, scope: str) -> dict[str, Any]: ...

    async def init_config(self) -> dict[str, Any]: ...

    async def get_mcp_status(self) -> dict[str, Any]: ...

    async def reload_mcp(self) -> dict[str, Any]: ...

    async def get_mcp_config(self, scope: str = "effective") -> dict[str, Any]: ...

    async def put_mcp_config(
        self, *, scope: str, servers: list[dict[str, Any]]
    ) -> dict[str, Any]: ...

    async def upsert_mcp_server(self, *, scope: str, server: dict[str, Any]) -> dict[str, Any]: ...

    async def remove_mcp_server(self, *, scope: str, name: str) -> dict[str, Any]: ...

    async def list_sessions(self) -> dict[str, Any]: ...

    async def get_session(self, session_id: str) -> dict[str, Any]: ...

    async def get_session_messages(self, session_id: str) -> dict[str, Any]: ...

    async def get_session_tree(self, session_id: str) -> dict[str, Any]: ...

    async def list_prompts(self) -> dict[str, Any]: ...

    async def render_prompt(self, name: str, arg: str = "") -> dict[str, Any]: ...

    async def list_skills(self) -> dict[str, Any]: ...

    async def list_rules(self, *, project_dir: str = "") -> dict[str, Any]: ...

    async def add_rule(
        self,
        *,
        scope: str,
        text: str,
        enabled: bool = True,
        project_dir: str = "",
    ) -> dict[str, Any]: ...

    async def edit_rule(
        self,
        rule_id: str,
        *,
        text: str | None = None,
        scope: str | None = None,
        enabled: bool | None = None,
        project_dir: str = "",
    ) -> dict[str, Any]: ...

    async def move_rule(
        self,
        rule_id: str,
        *,
        project_dir: str = "",
        position: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]: ...

    async def delete_rule(self, rule_id: str, *, project_dir: str = "") -> dict[str, Any]: ...

    async def get_session_rule_overrides(self, session_id: str) -> dict[str, Any]: ...

    async def set_session_rule_enabled(
        self,
        session_id: str,
        rule_id: str,
        *,
        enabled: bool | None,
    ) -> dict[str, Any]: ...

    async def list_session_commands(self, session_id: str) -> dict[str, Any]: ...

    async def run_session_command(
        self,
        session_id: str,
        command_name: str,
        arg: str = "",
    ) -> dict[str, Any]: ...

    async def set_session_model(self, session_id: str, model: str) -> dict[str, Any]: ...

    async def set_session_title(self, session_id: str, title: str) -> dict[str, Any]: ...

    async def set_session_project(self, session_id: str, project_dir: str) -> dict[str, Any]: ...

    async def set_session_thinking(
        self,
        session_id: str,
        thinking_level: str,
    ) -> dict[str, Any]: ...

    async def get_session_tasks(self, session_id: str) -> dict[str, Any]: ...

    async def put_session_tasks(self, session_id: str, content: str) -> dict[str, Any]: ...

    async def get_session_notes(self, session_id: str) -> dict[str, Any]: ...

    async def put_session_notes(self, session_id: str, content: str) -> dict[str, Any]: ...

    async def compact_session(self, session_id: str, prompt: str = "") -> dict[str, Any]: ...

    async def fork_session(
        self,
        session_id: str,
        *,
        message_index: int | None = None,
    ) -> dict[str, Any]: ...

    async def inject_skill(self, session_id: str, skill: str) -> dict[str, Any]: ...

    async def reload_session(self, session_id: str) -> dict[str, Any]: ...

    async def run_bash(self, session_id: str, command: str) -> dict[str, Any]: ...

    async def import_credentials(self, providers: list[dict[str, Any]]) -> dict[str, Any]: ...

    async def start_oauth(
        self,
        provider: str,
        *,
        redirect_uri: str = "",
    ) -> dict[str, Any]: ...

    async def complete_oauth(
        self,
        login_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...

    async def list_schedules(self) -> dict[str, Any]: ...

    async def create_schedule(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    async def update_schedule(
        self, schedule_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...

    async def delete_schedule(self, schedule_id: str) -> dict[str, Any]: ...

    async def run_schedule(self, schedule_id: str) -> dict[str, Any]: ...

    async def reload_schedules(self) -> dict[str, Any]: ...


def remote_rest_base_url(remote_url: str) -> str:
    """Derive the REST sidecar base URL from a WebSocket URL."""

    parts = urlsplit(remote_url)
    if parts.scheme not in {"ws", "wss"}:
        raise ValueError(f"Unsupported remote URL scheme: {parts.scheme!r}")

    scheme = "https" if parts.scheme == "wss" else "http"
    default_port = 443 if scheme == "https" else 80
    normalized_path = parts.path.rstrip("/")
    if normalized_path:
        base_path = normalized_path[:-3] if normalized_path.endswith("/ws") else normalized_path
        rest_port = parts.port or default_port
    else:
        ws_port = parts.port or default_port
        rest_port = ws_port + 1
        base_path = ""
    host = parts.hostname or ""
    netloc = host if rest_port == default_port else f"{host}:{rest_port}"
    return urlunsplit((scheme, netloc, base_path, "", ""))


class RemoteWorkerControl:
    """HTTP client for the server-side Artel control plane."""

    def __init__(self, remote_url: str, auth_token: str = "") -> None:
        self.base_url = remote_rest_base_url(remote_url).rstrip("/")
        self.auth_token = auth_token

    def _headers(self) -> dict[str, str]:
        if not self.auth_token:
            return {}
        return {"Authorization": f"Bearer {self.auth_token}"}

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json_data,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = exc.response.text.strip()
                raise RuntimeError(detail or f"HTTP {exc.response.status_code}") from exc
        if not response.content:
            return {}
        return response.json()

    async def list_providers(self) -> dict[str, Any]:
        return await self.request("GET", "/api/providers")

    async def list_models(self) -> dict[str, Any]:
        return await self.request("GET", "/api/models")

    async def list_extensions(self) -> dict[str, Any]:
        return await self.request("GET", "/api/extensions")

    async def install_extension(self, source: str) -> dict[str, Any]:
        return await self.request("POST", "/api/extensions/install", json_data={"source": source})

    async def remove_extension(self, name: str) -> dict[str, Any]:
        return await self.request("DELETE", f"/api/extensions/{quote(name, safe='')}")

    async def update_extension(self, name: str = "") -> dict[str, Any]:
        if name.strip():
            return await self.request("POST", f"/api/extensions/{quote(name, safe='')}/update")
        return await self.request("POST", "/api/extensions/update")

    async def search_extensions(self, query: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/extensions/search?q={quote(query, safe='')}")

    async def list_extension_registries(self) -> dict[str, Any]:
        return await self.request("GET", "/api/extensions/registries")

    async def add_extension_registry(self, name: str, url: str) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/api/extensions/registries",
            json_data={"name": name, "url": url},
        )

    async def remove_extension_registry(self, name: str) -> dict[str, Any]:
        return await self.request("DELETE", f"/api/extensions/registries/{quote(name, safe='')}")

    async def get_health(self) -> dict[str, Any]:
        return await self.request("GET", "/api/health")

    async def get_server_info(self) -> dict[str, Any]:
        return await self.request("GET", "/api/server/info")

    async def get_config_paths(self) -> dict[str, Any]:
        return await self.request("GET", "/api/config/paths")

    async def get_effective_config(self) -> dict[str, Any]:
        return await self.request("GET", "/api/config/effective")

    async def get_server_diagnostics(self) -> dict[str, Any]:
        return await self.request("GET", "/api/server/diagnostics")

    async def get_raw_config(self, scope: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/config/raw?scope={quote(scope, safe='')}")

    async def init_config(self) -> dict[str, Any]:
        return await self.request("POST", "/api/config/init", json_data={})

    async def get_mcp_status(self) -> dict[str, Any]:
        return await self.request("GET", "/api/mcp")

    async def reload_mcp(self) -> dict[str, Any]:
        return await self.request("POST", "/api/mcp/reload", json_data={})

    async def get_mcp_config(self, scope: str = "effective") -> dict[str, Any]:
        return await self.request("GET", f"/api/mcp/config?scope={quote(scope, safe='')}")

    async def put_mcp_config(self, *, scope: str, servers: list[dict[str, Any]]) -> dict[str, Any]:
        return await self.request(
            "PUT", "/api/mcp/config", json_data={"scope": scope, "servers": servers}
        )

    async def upsert_mcp_server(self, *, scope: str, server: dict[str, Any]) -> dict[str, Any]:
        payload = await self.get_mcp_config(scope)
        current = payload.get("servers", [])
        current_values = list(current.values()) if isinstance(current, dict) else list(current)
        filtered = [
            item
            for item in current_values
            if isinstance(item, dict) and str(item.get("name", "")) != str(server.get("name", ""))
        ]
        filtered.append(server)
        return await self.put_mcp_config(scope=scope, servers=filtered)

    async def remove_mcp_server(self, *, scope: str, name: str) -> dict[str, Any]:
        payload = await self.get_mcp_config(scope)
        current = payload.get("servers", [])
        current_values = list(current.values()) if isinstance(current, dict) else list(current)
        filtered = [
            item
            for item in current_values
            if isinstance(item, dict) and str(item.get("name", "")) != name
        ]
        return await self.put_mcp_config(scope=scope, servers=filtered)

    async def list_sessions(self) -> dict[str, Any]:
        return await self.request("GET", "/api/sessions")

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/sessions/{session_id}")

    async def get_session_messages(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/sessions/{session_id}/messages")

    async def get_session_tree(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/sessions/{session_id}/tree")

    async def list_prompts(self) -> dict[str, Any]:
        return await self.request("GET", "/api/prompts")

    async def render_prompt(self, name: str, arg: str = "") -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/api/prompts/{quote(name, safe='')}/render",
            json_data={"arg": arg},
        )

    async def list_skills(self) -> dict[str, Any]:
        return await self.request("GET", "/api/skills")

    async def list_rules(self, *, project_dir: str = "") -> dict[str, Any]:
        suffix = f"?project_dir={quote(project_dir, safe='')}" if project_dir.strip() else ""
        return await self.request("GET", f"/api/rules{suffix}")

    async def add_rule(
        self,
        *,
        scope: str,
        text: str,
        enabled: bool = True,
        project_dir: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"scope": scope, "text": text, "enabled": enabled}
        if project_dir.strip():
            payload["project_dir"] = project_dir
        return await self.request("POST", "/api/rules", json_data=payload)

    async def edit_rule(
        self,
        rule_id: str,
        *,
        text: str | None = None,
        scope: str | None = None,
        enabled: bool | None = None,
        project_dir: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if text is not None:
            payload["text"] = text
        if scope is not None:
            payload["scope"] = scope
        if enabled is not None:
            payload["enabled"] = enabled
        if project_dir.strip():
            payload["project_dir"] = project_dir
        return await self.request("PUT", f"/api/rules/{quote(rule_id, safe='')}", json_data=payload)

    async def move_rule(
        self,
        rule_id: str,
        *,
        project_dir: str = "",
        position: int | None = None,
        offset: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if project_dir.strip():
            payload["project_dir"] = project_dir
        if position is not None:
            payload["position"] = position
        if offset is not None:
            payload["offset"] = offset
        return await self.request(
            "POST", f"/api/rules/{quote(rule_id, safe='')}/move", json_data=payload
        )

    async def delete_rule(self, rule_id: str, *, project_dir: str = "") -> dict[str, Any]:
        suffix = f"?project_dir={quote(project_dir, safe='')}" if project_dir.strip() else ""
        return await self.request("DELETE", f"/api/rules/{quote(rule_id, safe='')}{suffix}")

    async def get_session_rule_overrides(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/sessions/{session_id}/rules")

    async def set_session_rule_enabled(
        self,
        session_id: str,
        rule_id: str,
        *,
        enabled: bool | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"enabled": enabled}
        return await self.request(
            "PUT",
            f"/api/sessions/{session_id}/rules/{quote(rule_id, safe='')}",
            json_data=payload,
        )

    async def list_session_commands(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/sessions/{session_id}/commands")

    async def run_session_command(
        self,
        session_id: str,
        command_name: str,
        arg: str = "",
    ) -> dict[str, Any]:
        encoded_name = quote(command_name, safe="")
        return await self.request(
            "POST",
            f"/api/sessions/{session_id}/commands/{encoded_name}",
            json_data={"arg": arg},
        )

    async def set_session_model(self, session_id: str, model: str) -> dict[str, Any]:
        return await self.request(
            "PUT",
            f"/api/sessions/{session_id}/model",
            json_data={"model": model},
        )

    async def set_session_title(self, session_id: str, title: str) -> dict[str, Any]:
        return await self.request(
            "PUT",
            f"/api/sessions/{session_id}/title",
            json_data={"title": title},
        )

    async def set_session_project(self, session_id: str, project_dir: str) -> dict[str, Any]:
        return await self.request(
            "PUT",
            f"/api/sessions/{session_id}/project",
            json_data={"project_dir": project_dir},
        )

    async def set_session_thinking(
        self,
        session_id: str,
        thinking_level: str,
    ) -> dict[str, Any]:
        return await self.request(
            "PUT",
            f"/api/sessions/{session_id}/thinking",
            json_data={"thinking_level": thinking_level},
        )

    async def get_session_tasks(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/sessions/{session_id}/tasks")

    async def put_session_tasks(self, session_id: str, content: str) -> dict[str, Any]:
        return await self.request(
            "PUT",
            f"/api/sessions/{session_id}/tasks",
            json_data={"content": content},
        )

    async def get_session_notes(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/sessions/{session_id}/notes")

    async def put_session_notes(self, session_id: str, content: str) -> dict[str, Any]:
        return await self.request(
            "PUT",
            f"/api/sessions/{session_id}/notes",
            json_data={"content": content},
        )

    async def compact_session(self, session_id: str, prompt: str = "") -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/api/sessions/{session_id}/compact",
            json_data={"prompt": prompt},
        )

    async def fork_session(
        self,
        session_id: str,
        *,
        message_index: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if message_index is not None:
            payload["message_index"] = message_index
        return await self.request(
            "POST",
            f"/api/sessions/{session_id}/fork",
            json_data=payload,
        )

    async def inject_skill(self, session_id: str, skill: str) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/api/sessions/{session_id}/skill",
            json_data={"skill": skill},
        )

    async def reload_session(self, session_id: str) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/api/sessions/{session_id}/reload",
            json_data={},
        )

    async def run_bash(self, session_id: str, command: str) -> dict[str, Any]:
        return await self.request(
            "POST",
            f"/api/sessions/{session_id}/bash",
            json_data={"command": command},
        )

    async def import_credentials(self, providers: list[dict[str, Any]]) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/api/credentials/import",
            json_data={"providers": providers},
        )

    async def start_oauth(
        self,
        provider: str,
        *,
        redirect_uri: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"provider": provider}
        if redirect_uri:
            payload["redirect_uri"] = redirect_uri
        return await self.request("POST", "/api/oauth/start", json_data=payload)

    async def complete_oauth(
        self,
        login_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self.request(
            "POST",
            "/api/oauth/complete",
            json_data={"login_id": login_id, "payload": payload},
        )

    async def list_schedules(self) -> dict[str, Any]:
        return await self.request("GET", "/api/schedules")

    async def create_schedule(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", "/api/schedules", json_data=payload)

    async def update_schedule(self, schedule_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self.request(
            "PUT", f"/api/schedules/{quote(schedule_id, safe='')}", json_data=payload
        )

    async def delete_schedule(self, schedule_id: str) -> dict[str, Any]:
        return await self.request("DELETE", f"/api/schedules/{quote(schedule_id, safe='')}")

    async def run_schedule(self, schedule_id: str) -> dict[str, Any]:
        return await self.request(
            "POST", f"/api/schedules/{quote(schedule_id, safe='')}/run", json_data={}
        )

    async def reload_schedules(self) -> dict[str, Any]:
        return await self.request("POST", "/api/schedules/reload", json_data={})


ArtelControl = WorkerControl
RemoteArtelControl = RemoteWorkerControl
RemoteControlClient = RemoteWorkerControl

__all__ = [
    "ArtelControl",
    "RemoteArtelControl",
    "RemoteControlClient",
    "RemoteWorkerControl",
    "WorkerControl",
    "remote_rest_base_url",
]
