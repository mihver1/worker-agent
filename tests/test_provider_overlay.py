"""Tests for persistent provider overlay hygiene."""

from __future__ import annotations

import json

from worker_server.provider_overlay import load_provider_overlay


class TestProviderOverlay:
    def test_load_provider_overlay_strips_placeholder_api_key_and_rewrites_file(
        self,
        tmp_path,
    ):
        overlay_path = tmp_path / "server-provider-overlay.json"
        overlay_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "openai": {
                            "api_key": "sk-remote",
                            "base_url": "https://api.openai.com/v1",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        overlay = load_provider_overlay(overlay_path)

        assert overlay["openai"].api_key == ""
        assert overlay["openai"].base_url == "https://api.openai.com/v1"
        assert json.loads(overlay_path.read_text(encoding="utf-8")) == {
            "providers": {"openai": {"base_url": "https://api.openai.com/v1"}}
        }
