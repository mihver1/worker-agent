"""Remote control helpers for the Worker TUI."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx


def remote_rest_base_url(remote_url: str) -> str:
    """Derive the REST sidecar base URL from a WebSocket URL."""

    parts = urlsplit(remote_url)
    if parts.scheme not in {"ws", "wss"}:
        raise ValueError(f"Unsupported remote URL scheme: {parts.scheme!r}")

    scheme = "https" if parts.scheme == "wss" else "http"
    default_port = 443 if parts.scheme == "wss" else 80
    ws_port = parts.port or default_port
    rest_port = ws_port + 1
    host = parts.hostname or ""
    netloc = host if rest_port == default_port else f"{host}:{rest_port}"
    return urlunsplit((scheme, netloc, "", "", ""))


class RemoteControlClient:
    """HTTP client for the server-side control plane used in remote mode."""

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

    async def list_sessions(self) -> dict[str, Any]:
        return await self.request("GET", "/api/sessions")

    async def get_session(self, session_id: str) -> dict[str, Any]:
        return await self.request("GET", f"/api/sessions/{session_id}")

    async def set_session_model(self, session_id: str, model: str) -> dict[str, Any]:
        return await self.request(
            "PUT",
            f"/api/sessions/{session_id}/model",
            json_data={"model": model},
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
