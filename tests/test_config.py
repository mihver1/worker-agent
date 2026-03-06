"""Tests for configuration system."""

from __future__ import annotations

from worker_core.bootstrap import (
    provider_requires_api_key,
    resolve_provider_runtime_config,
)
from worker_core.config import (
    ProviderConfig,
    WorkerConfig,
    _deep_merge,
    generate_global_config,
    generate_project_config,
    load_config,
    resolve_model,
)


class TestResolveModel:
    def test_provider_slash_model(self):
        config = WorkerConfig()
        config.agent.model = "openai/gpt-4.1"
        provider, model = resolve_model(config)
        assert provider == "openai"
        assert model == "gpt-4.1"

    def test_default_anthropic(self):
        config = WorkerConfig()
        config.agent.model = "claude-sonnet-4-20250514"
        provider, model = resolve_model(config)
        assert provider == "anthropic"
        assert model == "claude-sonnet-4-20250514"

    def test_deep_provider_id(self):
        config = WorkerConfig()
        config.agent.model = "google/gemini-2.5-pro"
        provider, model = resolve_model(config)
        assert provider == "google"
        assert model == "gemini-2.5-pro"


class TestDeepMerge:
    def test_simple_override(self):
        base = {"a": 1, "b": 2}
        _deep_merge(base, {"b": 3})
        assert base == {"a": 1, "b": 3}

    def test_nested_merge(self):
        base = {"a": {"x": 1, "y": 2}, "b": 0}
        _deep_merge(base, {"a": {"y": 99, "z": 3}})
        assert base == {"a": {"x": 1, "y": 99, "z": 3}, "b": 0}

    def test_new_key(self):
        base = {"a": 1}
        _deep_merge(base, {"b": 2})
        assert base == {"a": 1, "b": 2}


