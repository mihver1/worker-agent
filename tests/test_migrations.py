"""Tests for Artel first-run bootstrap and Artel state migration."""

from __future__ import annotations

import json

from artel_core.migrations import CURRENT_VERSION, check_and_migrate


def _patch_config_paths(monkeypatch, tmp_path):
    import artel_core.config as cfg_mod

    artel_root = tmp_path / ".config" / "artel"
    legacy_root = tmp_path / ".config" / "artel"

    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", artel_root)
    monkeypatch.setattr(cfg_mod, "LEGACY_CONFIG_DIR", legacy_root)
    monkeypatch.setattr(cfg_mod, "GLOBAL_CONFIG", artel_root / "config.toml")
    monkeypatch.setattr(cfg_mod, "LEGACY_GLOBAL_CONFIG", legacy_root / "config.toml")
    monkeypatch.setattr(cfg_mod, "AUTH_FILE", artel_root / "auth.json")
    monkeypatch.setattr(cfg_mod, "LEGACY_AUTH_FILE", legacy_root / "auth.json")
    monkeypatch.setattr(cfg_mod, "SESSIONS_DB", artel_root / "sessions.db")
    monkeypatch.setattr(cfg_mod, "LEGACY_SESSIONS_DB", legacy_root / "sessions.db")
    monkeypatch.setattr(cfg_mod, "GLOBAL_AGENTS_FILE", artel_root / "AGENTS.md")
    monkeypatch.setattr(cfg_mod, "LEGACY_GLOBAL_AGENTS_FILE", legacy_root / "AGENTS.md")
    monkeypatch.setattr(cfg_mod, "GLOBAL_SYSTEM_OVERRIDE", artel_root / "SYSTEM.md")
    monkeypatch.setattr(
        cfg_mod,
        "LEGACY_GLOBAL_SYSTEM_OVERRIDE",
        legacy_root / "SYSTEM.md",
    )
    monkeypatch.setattr(cfg_mod, "GLOBAL_APPEND_SYSTEM", artel_root / "APPEND_SYSTEM.md")
    monkeypatch.setattr(
        cfg_mod,
        "LEGACY_GLOBAL_APPEND_SYSTEM",
        legacy_root / "APPEND_SYSTEM.md",
    )
    monkeypatch.setattr(cfg_mod, "PROMPTS_DIR", artel_root / "prompts")
    monkeypatch.setattr(cfg_mod, "LEGACY_PROMPTS_DIR", legacy_root / "prompts")
    monkeypatch.setattr(cfg_mod, "SKILLS_DIR", artel_root / "skills")
    monkeypatch.setattr(cfg_mod, "LEGACY_SKILLS_DIR", legacy_root / "skills")
    monkeypatch.setattr(
        cfg_mod,
        "EXTENSIONS_MANIFEST",
        artel_root / "extensions.lock",
    )
    monkeypatch.setattr(
        cfg_mod,
        "LEGACY_EXTENSIONS_MANIFEST",
        legacy_root / "extensions.lock",
    )
    monkeypatch.setattr(cfg_mod, "REGISTRY_CACHE_DIR", artel_root / "registry_cache")
    monkeypatch.setattr(
        cfg_mod,
        "LEGACY_REGISTRY_CACHE_DIR",
        legacy_root / "registry_cache",
    )
    monkeypatch.setattr(
        cfg_mod,
        "SERVER_PROVIDER_OVERLAY_PATH",
        artel_root / "server-provider-overlay.json",
    )
    monkeypatch.setattr(
        cfg_mod,
        "LEGACY_SERVER_PROVIDER_OVERLAY_PATH",
        legacy_root / "server-provider-overlay.json",
    )
    monkeypatch.setattr(cfg_mod, "GLOBAL_MCP_PATH", artel_root / "mcp.json")
    monkeypatch.setattr(cfg_mod, "LEGACY_GLOBAL_MCP_PATH", legacy_root / "mcp.json")
    monkeypatch.setattr(cfg_mod, "GLOBAL_STATE_FILE", artel_root / "state.json")
    monkeypatch.setattr(
        cfg_mod,
        "LEGACY_GLOBAL_STATE_FILE",
        legacy_root / "state.json",
    )

    return artel_root, legacy_root


