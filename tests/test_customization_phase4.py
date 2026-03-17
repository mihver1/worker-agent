"""Phase 4 — Customization tests: prompts, skills, themes, keybindings."""

from __future__ import annotations

import textwrap

import pytest
from artel_core.agent import AgentSession

# ── Prompt templates ─────────────────────────────────────────────


class TestPrompts:
    """Tests for prompt template loading and rendering."""

    def test_load_prompts_from_project(self, tmp_path):
        """Project .artel/prompts/*.md are loaded."""
        from artel_core.prompts import load_prompts

        prompts_dir = tmp_path / ".artel" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "review.md").write_text("Review this code: {{input}}")
        (prompts_dir / "explain.md").write_text("Explain: {{input}}")

        prompts = load_prompts(str(tmp_path))
        assert "review" in prompts
        assert "explain" in prompts
        assert prompts["review"] == "Review this code: {{input}}"

    def test_load_prompts_global(self, tmp_path, monkeypatch):
        """Global prompts from CONFIG_DIR/prompts/ are loaded."""
        import artel_core.config as cfg_mod
        from artel_core import prompts as prompts_mod

        fake_config = tmp_path / "config"
        fake_config.mkdir()

        global_dir = fake_config / "prompts"
        global_dir.mkdir()
        monkeypatch.setattr(cfg_mod, "PROMPTS_DIR", global_dir)
        monkeypatch.setattr(cfg_mod, "LEGACY_PROMPTS_DIR", tmp_path / "legacy-prompts")
        (global_dir / "global_tpl.md").write_text("Global template")

        loaded = prompts_mod.load_prompts("")
        assert "global_tpl" in loaded

    def test_project_overrides_global(self, tmp_path, monkeypatch):
        """Project prompts override global prompts with the same name."""
        import artel_core.config as cfg_mod
        from artel_core import prompts as prompts_mod

        fake_config = tmp_path / "config"
        fake_config.mkdir()

        global_dir = fake_config / "prompts"
        global_dir.mkdir()
        monkeypatch.setattr(cfg_mod, "PROMPTS_DIR", global_dir)
        monkeypatch.setattr(cfg_mod, "LEGACY_PROMPTS_DIR", tmp_path / "legacy-prompts")
        (global_dir / "review.md").write_text("Global review")

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        project_prompts = project_dir / ".artel" / "prompts"
        project_prompts.mkdir(parents=True)
        (project_prompts / "review.md").write_text("Project review")

        loaded = prompts_mod.load_prompts(str(project_dir))
        assert loaded["review"] == "Project review"

    def test_render_prompt_substitution(self):
        """{{variable}} placeholders are replaced."""
        from artel_core.prompts import render_prompt

        tpl = "Hello {{name}}, please review {{file}}"
        result = render_prompt(tpl, {"name": "Alice", "file": "main.py"})
        assert result == "Hello Alice, please review main.py"

    def test_render_prompt_unknown_vars_preserved(self):
        """Unknown variables are left as-is."""
        from artel_core.prompts import render_prompt

        tpl = "Hello {{name}}, see {{unknown}}"
        result = render_prompt(tpl, {"name": "Bob"})
        assert result == "Hello Bob, see {{unknown}}"

    def test_render_prompt_no_vars(self):
        """Template without variables is returned unchanged."""
        from artel_core.prompts import render_prompt

        tpl = "Just a plain template"
        assert render_prompt(tpl, None) == tpl
        assert render_prompt(tpl, {}) == tpl

    def test_list_prompts(self, tmp_path):
        """list_prompts returns sorted names."""
        from artel_core.prompts import list_prompts

        prompts_dir = tmp_path / ".artel" / "prompts"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "beta.md").write_text("B")
        (prompts_dir / "alpha.md").write_text("A")

        names = list_prompts(str(tmp_path))
        assert names == ["alpha", "beta"]


# ── Skills ────────────────────────────────────────────────────────


