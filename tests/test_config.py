"""Tests for configuration system."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from worker_core.config import (
    WorkerConfig,
    generate_global_config,
    generate_project_config,
    load_config,
    resolve_model,
    _deep_merge,
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


class TestLoadConfig:
    def test_default_config(self):
        """Loading from a dir with no config files → defaults."""
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
