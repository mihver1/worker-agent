"""Tests for provider manifest resolution and effective provider catalogs."""

from __future__ import annotations

import pytest
from worker_core.config import ProviderConfig, ProviderModelConfig, WorkerConfig
from worker_core.provider_resolver import (
    get_effective_model_info,
    get_effective_provider_catalog,
)

_SAMPLE_PROVIDER_CATALOG = {
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
            "gpt-4o-mini": {
                "name": "GPT-4o Mini",
                "tool_call": True,
                "reasoning": False,
                "limit": {"context": 128000, "output": 4096},
                "cost": {"input": 0.15, "output": 0.60},
                "modalities": {"input": ["text"], "output": ["text"]},
            },
        },
    },
    "openrouter": {
        "name": "OpenRouter",
        "env": ["OPENROUTER_API_KEY"],
        "models": {
            "openai/gpt-4o": {
                "name": "GPT-4o via OpenRouter",
                "tool_call": True,
                "reasoning": False,
                "limit": {"context": 128000, "output": 4096},
                "cost": {"input": 5.0, "output": 15.0},
                "modalities": {"input": ["text", "image"], "output": ["text"]},
            },
        },
    },
    "google": {
        "name": "Google",
        "env": ["GEMINI_API_KEY"],
        "models": {
            "gemini-2.5-pro": {
                "name": "Gemini 2.5 Pro",
                "tool_call": True,
                "reasoning": True,
                "limit": {"context": 1048576, "output": 65536},
                "cost": {"input": 1.25, "output": 10.0},
                "modalities": {"input": ["text", "image"], "output": ["text"]},
            },
        },
    },
    "togetherai": {
        "name": "Together AI",
        "env": ["TOGETHER_API_KEY"],
        "models": {
            "meta-llama/llama-3.3-70b-instruct-turbo": {
                "name": "Llama 3.3 70B Turbo",
                "tool_call": True,
                "reasoning": False,
                "limit": {"context": 131072, "output": 8192},
                "cost": {"input": 0.88, "output": 0.88},
                "modalities": {"input": ["text"], "output": ["text"]},
            },
        },
    },
    "fireworks-ai": {
        "name": "Fireworks AI",
        "env": ["FIREWORKS_API_KEY"],
        "models": {
            "accounts/fireworks/models/llama-v3p1-8b-instruct": {
                "name": "Llama v3.1 8B Instruct",
                "tool_call": True,
                "reasoning": False,
                "limit": {"context": 131072, "output": 8192},
                "cost": {"input": 0.2, "output": 0.2},
                "modalities": {"input": ["text"], "output": ["text"]},
            },
        },
    },
    "perplexity": {
        "name": "Perplexity",
        "env": ["PERPLEXITY_API_KEY"],
        "models": {
            "sonar-pro": {
                "name": "Sonar Pro",
                "tool_call": True,
                "reasoning": False,
                "limit": {"context": 128000, "output": 4096},
                "cost": {"input": 1.0, "output": 1.0},
                "modalities": {"input": ["text"], "output": ["text"]},
            },
        },
    },
}


