from __future__ import annotations

import json

import pytest


@pytest.mark.asyncio
async def test_mcp_runtime_can_connect_to_context7_streamable_http(tmp_path, monkeypatch):
    from artel_core.config import ArtelConfig
    from artel_core.extensions import ExtensionContext
    from artel_core.mcp_runtime import McpRuntimeManager

    api_key = "ctx7sk-6b558bf0-48fb-4ad5-af92-336363ebea07"

    global_dir = tmp_path / "global"
    project_dir = tmp_path / "project"
    global_dir.mkdir()
    project_dir.mkdir()
    (project_dir / ".artel").mkdir()

    import artel_core.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", global_dir)
    monkeypatch.setattr(cfg_mod, "GLOBAL_MCP_PATH", global_dir / "mcp.json")
    monkeypatch.setattr(cfg_mod, "LEGACY_GLOBAL_MCP_PATH", global_dir / "legacy-mcp.json")
    monkeypatch.setenv("CONTEXT7_API_KEY", api_key)

    (global_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "context7": {
                        "transport": "streamable_http",
                        "url": "https://context7.liam.sh/mcp",
                        "headers": {"Authorization": "Bearer ${CONTEXT7_API_KEY}"},
                        "tool_prefix": "ctx7__",
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
        assert runtime.errors == {}, runtime.errors
        assert "context7" in runtime.servers
        tools = {tool.name: tool for tool in runtime.tools}
        assert tools, "Expected Context7 MCP to expose tools"
        assert any(name.startswith("ctx7__") for name in tools)
        status = runtime.status_text()
        assert "Connected servers:" in status
        assert "context7 [streamable_http]" in status
    finally:
        await runtime.close()
