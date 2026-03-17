"""Tests for first-party Artel capability scaffolding."""

from __future__ import annotations

import json


def test_load_builtin_capabilities_returns_bundled_capabilities() -> None:
    from artel_core.builtin_capabilities import load_builtin_capabilities
    from artel_core.lsp_runtime import LspRuntimeManager
    from artel_core.mcp import MCPRegistry

    capabilities = load_builtin_capabilities(project_dir="/tmp/project")

    assert sorted(capabilities) == [
        "artel-lsp",
        "artel-mcp",
    ]
    assert isinstance(capabilities["artel-lsp"].instance, LspRuntimeManager)
    assert isinstance(capabilities["artel-mcp"].instance, MCPRegistry)
    assert capabilities["artel-lsp"].bundled is True
    assert capabilities["artel-lsp"].removable is False
    assert capabilities["artel-mcp"].bundled is True
    assert capabilities["artel-mcp"].removable is False


def test_mcp_registry_reads_and_writes_artel_project_config(tmp_path, monkeypatch) -> None:
    import artel_core.config as cfg_mod
    from artel_core.mcp import MCPConfig, MCPRegistry, MCPServerConfig

    global_dir = tmp_path / "global-config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", global_dir)
    monkeypatch.setattr(cfg_mod, "GLOBAL_MCP_PATH", global_dir / "mcp.json")
    monkeypatch.setattr(cfg_mod, "LEGACY_GLOBAL_MCP_PATH", global_dir / "legacy-mcp.json")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    artel_dir = project_dir / ".artel"
    artel_dir.mkdir()
    (artel_dir / "mcp.json").write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "name": "filesystem",
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem"],
                        "env": {"ROOT": "/srv/project"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    registry = MCPRegistry()
    loaded = registry.load_project_config(str(project_dir))

    assert len(loaded.servers) == 1
    assert loaded.servers[0].name == "filesystem"
    assert loaded.servers[0].command == "npx"
    assert loaded.servers[0].env["ROOT"] == "/srv/project"

    written = registry.write_project_config(
        str(project_dir),
        MCPConfig(servers=[MCPServerConfig(name="browser", command="uvx")]),
    )
    saved = json.loads(written.read_text(encoding="utf-8"))

    assert written == artel_dir / "mcp.json"
    assert saved["servers"][0]["name"] == "browser"
    assert saved["servers"][0]["command"] == "uvx"

    global_written = registry.write_global_config(
        MCPConfig(servers=[MCPServerConfig(name="global-browser", command="uvx")])
    )
    global_saved = json.loads(global_written.read_text(encoding="utf-8"))

    assert global_written == global_dir / "mcp.json"
    assert global_saved["servers"][0]["name"] == "global-browser"


def test_mcp_registry_merges_global_and_project_stores(tmp_path, monkeypatch) -> None:
    import artel_core.config as cfg_mod
    from artel_core.mcp import MCPRegistry

    global_dir = tmp_path / "global-config"
    global_dir.mkdir()
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", global_dir)
    monkeypatch.setattr(cfg_mod, "GLOBAL_MCP_PATH", global_dir / "mcp.json")
    monkeypatch.setattr(cfg_mod, "LEGACY_GLOBAL_MCP_PATH", global_dir / "legacy-mcp.json")

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".artel").mkdir()
    (global_dir / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "demo": {
                        "transport": "stdio",
                        "command": "python3",
                        "headers": {"Authorization": "Bearer ${MCP_TEST_HEADER}"},
                    },
                    "shared": {"command": "global-cmd"},
                }
            }
        ),
        encoding="utf-8",
    )
    (project_dir / ".artel" / "mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "demo": {
                        "args": ["server.py"],
                        "tool_prefix": "demo__",
                        "cwd": "./tools",
                    },
                    "project-only": {"command": "project-cmd"},
                }
            }
        ),
        encoding="utf-8",
    )
    (project_dir / ".artel" / "tools").mkdir()
    monkeypatch.setenv("MCP_TEST_HEADER", "demo-token")

    registry = MCPRegistry()
    loaded = registry.load_merged_config(str(project_dir))

    assert len(loaded.sources) == 2
    assert sorted(loaded.servers) == ["demo", "project-only", "shared"]
    demo = loaded.servers["demo"]
    assert demo.command == "python3"
    assert demo.args == ["server.py"]
    assert demo.tool_prefix == "demo__"
    assert demo.cwd == str((project_dir / ".artel" / "tools").resolve())
    assert demo.headers["Authorization"] == "Bearer demo-token"
    assert loaded.servers["shared"].command == "global-cmd"
    assert loaded.servers["project-only"].command == "project-cmd"