class TestSkills:
    """Tests for the skills system."""

    def test_load_skills_with_frontmatter(self, tmp_path):
        """Skills with YAML-like frontmatter are parsed correctly."""
        from artel_core.skills import load_skills

        skills_dir = tmp_path / ".artel" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "testing.md").write_text(
            textwrap.dedent("""\
            ---
            name: python-testing
            description: Best practices for pytest
            ---

            # Python Testing

            Use fixtures, parametrize, etc.
        """)
        )

        skills = load_skills(str(tmp_path))
        assert "python-testing" in skills
        sk = skills["python-testing"]
        assert sk.description == "Best practices for pytest"
        assert "# Python Testing" in sk.content

    def test_load_skills_without_frontmatter(self, tmp_path):
        """Skills without frontmatter use filename as name."""
        from artel_core.skills import load_skills

        skills_dir = tmp_path / ".artel" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "git-workflow.md").write_text(
            "# Git Workflow\n\nAlways rebase before merging."
        )

        skills = load_skills(str(tmp_path))
        assert "git-workflow" in skills
        sk = skills["git-workflow"]
        assert sk.name == "git-workflow"
        # Description from first non-heading line
        assert "Always rebase" in sk.description

    def test_project_skills_override_global(self, tmp_path, monkeypatch):
        """Project skills override global skills with the same name."""
        import artel_core.config as cfg_mod
        from artel_core import skills as skills_mod

        fake_config = tmp_path / "config"
        fake_config.mkdir()

        global_dir = fake_config / "skills"
        global_dir.mkdir()
        monkeypatch.setattr(cfg_mod, "SKILLS_DIR", global_dir)
        monkeypatch.setattr(cfg_mod, "LEGACY_SKILLS_DIR", tmp_path / "legacy-skills")
        (global_dir / "docker.md").write_text(
            "---\nname: docker\ndescription: Global\n---\nGlobal content"
        )

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        project_skills = project_dir / ".artel" / "skills"
        project_skills.mkdir(parents=True)
        (project_skills / "docker.md").write_text(
            "---\nname: docker\ndescription: Project\n---\nProject content"
        )

        skills = skills_mod.load_skills(str(project_dir))
        assert skills["docker"].description == "Project"
        assert "Project content" in skills["docker"].content

    def test_build_skills_header(self, tmp_path):
        """Skills header contains all skill names and descriptions."""
        from artel_core.skills import build_skills_header, load_skills

        skills_dir = tmp_path / ".artel" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "a.md").write_text(
            "---\nname: alpha\ndescription: First skill\n---\nContent A"
        )
        (skills_dir / "b.md").write_text(
            "---\nname: beta\ndescription: Second skill\n---\nContent B"
        )

        skills = load_skills(str(tmp_path))
        header = build_skills_header(skills)

        assert "## Available Skills" in header
        assert "**alpha**" in header
        assert "First skill" in header
        assert "**beta**" in header
        assert "Second skill" in header

    def test_build_skills_header_empty(self):
        """Empty skills dict produces empty header."""
        from artel_core.skills import build_skills_header

        assert build_skills_header({}) == ""

    def test_inject_skill(self, tmp_path):
        """inject_skill appends skill content to system prompt."""
        from artel_core.skills import Skill, inject_skill

        skill = Skill(
            name="test-skill",
            description="A test",
            content="# Test Skill\n\nDo this and that.",
        )
        result = inject_skill("Base system prompt.", skill)
        assert result.startswith("Base system prompt.")
        assert "[Skill: test-skill]" in result
        assert "Do this and that." in result

    def test_list_skills(self, tmp_path):
        """list_skills returns sorted names."""
        from artel_core.skills import list_skills

        skills_dir = tmp_path / ".artel" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "z.md").write_text("---\nname: zulu\n---\nZ")
        (skills_dir / "a.md").write_text("---\nname: alpha\n---\nA")

        names = list_skills(str(tmp_path))
        assert names == ["alpha", "zulu"]

    @pytest.mark.asyncio
    async def test_skills_headers_in_system_prompt(self, tmp_path):
        """AgentSession._build_system_prompt includes skills headers."""
        skills_dir = tmp_path / ".artel" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "docker.md").write_text(
            "---\n"
            "name: docker\n"
            "description: Docker best practices\n"
            "---\n"
            "# Docker\n"
            "Use multi-stage builds."
        )

        prompt = AgentSession._build_system_prompt("", str(tmp_path))
        assert "## Available Skills" in prompt
        assert "docker" in prompt
        assert "Docker best practices" in prompt
        # Full content should NOT be in system prompt (only headers)
        assert "multi-stage builds" not in prompt


# ── Themes ────────────────────────────────────────────────────────


