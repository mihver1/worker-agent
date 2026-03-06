"""Tests for LM Studio provider behavior and direct model discovery."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock, patch

import pytest
from worker_ai.providers import create_default_registry
from worker_ai.providers.lmstudio import LMStudioProvider


class TestLMStudioProviderRegistry:
    @pytest.mark.asyncio
    async def test_registry_exposes_dedicated_lmstudio_provider(self):
        registry = create_default_registry()

        provider = registry.create("lmstudio")

        assert isinstance(provider, LMStudioProvider)
        assert provider._base_url == "http://127.0.0.1:1234/v1"

        await provider.close()


class TestLMStudioProviderDiscovery:
    @pytest.mark.asyncio
    async def test_list_models_direct_uses_native_lmstudio_models_endpoint(self):
        provider = LMStudioProvider()

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "type": "llm",
                    "id": "qwen3-32b-instruct",
                    "display_name": "Qwen3 32B Instruct",
                    "max_context_length": 131072,
                    "trained_for_tool_use": True,
                    "vision": False,
                },
                {
                    "type": "embedding",
                    "id": "nomic-embed-text",
                    "display_name": "Nomic Embed Text",
                },
            ]
        }

        with patch.object(
            provider._client,
            "get",
            new=AsyncMock(return_value=mock_response),
        ) as mock_get:
            models = await provider.list_models_direct()

        assert len(models) == 1
        assert models[0].id == "qwen3-32b-instruct"
        assert models[0].name == "Qwen3 32B Instruct"
        assert models[0].context_window == 131072
        assert models[0].supports_tools is True
        assert mock_get.call_args.args == ("http://127.0.0.1:1234/api/v1/models",)

        await provider.close()