class TestEffectiveProviderCatalog:
    @pytest.fixture(autouse=True)
    def _reset_catalog(self):
        from worker_ai.models_catalog import ModelsCatalog

        ModelsCatalog._data = None
        yield
        ModelsCatalog._data = None

    @pytest.mark.asyncio
    async def test_only_supported_or_configured_providers_are_included(self, monkeypatch):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        config = WorkerConfig()
        providers = await get_effective_provider_catalog(config)

        assert "openai" in providers
        assert "anthropic" in providers
        assert "bedrock" in providers
        assert "azure_openai" in providers
        assert "github_copilot" in providers
        assert "github_copilot_enterprise" in providers
        assert "google" in providers
        assert "google_vertex" in providers
        assert "vertex_anthropic" in providers
        assert "openrouter" in providers
        assert "together" in providers
        assert "fireworks" in providers
        assert "perplexity" not in providers

    @pytest.mark.asyncio
    async def test_custom_provider_models_and_overrides_are_merged(self, monkeypatch):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        config = WorkerConfig(
            providers={
                "openai": ProviderConfig(
                    blacklist=["gpt-4o-mini"],
                    models={
                        "gpt-4o": ProviderModelConfig(
                            name="GPT-4o tuned",
                            context_window=256000,
                        ),
                        "gpt-4.1-local": ProviderModelConfig(
                            name="GPT-4.1 Local Proxy",
                            context_window=512000,
                            max_output_tokens=8192,
                            supports_reasoning=True,
                        ),
                    },
                ),
                "localproxy": ProviderConfig(
                    type="openai_compat",
                    name="Local Proxy",
                    requires_api_key=False,
                    models={
                        "qwen3:32b": ProviderModelConfig(
                            name="Qwen3 32B",
                            context_window=131072,
                            max_output_tokens=16384,
                            supports_reasoning=True,
                        )
                    },
                ),
            }
        )

        providers = await get_effective_provider_catalog(config)

        openai = providers["openai"]
        openai_models = {model.id: model for model in openai.models}
        assert "gpt-4o-mini" not in openai_models
        assert openai_models["gpt-4o"].name == "GPT-4o tuned"
        assert openai_models["gpt-4o"].context_window == 256000
        assert openai_models["gpt-4.1-local"].name == "GPT-4.1 Local Proxy"
        assert openai_models["gpt-4.1-local"].supports_reasoning is True

        localproxy = providers["localproxy"]
        assert localproxy.name == "Local Proxy"
        assert localproxy.models[0].id == "qwen3:32b"
        assert localproxy.models[0].context_window == 131072

    @pytest.mark.asyncio
    async def test_effective_model_lookup_uses_configured_models(self, monkeypatch):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        config = WorkerConfig(
            providers={
                "localproxy": ProviderConfig(
                    type="openai_compat",
                    requires_api_key=False,
                    models={
                        "qwen3:32b": ProviderModelConfig(
                            name="Qwen3 32B",
                            context_window=131072,
                            input_price_per_m=0.25,
                            output_price_per_m=0.75,
                        )
                    },
                )
            }
        )

        model = await get_effective_model_info(config, "localproxy", "qwen3:32b")
        assert model is not None
        assert model.name == "Qwen3 32B"
        assert model.context_window == 131072
        assert model.input_price_per_m == 0.25

    @pytest.mark.asyncio
    async def test_local_provider_overrides_work_for_ollama_cloud_and_lmstudio(
        self,
        monkeypatch,
    ):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        config = WorkerConfig(
            providers={
                "ollama_cloud": ProviderConfig(
                    models={
                        "gpt-oss:20b": ProviderModelConfig(
                            name="gpt-oss 20B Cloud",
                            context_window=200000,
                            supports_reasoning=True,
                        )
                    }
                ),
                "lmstudio": ProviderConfig(
                    models={
                        "openai/gpt-oss-20b": ProviderModelConfig(
                            name="LM Studio GPT-OSS 20B",
                            context_window=131072,
                            max_output_tokens=8192,
                        )
                    }
                ),
            }
        )

        providers = await get_effective_provider_catalog(config)

        ollama_cloud = providers["ollama_cloud"]
        assert ollama_cloud.env == ("OLLAMA_API_KEY",)
        assert ollama_cloud.models[0].provider == "ollama_cloud"
        assert ollama_cloud.models[0].id == "gpt-oss:20b"
        assert ollama_cloud.models[0].supports_reasoning is True

        lmstudio = providers["lmstudio"]
        assert lmstudio.models[0].provider == "lmstudio"
        assert lmstudio.models[0].id == "openai/gpt-oss-20b"
        assert lmstudio.models[0].max_output_tokens == 8192

    @pytest.mark.asyncio
    async def test_direct_discovery_fetches_models_for_ollama_cloud_and_lmstudio(
        self,
        monkeypatch,
    ):
        from worker_ai.models import ModelInfo
        from worker_ai.models_catalog import ModelsCatalog
        from worker_ai.providers.lmstudio import LMStudioProvider
        from worker_ai.providers.ollama import OllamaProvider

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        async def _fake_ollama_discovery(self):
            return [
                ModelInfo(
                    id="gpt-oss:20b",
                    provider=self.name,
                    name="gpt-oss 20B",
                    context_window=200000,
                )
            ]

        async def _fake_lmstudio_discovery(self):
            return [
                ModelInfo(
                    id="qwen3-32b-instruct",
                    provider=self.name,
                    name="Qwen3 32B Instruct",
                    context_window=131072,
                )
            ]

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))
        monkeypatch.setattr(OllamaProvider, "list_models_direct", _fake_ollama_discovery)
        monkeypatch.setattr(LMStudioProvider, "list_models_direct", _fake_lmstudio_discovery)
        monkeypatch.setenv("OLLAMA_API_KEY", "ollama-token")

        providers = await get_effective_provider_catalog(WorkerConfig())

        assert providers["ollama_cloud"].models[0].id == "gpt-oss:20b"
        assert providers["ollama_cloud"].models[0].provider == "ollama_cloud"
        assert providers["lmstudio"].models[0].id == "qwen3-32b-instruct"
        assert providers["lmstudio"].models[0].provider == "lmstudio"

    @pytest.mark.asyncio
    async def test_catalog_aliases_map_models_to_canonical_provider_names(self, monkeypatch):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        providers = await get_effective_provider_catalog(WorkerConfig())

        together = providers["together"]
        fireworks = providers["fireworks"]

        assert together.models[0].provider == "together"
        assert together.models[0].id == "meta-llama/llama-3.3-70b-instruct-turbo"
        assert fireworks.models[0].provider == "fireworks"
        assert fireworks.models[0].id == "accounts/fireworks/models/llama-v3p1-8b-instruct"

    @pytest.mark.asyncio
    async def test_google_vertex_reuses_google_catalog_models(self, monkeypatch):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        providers = await get_effective_provider_catalog(WorkerConfig())
        google_vertex = providers["google_vertex"]

        assert google_vertex.models[0].provider == "google_vertex"
        assert google_vertex.models[0].id == "gemini-2.5-pro"

    @pytest.mark.asyncio
    async def test_vertex_anthropic_uses_builtin_models_when_catalog_has_no_entry(
        self,
        monkeypatch,
    ):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        providers = await get_effective_provider_catalog(WorkerConfig())
        vertex_anthropic = providers["vertex_anthropic"]
        vertex_models = {model.id: model for model in vertex_anthropic.models}

        assert vertex_models["claude-sonnet-4@20250514"].provider == "vertex_anthropic"
        assert vertex_models["claude-sonnet-4@20250514"].supports_reasoning is True
        assert vertex_models["claude-opus-4@20250514"].supports_tools is True

    @pytest.mark.asyncio
    async def test_azure_openai_uses_builtin_models_when_catalog_has_no_entry(
        self,
        monkeypatch,
    ):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        providers = await get_effective_provider_catalog(WorkerConfig())
        azure_openai = providers["azure_openai"]
        azure_models = {model.id: model for model in azure_openai.models}

        assert azure_models["gpt-4.1"].provider == "azure_openai"
        assert azure_models["gpt-4.1"].supports_tools is True
        assert azure_models["o3"].supports_reasoning is True

    @pytest.mark.asyncio
    async def test_bedrock_uses_builtin_models_when_catalog_has_no_entry(
        self,
        monkeypatch,
    ):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        providers = await get_effective_provider_catalog(WorkerConfig())
        bedrock = providers["bedrock"]
        bedrock_models = {model.id: model for model in bedrock.models}

        assert (
            bedrock_models["anthropic.claude-3-7-sonnet-20250219-v1:0"].provider
            == "bedrock"
        )
        assert (
            bedrock_models["anthropic.claude-3-7-sonnet-20250219-v1:0"].supports_reasoning
            is True
        )

    @pytest.mark.asyncio
    async def test_github_copilot_uses_builtin_models_when_catalog_has_no_entry(
        self,
        monkeypatch,
    ):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        providers = await get_effective_provider_catalog(WorkerConfig())
        github_copilot = providers["github_copilot"]
        copilot_models = {model.id: model for model in github_copilot.models}

        assert copilot_models["gpt-4.1"].provider == "github_copilot"
        assert copilot_models["claude-sonnet-4"].supports_tools is True
        assert copilot_models["gemini-2.5-pro"].supports_reasoning is True

    @pytest.mark.asyncio
    async def test_effective_model_lookup_accepts_alias_provider_names(self, monkeypatch):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        model = await get_effective_model_info(
            WorkerConfig(),
            "fireworks-ai",
            "accounts/fireworks/models/llama-v3p1-8b-instruct",
        )

        assert model is not None
        assert model.provider == "fireworks"

    @pytest.mark.asyncio
    async def test_effective_model_lookup_accepts_google_vertex_alias(self, monkeypatch):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        model = await get_effective_model_info(
            WorkerConfig(),
            "google-vertex",
            "gemini-2.5-pro",
        )

        assert model is not None
        assert model.provider == "google_vertex"

    @pytest.mark.asyncio
    async def test_effective_model_lookup_accepts_vertex_anthropic_alias(self, monkeypatch):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        model = await get_effective_model_info(
            WorkerConfig(),
            "anthropic_vertex",
            "claude-sonnet-4@20250514",
        )

        assert model is not None
        assert model.provider == "vertex_anthropic"

    @pytest.mark.asyncio
    async def test_effective_model_lookup_accepts_azure_openai_provider(self, monkeypatch):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        model = await get_effective_model_info(
            WorkerConfig(),
            "azure_openai",
            "gpt-4.1",
        )

        assert model is not None
        assert model.provider == "azure_openai"

    @pytest.mark.asyncio
    async def test_effective_model_lookup_accepts_bedrock_provider(self, monkeypatch):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        model = await get_effective_model_info(
            WorkerConfig(),
            "bedrock",
            "anthropic.claude-3-7-sonnet-20250219-v1:0",
        )

        assert model is not None
        assert model.provider == "bedrock"

    @pytest.mark.asyncio
    async def test_effective_model_lookup_accepts_github_copilot_alias(self, monkeypatch):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        model = await get_effective_model_info(
            WorkerConfig(),
            "github-copilot",
            "gpt-4.1",
        )

        assert model is not None
        assert model.provider == "github_copilot"

    @pytest.mark.asyncio
    async def test_effective_model_lookup_accepts_ollama_cloud_alias(self, monkeypatch):
        from worker_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))

        config = WorkerConfig(
            providers={
                "ollama_cloud": ProviderConfig(
                    models={
                        "gpt-oss:20b": ProviderModelConfig(
                            name="gpt-oss 20B Cloud",
                            context_window=200000,
                        )
                    }
                )
            }
        )

        model = await get_effective_model_info(
            config,
            "ollama-cloud",
            "gpt-oss:20b",
        )

        assert model is not None
        assert model.provider == "ollama_cloud"

    @pytest.mark.asyncio
    async def test_effective_model_lookup_accepts_direct_discovered_lmstudio_alias(
        self,
        monkeypatch,
    ):
        from worker_ai.models import ModelInfo
        from worker_ai.models_catalog import ModelsCatalog
        from worker_ai.providers.lmstudio import LMStudioProvider

        async def _fake_fetch(cls):
            return _SAMPLE_PROVIDER_CATALOG

        async def _fake_lmstudio_discovery(self):
            return [
                ModelInfo(
                    id="qwen3-32b-instruct",
                    provider=self.name,
                    name="Qwen3 32B Instruct",
                    context_window=131072,
                )
            ]

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))
        monkeypatch.setattr(LMStudioProvider, "list_models_direct", _fake_lmstudio_discovery)

        model = await get_effective_model_info(
            WorkerConfig(),
            "lm-studio",
            "qwen3-32b-instruct",
        )

        assert model is not None
        assert model.provider == "lmstudio"
