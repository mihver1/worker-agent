"""Tests for models.dev catalog parsing helpers."""

from __future__ import annotations

from artel_ai.models_catalog import _parse_provider


class TestParseProvider:
    def test_provider_api_base_url_is_preserved(self):
        provider = _parse_provider(
            "fireworks-ai",
            {
                "name": "Fireworks AI",
                "env": ["FIREWORKS_API_KEY"],
                "api": "https://api.fireworks.ai/inference/v1/",
                "models": {
                    "accounts/fireworks/models/llama-v3p1-8b-instruct": {
                        "name": "Llama v3.1 8B Instruct",
                        "tool_call": True,
                        "reasoning": False,
                        "limit": {"context": 131072, "output": 8192},
                        "cost": {"input": 0.2, "output": 0.2},
                        "modalities": {"input": ["text"], "output": ["text"]},
                    }
                },
            },
        )

        assert provider.api_base_url == "https://api.fireworks.ai/inference/v1/"
        assert provider.models[0].id == "accounts/fireworks/models/llama-v3p1-8b-instruct"