class TestProviderRuntimeConfig:
    def test_provider_section_name_used_when_type_omitted(self):
        config = WorkerConfig(
            providers={"openai": ProviderConfig(api_key="sk-test")}
        )
        provider_type, kwargs = resolve_provider_runtime_config(config, "openai")
        assert provider_type == "openai"
        assert kwargs == {}

    def test_openai_compat_alias_gets_default_base_url(self):
        config = WorkerConfig()
        provider_type, kwargs = resolve_provider_runtime_config(config, "groq")
        assert provider_type == "openai_compat"
        assert kwargs["base_url"] == "https://api.groq.com/openai/v1"

    def test_provider_config_overrides_runtime_settings(self):
        config = WorkerConfig(
            providers={
                "groq": ProviderConfig(
                    type="openai_compat",
                    base_url="https://proxy.example/v1",
                    api_type="chat",
                    api_version="2025-01-01",
                )
            }
        )
        provider_type, kwargs = resolve_provider_runtime_config(config, "groq")
        assert provider_type == "openai_compat"
        assert kwargs["base_url"] == "https://proxy.example/v1"
        assert kwargs["api_type"] == "chat"
        assert kwargs["api_version"] == "2025-01-01"

    def test_provider_requires_api_key_false_for_ollama(self):
        assert provider_requires_api_key(WorkerConfig(), "ollama") is False

    def test_ollama_uses_local_defaults_and_optional_env_key(self):
        provider_type, kwargs = resolve_provider_runtime_config(WorkerConfig(), "ollama")
        assert provider_type == "ollama"
        assert kwargs["base_url"] == "http://localhost:11434/v1"
        assert provider_requires_api_key(WorkerConfig(), "ollama") is False

    def test_ollama_cloud_alias_uses_hosted_defaults(self):
        config = WorkerConfig()

        for provider_name in ("ollama_cloud", "ollama-cloud"):
            provider_type, kwargs = resolve_provider_runtime_config(config, provider_name)
            assert provider_type == "ollama"
            assert kwargs["base_url"] == "https://ollama.com/v1"
            assert provider_requires_api_key(config, provider_name) is True

    def test_lmstudio_aliases_are_keyless_with_local_defaults(self):
        config = WorkerConfig()

        for provider_name in ("lmstudio", "lm-studio"):
            provider_type, kwargs = resolve_provider_runtime_config(config, provider_name)
            assert provider_type == "lmstudio"
            assert kwargs["base_url"] == "http://127.0.0.1:1234/v1"
            assert provider_requires_api_key(config, provider_name) is False
    def test_llamacpp_aliases_are_keyless_with_local_defaults(self):
        config = WorkerConfig()

        for provider_name in ("llama.cpp", "llamacpp"):
            provider_type, kwargs = resolve_provider_runtime_config(config, provider_name)
            assert provider_type == "openai_compat"
            assert kwargs["base_url"] == "http://localhost:8080/v1"
            assert provider_requires_api_key(config, provider_name) is False

    def test_long_tail_openai_compat_specs_get_default_base_urls(self):
        config = WorkerConfig()

        expected = {
            "302ai": "https://api.302.ai/v1",
            "baseten": "https://inference.baseten.co/v1",
            "fireworks": "https://api.fireworks.ai/inference/v1",
            "helicone": "https://ai-gateway.helicone.ai/v1",
            "io-net": "https://api.intelligence.io.solutions/api/v1",
            "nebius": "https://api.tokenfactory.nebius.com/v1",
        }

        for provider_name, base_url in expected.items():
            provider_type, kwargs = resolve_provider_runtime_config(config, provider_name)
            assert provider_type == "openai_compat"
            assert kwargs["base_url"] == base_url
            assert provider_requires_api_key(config, provider_name) is True

    def test_openai_compat_aliases_resolve_to_canonical_specs(self):
        config = WorkerConfig()

        expected = {
            "togetherai": "https://api.together.xyz/v1",
            "fireworks-ai": "https://api.fireworks.ai/inference/v1",
            "io.net": "https://api.intelligence.io.solutions/api/v1",
            "ionet": "https://api.intelligence.io.solutions/api/v1",
            "302.ai": "https://api.302.ai/v1",
        }

        for provider_name, base_url in expected.items():
            provider_type, kwargs = resolve_provider_runtime_config(config, provider_name)
            assert provider_type == "openai_compat"
            assert kwargs["base_url"] == base_url

    def test_provider_requires_api_key_true_for_openai_compat_alias(self):
        assert provider_requires_api_key(WorkerConfig(), "groq") is True

    def test_anthropic_runtime_options_are_passthrough(self):
        config = WorkerConfig(
            providers={
                "anthropic": ProviderConfig(
                    options={
                        "beta_headers": ["files-api-2025-04-14"],
                        "interleaved_thinking": True,
                        "fine_grained_tool_streaming": True,
                    }
                )
            }
        )
        provider_type, kwargs = resolve_provider_runtime_config(config, "anthropic")
        assert provider_type == "anthropic"
        assert kwargs["beta_headers"] == ["files-api-2025-04-14"]
        assert kwargs["interleaved_thinking"] is True
        assert kwargs["fine_grained_tool_streaming"] is True

    def test_google_vertex_runtime_settings_are_passthrough(self):
        config = WorkerConfig(
            providers={
                "google_vertex": ProviderConfig(
                    project="demo-project",
                    location="us-central1",
                    options={
                        "credentials_path": "/tmp/google-vertex.json",
                        "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
                    },
                )
            }
        )
        provider_type, kwargs = resolve_provider_runtime_config(config, "google_vertex")
        assert provider_type == "google_vertex"
        assert kwargs["base_url"] == "https://{location}-aiplatform.googleapis.com"
        assert kwargs["project"] == "demo-project"
        assert kwargs["location"] == "us-central1"
        assert kwargs["credentials_path"] == "/tmp/google-vertex.json"
        assert kwargs["scopes"] == ["https://www.googleapis.com/auth/cloud-platform"]
        assert provider_requires_api_key(config, "google_vertex") is False

    def test_google_vertex_alias_resolves_to_canonical_spec(self):
        provider_type, kwargs = resolve_provider_runtime_config(WorkerConfig(), "google-vertex")
        assert provider_type == "google_vertex"
        assert kwargs["base_url"] == "https://{location}-aiplatform.googleapis.com"
        assert provider_requires_api_key(WorkerConfig(), "google-vertex") is False

    def test_vertex_anthropic_runtime_settings_are_passthrough(self):
        config = WorkerConfig(
            providers={
                "vertex_anthropic": ProviderConfig(
                    project="demo-project",
                    location="us-east5",
                    options={
                        "credentials_path": "/tmp/vertex-anthropic.json",
                        "beta_headers": ["files-api-2025-04-14"],
                    },
                )
            }
        )
        provider_type, kwargs = resolve_provider_runtime_config(config, "vertex_anthropic")
        assert provider_type == "vertex_anthropic"
        assert kwargs["base_url"] == "https://{location}-aiplatform.googleapis.com"
        assert kwargs["project"] == "demo-project"
        assert kwargs["location"] == "us-east5"
        assert kwargs["credentials_path"] == "/tmp/vertex-anthropic.json"
        assert kwargs["beta_headers"] == ["files-api-2025-04-14"]
        assert provider_requires_api_key(config, "vertex_anthropic") is False

    def test_vertex_anthropic_alias_resolves_to_canonical_spec(self):
        provider_type, kwargs = resolve_provider_runtime_config(
            WorkerConfig(),
            "anthropic_vertex",
        )
        assert provider_type == "vertex_anthropic"
        assert kwargs["base_url"] == "https://{location}-aiplatform.googleapis.com"
        assert provider_requires_api_key(WorkerConfig(), "anthropic_vertex") is False

    def test_azure_openai_runtime_settings_are_passthrough(self):
        config = WorkerConfig(
            providers={
                "azure_openai": ProviderConfig(
                    base_url="https://demo.openai.azure.com",
                    api_version="2024-10-21",
                    api_type="responses",
                )
            }
        )
        provider_type, kwargs = resolve_provider_runtime_config(config, "azure_openai")
        assert provider_type == "azure_openai"
        assert kwargs["base_url"] == "https://demo.openai.azure.com"
        assert kwargs["api_version"] == "2024-10-21"
        assert kwargs["api_type"] == "responses"
        assert provider_requires_api_key(config, "azure_openai") is True

    def test_bedrock_runtime_settings_are_passthrough(self):
        config = WorkerConfig(
            providers={
                "bedrock": ProviderConfig(
                    region="us-east-1",
                    profile="sandbox",
                    base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
                    options={
                        "access_key_id": "AKIA_TEST",
                        "secret_access_key": "secret",
                        "session_token": "token",
                    },
                )
            }
        )
        provider_type, kwargs = resolve_provider_runtime_config(config, "bedrock")
        assert provider_type == "bedrock"
        assert kwargs["region"] == "us-east-1"
        assert kwargs["profile"] == "sandbox"
        assert kwargs["base_url"] == "https://bedrock-runtime.us-east-1.amazonaws.com"
        assert kwargs["access_key_id"] == "AKIA_TEST"
        assert kwargs["secret_access_key"] == "secret"
        assert kwargs["session_token"] == "token"
        assert provider_requires_api_key(config, "bedrock") is False

    def test_github_copilot_runtime_uses_builtin_defaults(self):
        provider_type, kwargs = resolve_provider_runtime_config(WorkerConfig(), "github_copilot")
        assert provider_type == "github_copilot"
        assert kwargs["base_url"] == "https://api.githubcopilot.com"
        assert provider_requires_api_key(WorkerConfig(), "github_copilot") is True

    def test_github_copilot_enterprise_alias_resolves_canonical_config(self):
        config = WorkerConfig(
            providers={
                "github_copilot_enterprise": ProviderConfig(
                    options={"github_host": "octo.ghe.com"},
                )
            }
        )
        provider_type, kwargs = resolve_provider_runtime_config(
            config,
            "github-copilot-enterprise",
        )
        assert provider_type == "github_copilot"
        assert kwargs["base_url"] == "https://api.githubcopilot.com"
        assert kwargs["github_host"] == "octo.ghe.com"
        assert provider_requires_api_key(config, "github-copilot-enterprise") is True

    def test_provider_runtime_config_includes_headers_timeout_and_options(self):
        config = WorkerConfig(
            providers={
                "openrouter": ProviderConfig(
                    headers={"HTTP-Referer": "https://example.com"},
                    timeout=300000,
                    options={"include_usage": True},
                )
            }
        )
        provider_type, kwargs = resolve_provider_runtime_config(config, "openrouter")
        assert provider_type == "openai_compat"
        assert kwargs["base_url"] == "https://openrouter.ai/api/v1"
        assert kwargs["headers"]["HTTP-Referer"] == "https://example.com"
        assert kwargs["timeout"] == 300000
        assert kwargs["include_usage"] is True

    def test_provider_requires_api_key_respects_override(self):
        config = WorkerConfig(
            providers={
                "localproxy": ProviderConfig(
                    type="openai_compat",
                    base_url="http://127.0.0.1:1234/v1",
                    requires_api_key=False,
                )
            }
        )
        assert provider_requires_api_key(config, "localproxy") is False


