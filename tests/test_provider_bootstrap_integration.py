"""Integration coverage for newly added built-in provider specs."""

from __future__ import annotations

import pytest
from artel_core.config import ArtelConfig, ProviderConfig, ProviderModelConfig


class TestRuntimeBootstrapNewProviders:
    @pytest.mark.asyncio
    async def test_bootstrap_runtime_supports_minimax(self, tmp_path, monkeypatch):
        from artel_ai.providers.anthropic import AnthropicProvider
        from artel_core.bootstrap import bootstrap_runtime
        from artel_core.cli import _resolve_api_key

        monkeypatch.setenv("MINIMAX_API_KEY", "minimax_env_token")

        runtime = await bootstrap_runtime(
            ArtelConfig(
                providers={
                    "minimax": ProviderConfig(
                        models={
                            "MiniMax-M2.5": ProviderModelConfig(
                                context_window=204800,
                            )
                        }
                    )
                }
            ),
            "minimax",
            "MiniMax-M2.5",
            project_dir=str(tmp_path),
            resolve_api_key=_resolve_api_key,
            include_extensions=False,
            runtime="local",
        )

        assert isinstance(runtime.provider, AnthropicProvider)
        assert runtime.provider.api_key == "minimax_env_token"
        assert runtime.provider._base_url == "https://api.minimax.io/anthropic/v1"
        assert runtime.context_window == 204800

        await runtime.provider.close()

    @pytest.mark.asyncio
    async def test_bootstrap_runtime_supports_zai_alias(self, tmp_path, monkeypatch):
        from artel_ai.providers.openai_compat import OpenAICompatibleProvider
        from artel_core.bootstrap import bootstrap_runtime
        from artel_core.cli import _resolve_api_key

        monkeypatch.setenv("ZHIPU_API_KEY", "zhipu_env_token")

        runtime = await bootstrap_runtime(
            ArtelConfig(
                providers={
                    "zai": ProviderConfig(
                        models={
                            "glm-5": ProviderModelConfig(
                                context_window=128000,
                            )
                        }
                    )
                }
            ),
            "z.ai",
            "glm-5",
            project_dir=str(tmp_path),
            resolve_api_key=_resolve_api_key,
            include_extensions=False,
            runtime="local",
        )

        assert isinstance(runtime.provider, OpenAICompatibleProvider)
        assert runtime.provider.api_key == "zhipu_env_token"
        assert runtime.provider._base_url == "https://api.z.ai/api/paas/v4"
        assert runtime.context_window == 128000

        await runtime.provider.close()
