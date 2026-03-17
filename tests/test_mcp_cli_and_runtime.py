from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import httpx
import pytest


def _server_script() -> str:
    return str(
        Path(__file__).resolve().parents[2]
        / "artel-ext-mcp"
        / "tests"
        / "fixtures"
        / "dummy_mcp_server.py"
    )


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_for_port(port: int, *, host: str = "127.0.0.1", timeout: float = 10.0) -> None:
    import asyncio

    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError as exc:
            if asyncio.get_event_loop().time() >= deadline:
                raise TimeoutError(f"Timed out waiting for {host}:{port}") from exc
            await asyncio.sleep(0.1)


def test_mcp_cli_show_and_set_and_remove(tmp_path, monkeypatch):
    import artel_core.config as cfg_mod
    from artel_core import cli as cli_mod
    from click.testing import CliRunner

    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".artel").mkdir()
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", global_dir)
    monkeypatch.setattr(cfg_mod, "GLOBAL_MCP_PATH", global_dir / "mcp.json")
    monkeypatch.setattr(cfg_mod, "LEGACY_GLOBAL_MCP_PATH", global_dir / "legacy-mcp.json")

    runner = CliRunner()
    result = runner.invoke(
        cli_mod.cli,
        [
            "mcp",
            "set",
            "demo",
            "--scope",
            "global",
            "--transport",
            "stdio",
            "--command",
            "python3",
            "--arg",
            "server.py",
            "--tool-prefix",
            "demo__",
        ],
    )
    assert result.exit_code == 0

    result = runner.invoke(cli_mod.cli, ["mcp", "show", "--scope", "global"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["servers"][0]["name"] == "demo"
    assert payload["servers"][0]["tool_prefix"] == "demo__"

    result = runner.invoke(cli_mod.cli, ["mcp", "remove", "demo", "--scope", "global"])
    assert result.exit_code == 0
    result = runner.invoke(cli_mod.cli, ["mcp", "show", "--scope", "global"])
    payload = json.loads(result.output)
    assert payload["servers"] == []


@pytest.mark.asyncio
async def test_mcp_runtime_loads_streamable_http_server_from_merged_store(tmp_path, monkeypatch):
    mcp = pytest.importorskip("mcp")
    del mcp
    from artel_core.config import ArtelConfig
    from artel_core.extensions import ExtensionContext
    from artel_core.mcp_runtime import McpRuntimeManager

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".artel").mkdir()
    global_dir = tmp_path / "global"
    global_dir.mkdir()

    import artel_core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", global_dir)
    monkeypatch.setattr(cfg_mod, "GLOBAL_MCP_PATH", global_dir / "mcp.json")
    monkeypatch.setattr(cfg_mod, "LEGACY_GLOBAL_MCP_PATH", global_dir / "legacy-mcp.json")

    port = _free_tcp_port()
    env = dict(os.environ)
    env["DUMMY_MCP_PORT"] = str(port)
    proc = await __import__("asyncio").create_subprocess_exec(
        "uv",
        "run",
        "python",
        _server_script(),
        "streamable_http",
        env=env,
        stdout=__import__("asyncio").subprocess.PIPE,
        stderr=__import__("asyncio").subprocess.PIPE,
    )

    try:
        await _wait_for_port(port)
        (global_dir / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "remote": {
                            "transport": "streamable_http",
                            "url": f"http://127.0.0.1:{port}/mcp",
                            "tool_prefix": "remote__",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        runtime = McpRuntimeManager()
        await runtime.load(
            ExtensionContext(project_dir=str(project_dir), runtime="local", config=ArtelConfig())
        )
        try:
            tools = {tool.name: tool for tool in runtime.tools}
            assert "remote__echo" in tools
            result = await tools["remote__echo"].execute(
                payload={"text": "http", "tags": ["ok"]},
                repeat=1,
            )
            assert "http:ok" in result
            status = runtime.status_text()
            assert "Connected servers:" in status
            assert "remote [streamable_http] state=connected" in status
            payload = runtime.status_payload()
            assert payload["summary"]["connected"] == 1
            assert payload["servers"][0]["state"] == "connected"
        finally:
            await runtime.close()
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()


@pytest.mark.asyncio
async def test_mcp_runtime_status_payload_marks_disabled_and_timeout(tmp_path):
    from artel_core.config import ArtelConfig
    from artel_core.extensions import ExtensionContext
    from artel_core.mcp import MCPConfig, MCPRegistry, MCPServerConfig
    from artel_core.mcp_runtime import McpRuntimeManager

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".artel").mkdir()

    registry = MCPRegistry()
    registry.write_project_config(
        str(project_dir),
        MCPConfig(
            servers=[
                MCPServerConfig(
                    name="disabled-demo", enabled=False, transport="stdio", command="python3"
                ),
                MCPServerConfig(
                    name="timeout-demo", transport="streamable_http", url="https://example.test/mcp"
                ),
            ]
        ),
    )

    runtime = McpRuntimeManager()

    async def _fake_connect(name, config):
        raise httpx.ReadTimeout(
            "timed out", request=httpx.Request("GET", config.url or "https://example.test")
        )

    runtime._connect_server = _fake_connect  # type: ignore[method-assign]
    await runtime.load(
        ExtensionContext(project_dir=str(project_dir), runtime="local", config=ArtelConfig())
    )

    payload = runtime.status_payload()
    by_name = {item["name"]: item for item in payload["servers"]}
    assert by_name["disabled-demo"]["state"] == "disabled"
    assert by_name["timeout-demo"]["state"] == "timeout"
    assert payload["summary"]["disabled"] == 1
    assert payload["summary"]["timeout"] == 1


@pytest.mark.asyncio
async def test_mcp_runtime_status_payload_marks_needs_auth(tmp_path):
    from artel_core.config import ArtelConfig
    from artel_core.extensions import ExtensionContext
    from artel_core.mcp import MCPConfig, MCPRegistry, MCPServerConfig
    from artel_core.mcp_runtime import McpRuntimeManager

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".artel").mkdir()

    registry = MCPRegistry()
    registry.write_project_config(
        str(project_dir),
        MCPConfig(
            servers=[
                MCPServerConfig(
                    name="auth-demo", transport="streamable_http", url="https://example.test/mcp"
                ),
            ]
        ),
    )

    runtime = McpRuntimeManager()

    async def _fake_connect(name, config):
        raise RuntimeError("401 unauthorized")

    runtime._connect_server = _fake_connect  # type: ignore[method-assign]
    await runtime.load(
        ExtensionContext(project_dir=str(project_dir), runtime="local", config=ArtelConfig())
    )

    payload = runtime.status_payload()
    assert payload["servers"][0]["state"] == "needs_auth"
    assert payload["summary"]["needs_auth"] == 1
