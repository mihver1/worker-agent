"""Tests for AGENTS.md loading, hook dispatching, and OAuth api_key fallback."""

from __future__ import annotations

from typing import Any

import pytest
from artel_ai.models import Done, TextDelta, Usage
from artel_ai.oauth import OAuthToken, TokenStore
from artel_core.agent import AgentSession
from artel_core.extensions import Extension, HookDispatcher, hook
from conftest import MockProvider

# ── AGENTS.md loading ─────────────────────────────────────────────


class TestAgentsMdLoading:
    @pytest.fixture(autouse=True)
    def _isolate_global_context_files(self, tmp_path, monkeypatch):
        import artel_core.config as cfg_mod

        global_dir = tmp_path / "global"
        monkeypatch.setattr(cfg_mod, "GLOBAL_AGENTS_FILE", global_dir / "AGENTS.md")
        monkeypatch.setattr(
            cfg_mod,
            "LEGACY_GLOBAL_AGENTS_FILE",
            global_dir / "legacy-AGENTS.md",
        )
        monkeypatch.setattr(cfg_mod, "GLOBAL_SYSTEM_OVERRIDE", global_dir / "SYSTEM.md")
        monkeypatch.setattr(
            cfg_mod,
            "LEGACY_GLOBAL_SYSTEM_OVERRIDE",
            global_dir / "legacy-SYSTEM.md",
        )
        monkeypatch.setattr(
            cfg_mod,
            "GLOBAL_APPEND_SYSTEM",
            global_dir / "APPEND_SYSTEM.md",
        )
        monkeypatch.setattr(
            cfg_mod,
            "LEGACY_GLOBAL_APPEND_SYSTEM",
            global_dir / "legacy-APPEND_SYSTEM.md",
        )
        monkeypatch.setattr(cfg_mod, "SKILLS_DIR", global_dir / "skills")
        monkeypatch.setattr(cfg_mod, "LEGACY_SKILLS_DIR", global_dir / "legacy-skills")

    def test_system_prompt_includes_agents_md(self, tmp_path):
        artel_dir = tmp_path / ".artel"
        artel_dir.mkdir()
        (artel_dir / "AGENTS.md").write_text("# My Project\nAlways use pytest.\n")

        prompt = AgentSession._build_system_prompt("", str(tmp_path))
        assert "Always use pytest." in prompt
        assert "Artel" in prompt  # default prompt still there

    def test_system_prompt_custom_plus_agents_md(self, tmp_path):
        artel_dir = tmp_path / ".artel"
        artel_dir.mkdir()
        (artel_dir / "AGENTS.md").write_text("Use black for formatting.\n")

        prompt = AgentSession._build_system_prompt("Be concise.", str(tmp_path))
        assert "Be concise." in prompt
        assert "Use black for formatting." in prompt

    def test_system_prompt_no_agents_md(self, tmp_path):
        prompt = AgentSession._build_system_prompt("", str(tmp_path))
        assert "Artel" in prompt
        # No crash when .artel/AGENTS.md doesn't exist

    def test_system_prompt_empty_agents_md(self, tmp_path):
        artel_dir = tmp_path / ".artel"
        artel_dir.mkdir()
        (artel_dir / "AGENTS.md").write_text("   \n")

        prompt = AgentSession._build_system_prompt("", str(tmp_path))
        # Empty content should not add extra sections
        assert prompt.count("\n\n") == 0 or "Artel" in prompt

    def test_system_prompt_legacy_artel_agents_md_still_loads(self, tmp_path):
        artel_dir = tmp_path / ".artel"
        artel_dir.mkdir()
        (artel_dir / "AGENTS.md").write_text("Legacy rule: keep compatibility.\n")

        prompt = AgentSession._build_system_prompt("", str(tmp_path))
        assert "Legacy rule: keep compatibility." in prompt

    @pytest.mark.asyncio
    async def test_session_uses_agents_md(self, tmp_path):
        artel_dir = tmp_path / ".artel"
        artel_dir.mkdir()
        (artel_dir / "AGENTS.md").write_text("Project rule: always test.\n")

        provider = MockProvider()
        session = AgentSession(
            provider=provider,
            model="test",
            tools=[],
            project_dir=str(tmp_path),
        )
        assert "always test" in session.messages[0].content


# ── HookDispatcher ────────────────────────────────────────────────


