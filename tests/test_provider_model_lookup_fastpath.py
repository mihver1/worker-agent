from __future__ import annotations

import pytest
from artel_core.config import ArtelConfig, ProviderConfig, ProviderModelConfig


class TestEffectiveModelLookupFastPath:
    @pytest.fixture(autouse=True)
    def _reset_catalog(self):
        from artel_ai.models_catalog import ModelsCatalog

        ModelsCatalog._data = None
        yield
        ModelsCatalog._data = None

    @pytest.mark.asyncio
    async def test_effective_model_lookup_fast_path_uses_catalog_entry(self, monkeypatch):
        import artel_core.provider_resolver as resolver_mod
        from artel_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return {
                "openai": {
                    "name": "OpenAI",
                    "env": ["OPENAI_API_KEY"],
                    "models": {
                        "gpt-fast": {
                            "name": "GPT Fast",
                            "tool_call": True,
                            "reasoning": True,
                            "limit": {"context": 123456, "output": 4096},
                            "cost": {"input": 1.5, "output": 4.5},
                            "modalities": {"input": ["text", "image"], "output": ["text"]},
                        }
                    },
                }
            }

        async def _unexpected_catalog_build(*args, **kwargs):
            raise AssertionError(
                "full provider catalog should not be built for direct model lookup"
            )

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))
        monkeypatch.setattr(
            resolver_mod, "get_effective_provider_catalog", _unexpected_catalog_build
        )

        model = await resolver_mod.get_effective_model_info(ArtelConfig(), "openai", "gpt-fast")

        assert model is not None
        assert model.provider == "openai"
        assert model.context_window == 123456
        assert model.input_price_per_m == 1.5
        assert model.supports_vision is True

    @pytest.mark.asyncio
    async def test_effective_model_lookup_applies_provider_override_without_full_catalog_build(
        self,
        monkeypatch,
    ):
        import artel_core.provider_resolver as resolver_mod
        from artel_ai.models_catalog import ModelsCatalog

        async def _fake_fetch(cls):
            return {
                "openai": {
                    "name": "OpenAI",
                    "env": ["OPENAI_API_KEY"],
                    "models": {
                        "gpt-fast": {
                            "name": "GPT Fast",
                            "tool_call": True,
                            "reasoning": False,
                            "limit": {"context": 100000, "output": 4096},
                            "cost": {"input": 1.0, "output": 2.0},
                            "modalities": {"input": ["text"], "output": ["text"]},
                        }
                    },
                }
            }

        async def _unexpected_catalog_build(*args, **kwargs):
            raise AssertionError(
                "full provider catalog should not be built for direct model lookup"
            )

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))
        monkeypatch.setattr(
            resolver_mod, "get_effective_provider_catalog", _unexpected_catalog_build
        )

        config = ArtelConfig(
            providers={
                "openai": ProviderConfig(
                    models={
                        "gpt-fast": ProviderModelConfig(
                            context_window=222222,
                            supports_reasoning=True,
                        )
                    }
                )
            }
        )

        model = await resolver_mod.get_effective_model_info(config, "openai", "gpt-fast")

        assert model is not None
        assert model.context_window == 222222
        assert model.supports_reasoning is True

    @pytest.mark.asyncio
    async def test_effective_model_lookup_uses_direct_discovery_fast_path_for_lmstudio_alias(
        self,
        monkeypatch,
    ):
        import artel_core.provider_resolver as resolver_mod
        from artel_ai.models import ModelInfo
        from artel_ai.models_catalog import ModelsCatalog
        from artel_ai.providers.lmstudio import LMStudioProvider

        async def _fake_fetch(cls):
            return {}

        async def _fake_direct(self):
            return [
                ModelInfo(
                    id="qwen-fast",
                    provider=self.name,
                    name="Qwen Fast",
                    context_window=131072,
                )
            ]

        async def _unexpected_catalog_build(*args, **kwargs):
            raise AssertionError(
                "full provider catalog should not be built for direct model lookup"
            )

        monkeypatch.setattr(ModelsCatalog, "_fetch_raw", classmethod(_fake_fetch))
        monkeypatch.setattr(LMStudioProvider, "list_models_direct", _fake_direct)
        monkeypatch.setattr(
            resolver_mod, "get_effective_provider_catalog", _unexpected_catalog_build
        )

        model = await resolver_mod.get_effective_model_info(
            ArtelConfig(), "lm-studio", "qwen-fast"
        )

        assert model is not None
        assert model.provider == "lmstudio"
        assert model.context_window == 131072