class TestLoadConfig:
    def test_default_config(self, tmp_path, monkeypatch):
        """Loading from a dir with no config files → defaults."""
        import worker_core.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "GLOBAL_CONFIG", tmp_path / "config.toml")
        config = load_config("/nonexistent/path")
        assert config.agent.model == "anthropic/claude-sonnet-4-20250514"
        assert config.agent.temperature == 0.0
        assert config.permissions.bash == "ask"
        assert config.server.port == 7432

    def test_project_overlay(self, tmp_path):
        """Project config merges over global defaults."""
        worker_dir = tmp_path / ".worker"
        worker_dir.mkdir()
        (worker_dir / "config.toml").write_text(
            '[agent]\nmodel = "openai/gpt-4.1"\ntemperature = 0.5\n'
        )
        config = load_config(str(tmp_path))
        assert config.agent.model == "openai/gpt-4.1"
        assert config.agent.temperature == 0.5
        # Other fields stay at defaults
        assert config.permissions.bash == "ask"


class TestGenerateConfig:
    def test_generate_global(self, tmp_path, monkeypatch):
        import worker_core.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg_mod, "GLOBAL_CONFIG", tmp_path / "config.toml")
        generate_global_config()
        content = (tmp_path / "config.toml").read_text()
        # Should contain commented examples
        assert "[agent]" in content
        assert "model =" in content
        assert "[providers" in content  # either [providers.*] sections
        assert "[permissions]" in content

    def test_generate_project(self, tmp_path):
        generate_project_config(str(tmp_path))
        assert (tmp_path / ".worker" / "config.toml").exists()
        assert (tmp_path / ".worker" / "AGENTS.md").exists()

    def test_no_overwrite(self, tmp_path, monkeypatch):
        """Generating global config shouldn't overwrite existing."""
        import worker_core.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg_mod, "GLOBAL_CONFIG", tmp_path / "config.toml")
        (tmp_path / "config.toml").write_text("custom content")
        generate_global_config()
        assert (tmp_path / "config.toml").read_text() == "custom content"


class TestWorkerConfigDefaults:
    def test_all_defaults(self):
        config = WorkerConfig()
        assert config.agent.max_turns == 50
        assert config.server.host == "127.0.0.1"
        assert config.sessions.auto_compact is True
        assert config.ui.theme == "dark"
        assert config.ui.show_cost is True