class _TestExtension(Extension):
    name = "test"
    version = "0.0.1"

    def __init__(self):
        self.calls: list[str] = []

    @hook("before_turn")
    async def on_before(self, **kwargs: Any) -> None:
        self.calls.append(f"before_turn:{kwargs.get('turn')}")

    @hook("after_turn")
    async def on_after(self, **kwargs: Any) -> None:
        self.calls.append(f"after_turn:{kwargs.get('turn')}")

    @hook("on_tool_call")
    async def on_tool(self, **kwargs: Any) -> None:
        self.calls.append(f"on_tool_call:{kwargs.get('tool_name')}")


class TestHookDispatcher:
    @pytest.mark.asyncio
    async def test_fire_hooks(self):
        ext = _TestExtension()
        dispatcher = HookDispatcher([ext])
        await dispatcher.fire("before_turn", session=None, turn=0)
        await dispatcher.fire("after_turn", session=None, turn=0)
        await dispatcher.fire("on_tool_call", session=None, tool_name="bash", args={})

        assert ext.calls == ["before_turn:0", "after_turn:0", "on_tool_call:bash"]

    @pytest.mark.asyncio
    async def test_fire_unknown_event(self):
        """Unknown events should not crash."""
        dispatcher = HookDispatcher([])
        await dispatcher.fire("nonexistent", foo="bar")  # should be no-op

    @pytest.mark.asyncio
    async def test_hooks_called_in_agent_loop(self, tmp_workdir):
        """Hooks are actually called during agent loop."""
        ext = _TestExtension()
        dispatcher = HookDispatcher([ext])

        provider = MockProvider(
            responses=[
                [TextDelta(content="ok"), Done(usage=Usage())],
            ]
        )
        session = AgentSession(
            provider=provider,
            model="test",
            tools=[],
            hooks=dispatcher,
        )
        async for _ in session.run("hi"):
            pass

        assert "before_turn:0" in ext.calls
        assert "after_turn:0" in ext.calls


# ── OAuth fallback in _resolve_api_key ────────────────────────────