class TestThemes:
    """Tests for the theme system."""

    def test_builtin_themes_available(self):
        """All four built-in themes exist."""
        from artel_tui.themes import BUILTIN_THEMES

        assert "dark" in BUILTIN_THEMES
        assert "light" in BUILTIN_THEMES
        assert "monokai" in BUILTIN_THEMES
        assert "dracula" in BUILTIN_THEMES

    def test_builtin_themes_valid_css(self):
        """Built-in themes contain expected CSS selectors."""
        from artel_tui.themes import BUILTIN_THEMES

        for name, css in BUILTIN_THEMES.items():
            assert "Screen" in css, f"{name} missing Screen selector"
            assert ".user-message" in css, f"{name} missing .user-message"
            assert ".error-message" in css, f"{name} missing .error-message"

    def test_user_theme_override(self, tmp_path, monkeypatch):
        """User themes override built-in ones with the same name."""
        from artel_tui import themes as themes_mod

        fake_config = tmp_path / "config"
        fake_config.mkdir()
        monkeypatch.setattr(themes_mod, "CONFIG_DIR", fake_config)
        monkeypatch.setattr(themes_mod, "LEGACY_CONFIG_DIR", tmp_path / "legacy-config")

        themes_dir = fake_config / "themes"
        themes_dir.mkdir()
        (themes_dir / "dark.tcss").write_text("Screen { background: #000; }")

        loaded = themes_mod.load_themes("")
        assert loaded["dark"] == "Screen { background: #000; }"

    def test_user_custom_theme(self, tmp_path, monkeypatch):
        """User can add entirely new themes."""
        from artel_tui import themes as themes_mod

        fake_config = tmp_path / "config"
        fake_config.mkdir()
        monkeypatch.setattr(themes_mod, "CONFIG_DIR", fake_config)
        monkeypatch.setattr(themes_mod, "LEGACY_CONFIG_DIR", tmp_path / "legacy-config")

        themes_dir = fake_config / "themes"
        themes_dir.mkdir()
        (themes_dir / "solarized.tcss").write_text("Screen { background: #002b36; }")

        loaded = themes_mod.load_themes("")
        assert "solarized" in loaded

    def test_list_themes(self, tmp_path, monkeypatch):
        """list_themes includes built-in and user themes."""
        from artel_tui import themes as themes_mod

        fake_config = tmp_path / "config"
        fake_config.mkdir()
        monkeypatch.setattr(themes_mod, "CONFIG_DIR", fake_config)
        monkeypatch.setattr(themes_mod, "LEGACY_CONFIG_DIR", tmp_path / "legacy-config")

        themes_dir = fake_config / "themes"
        themes_dir.mkdir()
        (themes_dir / "custom.tcss").write_text("Screen {}")

        names = themes_mod.list_themes("")
        assert "dark" in names
        assert "custom" in names

    def test_project_theme_override(self, tmp_path, monkeypatch):
        """Project themes override global user themes."""
        from artel_tui import themes as themes_mod

        fake_config = tmp_path / "config"
        fake_config.mkdir()
        monkeypatch.setattr(themes_mod, "CONFIG_DIR", fake_config)
        monkeypatch.setattr(themes_mod, "LEGACY_CONFIG_DIR", tmp_path / "legacy-config")

        global_themes = fake_config / "themes"
        global_themes.mkdir()
        (global_themes / "custom.tcss").write_text("Screen { background: #111; }")

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        project_themes = project_dir / ".artel" / "themes"
        project_themes.mkdir(parents=True)
        (project_themes / "custom.tcss").write_text("Screen { background: #222; }")

        loaded = themes_mod.load_themes(str(project_dir))
        assert loaded["custom"] == "Screen { background: #222; }"


# ── Keybindings config ────────────────────────────────────────────


class TestKeybindingsConfig:
    """Tests for keybindings configuration."""

    def test_default_empty(self):
        """Default keybindings config has no bindings."""
        from artel_core.config import KeybindingsConfig

        kb = KeybindingsConfig()
        assert kb.bindings == {}

    def test_keybindings_in_config(self, tmp_path):
        """Keybindings section is parsed from config.toml."""
        from artel_core.config import load_config

        config_dir = tmp_path / ".artel"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            textwrap.dedent("""\
            [keybindings]
            [keybindings.bindings]
            "ctrl+k" = "clear"
            "ctrl+r" = "resume"
        """)
        )

        config = load_config(str(tmp_path))
        assert config.keybindings.bindings == {
            "ctrl+k": "clear",
            "ctrl+r": "resume",
        }

    def test_keybindings_missing_section(self, tmp_path):
        """Config without keybindings section uses defaults."""
        from artel_core.config import load_config

        config_dir = tmp_path / ".artel"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('[agent]\nmodel = "mock/m"\n')

        config = load_config(str(tmp_path))
        assert config.keybindings.bindings == {}


# ── Skills frontmatter parsing ────────────────────────────────────


class TestFrontmatterParsing:
    """Tests for the frontmatter parser."""

    def test_parse_valid(self):
        from artel_core.skills import _parse_frontmatter

        raw = "---\nname: foo\ndescription: bar\n---\n\n# Body"
        meta, body = _parse_frontmatter(raw)
        assert meta == {"name": "foo", "description": "bar"}
        assert body == "# Body"

    def test_parse_no_frontmatter(self):
        from artel_core.skills import _parse_frontmatter

        raw = "# Just a heading\n\nSome text."
        meta, body = _parse_frontmatter(raw)
        assert meta == {}
        assert body == raw.strip()

    def test_parse_empty_frontmatter(self):
        from artel_core.skills import _parse_frontmatter

        raw = "---\n---\n\nBody text"
        meta, body = _parse_frontmatter(raw)
        assert meta == {}
        assert body == "Body text"

    def test_parse_multiline_ignored(self):
        """Only key: value lines are parsed."""
        from artel_core.skills import _parse_frontmatter

        raw = "---\nname: test\nsome random line\ndescription: desc\n---\nBody"
        meta, body = _parse_frontmatter(raw)
        assert meta["name"] == "test"
        assert meta["description"] == "desc"
        assert body == "Body"