class TestMigrations:
    def test_check_and_migrate_copies_global_and_project_artel_state(
        self,
        tmp_path,
        monkeypatch,
    ):
        artel_root, legacy_root = _patch_config_paths(monkeypatch, tmp_path)
        project_dir = tmp_path / "project"
        legacy_project_dir = project_dir / ".artel"

        legacy_root.mkdir(parents=True)
        (legacy_root / "config.toml").write_text(
            '[agent]\nmodel = "openai/gpt-4.1"\n',
            encoding="utf-8",
        )
        (legacy_root / "auth.json").write_text(
            json.dumps({"anthropic": {"access_token": "oauth_token"}}),
            encoding="utf-8",
        )
        (legacy_root / "sessions.db").write_text("sqlite", encoding="utf-8")
        (legacy_root / "extensions.lock").write_text("[]", encoding="utf-8")
        (legacy_root / "server-provider-overlay.json").write_text(
            json.dumps({"providers": {"openai": {"base_url": "https://api.openai.com/v1"}}}),
            encoding="utf-8",
        )
        (legacy_root / "AGENTS.md").write_text("Global instructions\n", encoding="utf-8")
        (legacy_root / "SYSTEM.md").write_text("Global system\n", encoding="utf-8")
        (legacy_root / "APPEND_SYSTEM.md").write_text("Append system\n", encoding="utf-8")
        (legacy_root / "prompts").mkdir()
        (legacy_root / "prompts" / "review.md").write_text("Review prompt\n", encoding="utf-8")
        (legacy_root / "skills").mkdir()
        (legacy_root / "skills" / "testing.md").write_text("Testing skill\n", encoding="utf-8")
        (legacy_root / "registry_cache").mkdir()
        (legacy_root / "registry_cache" / "cache.json").write_text("{}", encoding="utf-8")
        (legacy_root / "mcp.json").write_text(
            json.dumps({"mcpServers": {"global-demo": {"command": "uvx"}}}),
            encoding="utf-8",
        )
        (legacy_root / "state.json").write_text(
            json.dumps({"config_version": 1, "legacy_marker": "present"}),
            encoding="utf-8",
        )

        legacy_project_dir.mkdir(parents=True)
        (legacy_project_dir / "config.toml").write_text(
            '[agent]\nmodel = "openai/gpt-4.1-mini"\n',
            encoding="utf-8",
        )
        (legacy_project_dir / "AGENTS.md").write_text("Project instructions\n", encoding="utf-8")
        (legacy_project_dir / "SYSTEM.md").write_text("Project system\n", encoding="utf-8")
        (legacy_project_dir / "APPEND_SYSTEM.md").write_text("Project append\n", encoding="utf-8")
        (legacy_project_dir / "server.json").write_text(
            json.dumps({"remote_url": "ws://127.0.0.1:7432", "auth_token": "artel_token"}),
            encoding="utf-8",
        )
        (legacy_project_dir / "mcp.json").write_text(
            json.dumps({"servers": [{"name": "example"}]}),
            encoding="utf-8",
        )
        (legacy_project_dir / "prompts").mkdir()
        (legacy_project_dir / "prompts" / "project.md").write_text(
            "Project prompt\n",
            encoding="utf-8",
        )
        (legacy_project_dir / "skills").mkdir()
        (legacy_project_dir / "skills" / "project-skill.md").write_text(
            "Project skill\n",
            encoding="utf-8",
        )

        check_and_migrate(str(project_dir))

        assert (artel_root / "config.toml").read_text(encoding="utf-8") == (
            legacy_root / "config.toml"
        ).read_text(encoding="utf-8")
        assert (artel_root / "auth.json").exists()
        assert (artel_root / "mcp.json").exists()
        assert (artel_root / "prompts" / "review.md").read_text(
            encoding="utf-8"
        ) == "Review prompt\n"
        assert (artel_root / "skills" / "testing.md").read_text(
            encoding="utf-8"
        ) == "Testing skill\n"
        assert (project_dir / ".artel" / "config.toml").read_text(encoding="utf-8") == (
            legacy_project_dir / "config.toml"
        ).read_text(encoding="utf-8")
        assert (project_dir / ".artel" / "AGENTS.md").read_text(
            encoding="utf-8"
        ) == "Project instructions\n"
        assert (project_dir / ".artel" / "server.json").exists()
        assert (project_dir / ".artel" / "mcp.json").exists()
        assert (project_dir / ".artel" / "prompts" / "project.md").exists()
        assert (project_dir / ".artel" / "skills" / "project-skill.md").exists()

        # Copy migration is non-destructive.
        assert (legacy_root / "config.toml").exists()
        assert (legacy_project_dir / "AGENTS.md").exists()

        state = json.loads((artel_root / "state.json").read_text(encoding="utf-8"))
        assert state["config_version"] == CURRENT_VERSION
        assert state["legacy_marker"] == "present"
        assert "config.toml" in state["artel_migrations"]["artel_global_to_artel"]["copied"]
        assert str(project_dir.resolve()) in state["artel_project_migrations"]

        # Idempotent re-run.
        check_and_migrate(str(project_dir))
        assert (project_dir / ".artel" / "AGENTS.md").read_text(
            encoding="utf-8"
        ) == "Project instructions\n"


class TestArtelBootstrap:
    def test_bootstrap_artel_runs_migrations_for_resolved_project_dir(
        self,
        tmp_path,
        monkeypatch,
    ):
        import artel_core.artel_bootstrap as bootstrap_mod

        seen: list[str | None] = []
        monkeypatch.setattr(
            bootstrap_mod,
            "check_and_migrate",
            lambda project_dir=None: seen.append(project_dir),
        )
        raw_project_dir = tmp_path / "nested" / ".." / "project"
        result = bootstrap_mod.bootstrap_artel(str(raw_project_dir))
        resolved = str(raw_project_dir.resolve())

        assert result.project_dir == resolved
        assert result.cmux_required is False
        assert result.cmux_preflight is None
        assert seen == [resolved]

    def test_bootstrap_artel_skips_cmux_preflight_for_non_interactive_command(
        self, tmp_path, monkeypatch
    ):
        import artel_core.artel_bootstrap as bootstrap_mod

        seen: list[str | None] = []
        monkeypatch.setattr(
            bootstrap_mod,
            "check_and_migrate",
            lambda project_dir=None: seen.append(project_dir),
        )

        result = bootstrap_mod.bootstrap_artel(
            str(tmp_path / "project"),
            command_name="serve",
        )

        assert result.project_dir == str((tmp_path / "project").resolve())
        assert result.cmux_required is False
        assert result.cmux_preflight is None
        assert seen == [result.project_dir]

    def test_bootstrap_artel_skips_cmux_preflight_for_connect_command(self, tmp_path, monkeypatch):
        import artel_core.artel_bootstrap as bootstrap_mod

        monkeypatch.setattr(
            bootstrap_mod,
            "check_and_migrate",
            lambda project_dir=None: None,
        )

        result = bootstrap_mod.bootstrap_artel(
            str(tmp_path / "project"),
            command_name="connect",
        )

        assert result.cmux_required is False
        assert result.cmux_preflight is None