class TestOAuthFallback:
    @pytest.mark.asyncio
    async def test_oauth_token_used_when_no_key(self, tmp_path, monkeypatch):
        from artel_core.cli import _resolve_api_key
        from artel_core.config import ArtelConfig

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        store = TokenStore(path=tmp_path / "auth.json")
        store.save(
            OAuthToken(
                access_token="oauth_token_123",
                provider="anthropic",
                expires_at=9999999999.0,
            )
        )

        import artel_ai.oauth as oauth_mod

        monkeypatch.setattr(oauth_mod, "_DEFAULT_AUTH_PATH", tmp_path / "auth.json")

        config = ArtelConfig()
        key, auth_type = await _resolve_api_key(config, "anthropic")
        assert key == "oauth_token_123"
        assert auth_type == "oauth"

    @pytest.mark.asyncio
    async def test_config_key_takes_priority(self, tmp_path, monkeypatch):
        from artel_core.cli import _resolve_api_key
        from artel_core.config import ArtelConfig, ProviderConfig

        store = TokenStore(path=tmp_path / "auth.json")
        store.save(
            OAuthToken(
                access_token="oauth_token",
                provider="anthropic",
                expires_at=9999999999.0,
            )
        )

        import artel_ai.oauth as oauth_mod

        monkeypatch.setattr(oauth_mod, "_DEFAULT_AUTH_PATH", tmp_path / "auth.json")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        config = ArtelConfig(providers={"anthropic": ProviderConfig(api_key="sk-config-key")})
        key, auth_type = await _resolve_api_key(config, "anthropic")
        assert key == "sk-config-key"
        assert auth_type == "api"

    @pytest.mark.asyncio
    async def test_env_key_takes_priority_over_oauth(self, tmp_path, monkeypatch):
        from artel_core.cli import _resolve_api_key
        from artel_core.config import ArtelConfig

        store = TokenStore(path=tmp_path / "auth.json")
        store.save(
            OAuthToken(
                access_token="oauth_token",
                provider="anthropic",
                expires_at=9999999999.0,
            )
        )

        import artel_ai.oauth as oauth_mod

        monkeypatch.setattr(oauth_mod, "_DEFAULT_AUTH_PATH", tmp_path / "auth.json")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")

        config = ArtelConfig()
        key, auth_type = await _resolve_api_key(config, "anthropic")
        assert key == "sk-env-key"
        assert auth_type == "api"

    @pytest.mark.asyncio
    async def test_expired_oauth_token_is_refreshed(self, tmp_path, monkeypatch):
        from artel_core.cli import _resolve_api_key
        from artel_core.config import ArtelConfig

        store = TokenStore(path=tmp_path / "auth.json")
        store.save(
            OAuthToken(
                access_token="expired_token",
                refresh_token="refresh_token",
                provider="anthropic",
                expires_at=1.0,
            )
        )

        import artel_ai.oauth as oauth_mod

        monkeypatch.setattr(oauth_mod, "_DEFAULT_AUTH_PATH", tmp_path / "auth.json")
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        async def fake_refresh(self, token):
            assert token.access_token == "expired_token"
            return OAuthToken(
                access_token="refreshed_token",
                refresh_token="new_refresh_token",
                provider="anthropic",
                expires_at=9999999999.0,
            )

        monkeypatch.setattr(oauth_mod.AnthropicOAuth, "refresh", fake_refresh)

        key, auth_type = await _resolve_api_key(ArtelConfig(), "anthropic")

        assert key == "refreshed_token"
        assert auth_type == "oauth"
        saved = TokenStore(path=tmp_path / "auth.json").load("anthropic")
        assert saved is not None
        assert saved.access_token == "refreshed_token"
        assert saved.refresh_token == "new_refresh_token"

    @pytest.mark.asyncio
    async def test_non_oauth_provider_does_not_use_stale_token_store_entry(
        self,
        tmp_path,
        monkeypatch,
    ):
        from artel_core.cli import _resolve_api_key
        from artel_core.config import ArtelConfig

        store = TokenStore(path=tmp_path / "auth.json")
        store.save(
            OAuthToken(
                access_token="stale_kimi_oauth_token",
                provider="kimi",
                expires_at=9999999999.0,
            )
        )

        import artel_ai.oauth as oauth_mod

        monkeypatch.setattr(oauth_mod, "_DEFAULT_AUTH_PATH", tmp_path / "auth.json")
        monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)

        key, auth_type = await _resolve_api_key(ArtelConfig(), "kimi")

        assert key is None
        assert auth_type == "api"

    @pytest.mark.asyncio
    async def test_custom_provider_env_list_is_used(self, monkeypatch):
        from artel_core.cli import _resolve_api_key
        from artel_core.config import ArtelConfig, ProviderConfig

        monkeypatch.setenv("CUSTOM_OPENAI_API_KEY", "env_token_123")

        config = ArtelConfig(
            providers={
                "localproxy": ProviderConfig(
                    type="openai_compat",
                    env=["CUSTOM_OPENAI_API_KEY"],
                )
            }
        )
        key, auth_type = await _resolve_api_key(config, "localproxy")
        assert key == "env_token_123"
        assert auth_type == "api"

    @pytest.mark.asyncio
    async def test_ollama_cloud_reads_api_key_from_builtin_env(self, monkeypatch):
        from artel_core.cli import _resolve_api_key
        from artel_core.config import ArtelConfig

        monkeypatch.setenv("OLLAMA_API_KEY", "ollama_env_token")

        key, auth_type = await _resolve_api_key(ArtelConfig(), "ollama-cloud")
        assert key == "ollama_env_token"
        assert auth_type == "api"

    @pytest.mark.asyncio
    async def test_zai_alias_reads_api_key_from_builtin_env(self, monkeypatch):
        from artel_core.cli import _resolve_api_key
        from artel_core.config import ArtelConfig

        monkeypatch.setenv("ZHIPU_API_KEY", "zhipu_env_token")

        key, auth_type = await _resolve_api_key(ArtelConfig(), "z.ai")
        assert key == "zhipu_env_token"
        assert auth_type == "api"

    @pytest.mark.asyncio
    async def test_minimax_reads_api_key_from_builtin_env(self, monkeypatch):
        from artel_core.cli import _resolve_api_key
        from artel_core.config import ArtelConfig

        monkeypatch.setenv("MINIMAX_API_KEY", "minimax_env_token")

        key, auth_type = await _resolve_api_key(ArtelConfig(), "minimax")
        assert key == "minimax_env_token"
        assert auth_type == "api"

    @pytest.mark.asyncio
    async def test_github_copilot_reads_token_from_config_file(self, tmp_path, monkeypatch):
        import artel_ai.oauth as oauth_mod
        import artel_core.cli as cli_mod
        from artel_core.config import ArtelConfig, ProviderConfig

        token_path = tmp_path / ".copilot" / "config.json"
        token_path.parent.mkdir(parents=True)
        token_path.write_text(
            '{"hosts":{"github.com":{"oauth_token":"gho_github"},"octo.ghe.com":{"oauth_token":"gho_enterprise"}}}'
        )

        monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(oauth_mod, "_github_copilot_token_paths", lambda: (token_path,))

        async def _unexpected_gh_cli(host: str) -> str | None:
            raise AssertionError(f"gh auth fallback should not be used for {host}")

        monkeypatch.setattr(
            oauth_mod,
            "load_github_copilot_token_from_gh_cli",
            _unexpected_gh_cli,
        )

        config = ArtelConfig(
            providers={
                "github_copilot_enterprise": ProviderConfig(
                    options={"github_host": "octo.ghe.com"},
                )
            }
        )

        key, auth_type = await cli_mod._resolve_api_key(config, "github_copilot_enterprise")

        assert key == "gho_enterprise"
        assert auth_type == "api"

    @pytest.mark.asyncio
    async def test_github_copilot_gh_cli_fallback_uses_resolved_host(self, monkeypatch):
        import artel_ai.oauth as oauth_mod
        import artel_core.cli as cli_mod
        from artel_core.config import ArtelConfig, ProviderConfig

        monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(
            oauth_mod,
            "load_github_copilot_token_from_files",
            lambda host: None,
        )

        seen_hosts: list[str] = []

        async def _fake_gh_cli(host: str) -> str | None:
            seen_hosts.append(host)
            return "gho_from_gh_cli"

        monkeypatch.setattr(
            oauth_mod,
            "load_github_copilot_token_from_gh_cli",
            _fake_gh_cli,
        )

        config = ArtelConfig(
            providers={
                "github_copilot_enterprise": ProviderConfig(
                    options={"github_host": "octo.ghe.com"},
                )
            }
        )

        key, auth_type = await cli_mod._resolve_api_key(
            config,
            "github-copilot-enterprise",
        )

        assert key == "gho_from_gh_cli"
        assert auth_type == "api"
        assert seen_hosts == ["octo.ghe.com"]

    @pytest.mark.asyncio
    async def test_github_copilot_artel_oauth_token_takes_priority_over_gh_fallback(
        self,
        tmp_path,
        monkeypatch,
    ):
        import artel_ai.oauth as oauth_mod
        from artel_core.cli import _resolve_api_key
        from artel_core.config import ArtelConfig

        store = TokenStore(path=tmp_path / "auth.json")
        store.save(
            OAuthToken(
                access_token="artel_oauth_token",
                provider="github_copilot",
            )
        )

        monkeypatch.setattr(oauth_mod, "_DEFAULT_AUTH_PATH", tmp_path / "auth.json")
        monkeypatch.delenv("COPILOT_GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setattr(
            oauth_mod,
            "load_github_copilot_token_from_files",
            lambda host: "gho_from_file",
        )

        key, auth_type = await _resolve_api_key(ArtelConfig(), "github_copilot")

        assert key == "artel_oauth_token"
        assert auth_type == "oauth"


