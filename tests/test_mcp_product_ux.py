from __future__ import annotations

import json

import pytest


def test_mcp_cli_status_and_reload(monkeypatch, tmp_path):
    from click.testing import CliRunner
    from worker_core import cli as cli_mod

    class _Runtime:
        available = True

        async def load(self, context):
            self.context = context

        async def reload(self):
            return None

        async def close(self):
            return None

        def status_text(self):
            return (
                "Connected servers:\n"
                "- demo [streamable_http] state=connected tools=1 "
                "prompts=0 resources=0 templates=0"
            )

        def status_payload(self):
            return {
                "available": True,
                "sources": ["/tmp/demo-mcp.json"],
                "summary": {
                    "connected": 1,
                    "disabled": 0,
                    "failed": 0,
                    "needs_auth": 0,
                    "timeout": 0,
                    "unavailable": 0,
                    "total": 1,
                },
                "servers": [
                    {
                        "name": "demo",
                        "state": "connected",
                        "transport": "streamable_http",
                        "enabled": True,
                        "source": "https://example.test/mcp",
                        "endpoint": "https://example.test/mcp",
                        "tool_prefix": "demo__",
                        "include_tools": True,
                        "include_prompts": True,
                        "include_resources": True,
                        "tools": 1,
                        "prompts": 0,
                        "resources": 0,
                        "templates": 0,
                        "error": "",
                    }
                ],
            }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("worker_core.mcp_runtime.McpRuntimeManager", _Runtime)

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["mcp", "status"])
    assert result.exit_code == 0
    assert "state=connected" in result.output

    result = runner.invoke(cli_mod.cli, ["mcp", "status", "--json-output"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["summary"]["connected"] == 1
    assert payload["servers"][0]["state"] == "connected"

    result = runner.invoke(cli_mod.cli, ["mcp", "reload", "--json-output"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["servers"][0]["name"] == "demo"


@pytest.mark.asyncio
async def test_mcp_server_config_endpoints_support_server_upsert_and_delete(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer
    from worker_core.config import WorkerConfig
    from worker_server.server import ServerState, _create_rest_app

    state = ServerState(config=WorkerConfig(), default_project_dir=str(tmp_path))
    app = _create_rest_app(state, "test_token")
    async with TestClient(TestServer(app)) as client:
        resp = await client.put(
            "/api/mcp/servers/demo?scope=project",
            headers={"Authorization": "Bearer test_token"},
            json={
                "transport": "streamable_http",
                "url": "https://context7.liam.sh/mcp",
                "tool_prefix": "ctx7__",
            },
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["server"]["name"] == "demo"
        assert data["server"]["tool_prefix"] == "ctx7__"

        resp = await client.get(
            "/api/mcp/config?scope=project",
            headers={"Authorization": "Bearer test_token"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["servers"][0]["name"] == "demo"

        resp = await client.delete(
            "/api/mcp/servers/demo?scope=project",
            headers={"Authorization": "Bearer test_token"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["deleted"] == "demo"

        resp = await client.get(
            "/api/mcp/config?scope=project",
            headers={"Authorization": "Bearer test_token"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["servers"] == []


@pytest.mark.asyncio
async def test_tui_mcp_command_remote_uses_control_plane(monkeypatch):
    from worker_tui.app import WorkerApp

    class _RemoteClient:
        async def request(self, method: str, path: str, *, json_data=None):
            status = (
                "Connected servers:\n"
                "- context7 [streamable_http] state=connected tools=3 "
                "prompts=0 resources=0 templates=0"
            )
            if method == "GET" and path == "/api/mcp":
                return {"status": status}
            if method == "POST" and path == "/api/mcp/reload":
                return {"status": status}
            raise AssertionError((method, path, json_data))

    app = WorkerApp(remote_url="ws://localhost:7432")
    app._remote_control_client = _RemoteClient()
    seen_messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant": seen_messages.append((content, role))  # type: ignore[method-assign]

    await app._cmd_mcp("")
    await app._cmd_mcp("reload")

    assert seen_messages == [
        (
            "Connected servers:\n"
            "- context7 [streamable_http] state=connected tools=3 "
            "prompts=0 resources=0 templates=0",
            "tool",
        ),
        (
            "Connected servers:\n"
            "- context7 [streamable_http] state=connected tools=3 "
            "prompts=0 resources=0 templates=0",
            "tool",
        ),
    ]