def test_runtime_bootstrap_binds_builtin_capabilities_into_extension_context(monkeypatch, tmp_path):
    from artel_core.bootstrap import bootstrap_runtime
    from artel_core.cli import _resolve_api_key
    from artel_core.config import ArtelConfig

    seen_contexts = []

    async def fake_load_ai_extensions_async(context=None):
        seen_contexts.append(context)
        return []

    async def fake_load_extensions_async(context=None):
        seen_contexts.append(context)
        hook_dispatcher = __import__(
            "artel_core.extensions",
            fromlist=["HookDispatcher"],
        ).HookDispatcher()
        return [], hook_dispatcher

    class _Provider:
        async def close(self):
            return None

    class _Registry:
        def create(self, provider_type, api_key=None, **kwargs):
            return _Provider()

    monkeypatch.setattr("artel_core.bootstrap.create_default_registry", lambda: _Registry())
    monkeypatch.setattr(
        "artel_core.bootstrap.load_ai_extensions_async",
        fake_load_ai_extensions_async,
    )
    monkeypatch.setattr("artel_core.bootstrap.load_extensions_async", fake_load_extensions_async)
    monkeypatch.setattr(
        "artel_core.bootstrap.resolve_provider_runtime_config",
        lambda config, provider_name: (provider_name, {}),
    )
    monkeypatch.setattr(
        "artel_core.bootstrap.fetch_model_runtime_info",
        lambda config, provider_name, model_id: __import__("asyncio").sleep(
            0,
            result=(0, 0.0, 0.0),
        ),
    )

    runtime = __import__("asyncio").run(
        bootstrap_runtime(
            ArtelConfig(),
            "openai",
            "gpt-4.1",
            project_dir=str(tmp_path),
            resolve_api_key=_resolve_api_key,
            include_extensions=True,
            runtime="local",
        )
    )

    assert runtime.extensions == []
    assert len(seen_contexts) == 2
    for context in seen_contexts:
        assert context is not None
        assert "builtin_capabilities" in context.extras
        assert sorted(context.extras["builtin_capabilities"]) == [
            "artel-lsp",
            "artel-mcp",
        ]


def test_list_installed_extensions_includes_bundled_capabilities(monkeypatch):
    from artel_core.extensions_admin import list_installed_extensions

    class _Ext:
        version = "1.2.3"

    monkeypatch.setattr(
        "artel_core.extensions_admin.discover_extensions",
        lambda: {"artel-ext-demo": _Ext},
    )
    monkeypatch.setattr(
        "artel_core.extensions_admin.ext_manifest.list_entries",
        lambda: [
            type(
                "Entry",
                (),
                {"name": "artel-ext-demo", "source": "git+https://example.com/demo.git"},
            )()
        ],
    )

    result = list_installed_extensions()
    names = [item.name for item in result]

    assert "artel-lsp" in names
    assert "artel-mcp" in names
    assert "artel-ext-demo" in names
    bundled = {item.name: item for item in result if item.source == "bundled"}
    assert bundled["artel-lsp"].version == "bundled"
    assert bundled["artel-mcp"].version == "bundled"