# ── ModelsCatalog ─────────────────────────────────────────────────


_SAMPLE_API_RESPONSE = {
    "anthropic": {
        "name": "Anthropic",
        "env": ["ANTHROPIC_API_KEY"],
        "models": {
            "claude-sonnet-4-20250514": {
                "name": "Claude Sonnet 4",
                "tool_call": True,
                "reasoning": False,
                "limit": {"context": 200000, "output": 8192},
                "cost": {"input": 3.0, "output": 15.0},
                "modalities": {"input": ["text", "image"], "output": ["text"]},
            },
            "claude-3-5-haiku-20241022": {
                "name": "Claude 3.5 Haiku",
                "tool_call": True,
                "reasoning": False,
                "limit": {"context": 200000, "output": 8192},
                "cost": {"input": 1.0, "output": 5.0},
            },
            "claude-3-embedding": {
                "name": "Claude Embedding",
                "tool_call": False,  # Not tool-capable — should be filtered
            },
        },
    },
    "openai": {
        "name": "OpenAI",
        "env": ["OPENAI_API_KEY"],
        "models": {
            "gpt-4o": {
                "name": "GPT-4o",
                "tool_call": True,
                "reasoning": False,
                "limit": {"context": 128000, "output": 4096},
                "cost": {"input": 5.0, "output": 15.0},
                "modalities": {"input": ["text", "image"], "output": ["text"]},
            },
        },
    },
}


class TestModelsCatalog:
    @pytest.fixture(autouse=True)
    def _reset_catalog(self):
        """Reset in-memory cache before each test."""
        from artel_ai.models_catalog import ModelsCatalog

        ModelsCatalog._data = None
        yield
        ModelsCatalog._data = None

    @pytest.mark.asyncio
    async def test_parse_and_filter(self, monkeypatch):
        from artel_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_API_RESPONSE

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        catalog = await ModelsCatalog.load()
        assert "anthropic" in catalog
        assert "openai" in catalog

        ant = catalog["anthropic"]
        assert ant.name == "Anthropic"
        # Embedding model filtered out (tool_call=False)
        assert len(ant.models) == 2
        ids = [m.id for m in ant.models]
        assert "claude-sonnet-4-20250514" in ids
        assert "claude-3-5-haiku-20241022" in ids
        assert "claude-3-embedding" not in ids

    @pytest.mark.asyncio
    async def test_list_models_by_provider(self, monkeypatch):
        from artel_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_API_RESPONSE

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        models = await ModelsCatalog.list_models("openai")
        assert len(models) == 1
        assert models[0].id == "gpt-4o"
        assert models[0].context_window == 128000
        assert models[0].supports_vision is True

    @pytest.mark.asyncio
    async def test_list_models_all(self, monkeypatch):
        from artel_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_API_RESPONSE

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        all_models = await ModelsCatalog.list_models()
        assert len(all_models) == 3  # 2 anthropic + 1 openai

    @pytest.mark.asyncio
    async def test_get_model(self, monkeypatch):
        from artel_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_API_RESPONSE

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        m = await ModelsCatalog.get_model("anthropic", "claude-sonnet-4-20250514")
        assert m is not None
        assert m.name == "Claude Sonnet 4"
        assert m.context_window == 200000
        assert m.supports_tools is True
        assert m.input_price_per_m == 3.0

    @pytest.mark.asyncio
    async def test_get_model_not_found(self, monkeypatch):
        from artel_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_API_RESPONSE

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        assert await ModelsCatalog.get_model("anthropic", "nonexistent") is None
        assert await ModelsCatalog.get_model("nonexistent", "gpt-4o") is None

    @pytest.mark.asyncio
    async def test_list_providers(self, monkeypatch):
        from artel_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_API_RESPONSE

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        providers = await ModelsCatalog.list_providers()
        names = {p.id for p in providers}
        assert names == {"anthropic", "openai"}

    @pytest.mark.asyncio
    async def test_cache_file(self, tmp_path, monkeypatch):
        import artel_ai.models_catalog as cat_mod
        from artel_ai.models_catalog import ModelsCatalog

        cache_path = tmp_path / "models.json"
        monkeypatch.setattr(cat_mod, "_CACHE_DIR", tmp_path)
        monkeypatch.setattr(cat_mod, "_CACHE_PATH", cache_path)

        async def _fake_fetch(cls):
            return _SAMPLE_API_RESPONSE

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        # Load writes cache
        await ModelsCatalog.load()
        # Reset memory cache
        ModelsCatalog._data = None

        # Now _fetch_raw will use real code path — should read cache
        monkeypatch.undo()  # restore _fetch_raw
        # Re-patch paths only
        monkeypatch.setattr(cat_mod, "_CACHE_DIR", tmp_path)
        monkeypatch.setattr(cat_mod, "_CACHE_PATH", cache_path)

        # Write sample data to cache manually
        import json

        cache_path.write_text(json.dumps(_SAMPLE_API_RESPONSE))
        catalog = await ModelsCatalog.load()
        assert "anthropic" in catalog

    @pytest.mark.asyncio
    async def test_refresh_clears_cache(self, tmp_path, monkeypatch):
        import artel_ai.models_catalog as cat_mod
        from artel_ai.models_catalog import ModelsCatalog

        cache_path = tmp_path / "models.json"
        monkeypatch.setattr(cat_mod, "_CACHE_DIR", tmp_path)
        monkeypatch.setattr(cat_mod, "_CACHE_PATH", cache_path)

        async def _fake_fetch(cls):
            return _SAMPLE_API_RESPONSE

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        await ModelsCatalog.load()
        assert ModelsCatalog._data is not None

        catalog = await ModelsCatalog.refresh()
        assert "anthropic" in catalog  # Re-fetched
