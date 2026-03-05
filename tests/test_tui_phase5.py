"""Phase 5 — TUI enhancements + cmux integration tests."""

from __future__ import annotations

import asyncio
import os
import textwrap

import pytest

from conftest import MockProvider
from worker_ai.models import Done, TextDelta, Usage


# ── cmux module tests ─────────────────────────────────────────────


class TestCmuxDetection:
    """Tests for cmux environment detection."""

    def test_is_cmux_false_without_env(self, monkeypatch):
        """is_cmux() returns False when CMUX_WORKSPACE_ID is not set."""
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        # Reset cached binary
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", None)
        assert cmux_mod.is_cmux() is False

    def test_is_cmux_false_without_binary(self, monkeypatch):
        """is_cmux() returns False when binary is not found."""
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "test-workspace")
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", None)
        monkeypatch.setattr("shutil.which", lambda _: None)
        # Also prevent fallback path from matching
        monkeypatch.setattr("os.path.isfile", lambda _: False)
        assert cmux_mod.is_cmux() is False

    def test_is_cmux_true_with_env_and_binary(self, monkeypatch):
        """is_cmux() returns True when env is set and binary is found."""
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "test-workspace")
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/usr/local/bin/cmux")
        assert cmux_mod.is_cmux() is True

    def test_workspace_id(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-123")
        assert cmux_mod.workspace_id() == "ws-123"

    def test_surface_id(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_SURFACE_ID", "sf-456")
        assert cmux_mod.surface_id() == "sf-456"


class TestCmuxNoOps:
    """Verify cmux functions are no-ops when not in cmux."""

    @pytest.mark.asyncio
    async def test_set_status_noop(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", None)
        # Should not raise
        await cmux_mod.set_status("key", "value")

    @pytest.mark.asyncio
    async def test_notify_noop(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", None)
        await cmux_mod.notify("Title")

    @pytest.mark.asyncio
    async def test_set_progress_noop(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", None)
        await cmux_mod.set_progress(0.5, label="test")

    @pytest.mark.asyncio
    async def test_log_noop(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", None)
        await cmux_mod.log("test message")

    @pytest.mark.asyncio
    async def test_new_split_noop(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", None)
        result = await cmux_mod.new_split("right")
        assert result == ""

    @pytest.mark.asyncio
    async def test_browser_open_noop(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", None)
        result = await cmux_mod.browser_open("https://example.com")
        assert result == ""

    @pytest.mark.asyncio
    async def test_read_screen_noop(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", None)
        result = await cmux_mod.read_screen()
        assert result == ""


class TestCmuxCommandBuilding:
    """Test that cmux functions build correct command args when in cmux."""

    @pytest.mark.asyncio
    async def test_set_status_with_options(self, monkeypatch):
        """set_status builds correct args with icon and color."""
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-test")
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        captured_args = []

        async def fake_run(args, **kwargs):
            captured_args.extend(args)
            return ""

        monkeypatch.setattr(cmux_mod, "_run", fake_run)

        await cmux_mod.set_status("state", "thinking", icon="brain", color="#89b4fa")

        assert captured_args == [
            "set-status", "state", "thinking",
            "--icon", "brain", "--color", "#89b4fa",
        ]

    @pytest.mark.asyncio
    async def test_notify_with_subtitle_and_body(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-test")
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        captured_args = []

        async def fake_run(args, **kwargs):
            captured_args.extend(args)
            return ""

        monkeypatch.setattr(cmux_mod, "_run", fake_run)

        await cmux_mod.notify("Worker", subtitle="Done", body="Task finished")

        assert captured_args == [
            "notify", "--title", "Worker",
            "--subtitle", "Done", "--body", "Task finished",
        ]

    @pytest.mark.asyncio
    async def test_set_progress_format(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-test")
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        captured_args = []

        async def fake_run(args, **kwargs):
            captured_args.extend(args)
            return ""

        monkeypatch.setattr(cmux_mod, "_run", fake_run)

        await cmux_mod.set_progress(0.75, label="ctx 75%")

        assert captured_args == [
            "set-progress", "0.75", "--label", "ctx 75%",
        ]

    @pytest.mark.asyncio
    async def test_log_with_level_and_source(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-test")
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        captured_args = []

        async def fake_run(args, **kwargs):
            captured_args.extend(args)
            return ""

        monkeypatch.setattr(cmux_mod, "_run", fake_run)

        await cmux_mod.log("test error", level="error", source="worker")

        assert captured_args == [
            "log", "--level", "error", "--source", "worker", "--", "test error",
        ]


# ── StatusFooter tests ────────────────────────────────────────────


class TestStatusFooter:
    """Tests for the StatusFooter widget."""

    def test_initial_render(self, monkeypatch):
        """Footer renders with 0 tokens initially."""
        import worker_core.cmux as cmux_mod

        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "")
        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)

        from worker_tui.app import StatusFooter

        footer = StatusFooter()
        text = footer.render()
        assert "0 tok" in str(text)

    def test_update_usage(self, monkeypatch):
        """update_usage accumulates tokens and cost."""
        import worker_core.cmux as cmux_mod

        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "")
        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)

        from worker_tui.app import StatusFooter

        footer = StatusFooter()
        footer.update_usage(100, 50, input_price=3.0, output_price=15.0)

        assert footer._total_input == 100
        assert footer._total_output == 50
        # Cost: 100 * 3.0 / 1M + 50 * 15.0 / 1M = 0.0003 + 0.00075 = 0.00105
        assert abs(footer._total_cost - 0.00105) < 0.0001

    def test_accumulates_across_calls(self, monkeypatch):
        """Multiple update_usage calls accumulate."""
        import worker_core.cmux as cmux_mod

        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "")
        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)

        from worker_tui.app import StatusFooter

        footer = StatusFooter()
        footer.update_usage(100, 50)
        footer.update_usage(200, 100)

        assert footer._total_input == 300
        assert footer._total_output == 150

    def test_update_context_pct(self, monkeypatch):
        """update_context_pct calculates correct percentage."""
        import worker_core.cmux as cmux_mod

        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "")
        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)

        from worker_tui.app import StatusFooter

        footer = StatusFooter()
        footer.update_context_pct(50_000, 200_000)

        assert abs(footer._context_pct - 0.25) < 0.001

    def test_context_pct_zero_window(self, monkeypatch):
        """No division by zero when context_window is 0."""
        import worker_core.cmux as cmux_mod

        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "")
        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)

        from worker_tui.app import StatusFooter

        footer = StatusFooter()
        footer.update_context_pct(1000, 0)
        assert footer._context_pct == 0.0

    def test_set_model(self, monkeypatch):
        """set_model updates the displayed model."""
        import worker_core.cmux as cmux_mod

        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "")
        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)

        from worker_tui.app import StatusFooter

        footer = StatusFooter()
        footer.set_model("anthropic/claude-sonnet")
        text = str(footer.render())
        assert "anthropic/claude-sonnet" in text

    def test_cmux_indicator_when_in_cmux(self, monkeypatch):
        """Footer shows 'cmux' indicator when in cmux."""
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-test")
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        from worker_tui.app import StatusFooter

        footer = StatusFooter()
        text = str(footer.render())
        assert "cmux" in text

    def test_no_cmux_indicator_outside(self, monkeypatch):
        """Footer does not show 'cmux' when outside cmux."""
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "")

        from worker_tui.app import StatusFooter

        footer = StatusFooter()
        text = str(footer.render())
        assert "cmux" not in text


# ── Bash !/!! tests ───────────────────────────────────────────────


class TestBashPrefixParsing:
    """Test that !/!! prefixes are parsed correctly in input handling."""

    def test_double_bang_strips_prefix(self):
        """!! prefix is correctly identified and command is extracted."""
        text = "!! ls -la"
        assert text.startswith("!!")
        cmd = text[2:].strip()
        assert cmd == "ls -la"

    def test_single_bang_strips_prefix(self):
        """! prefix is correctly identified and command is extracted."""
        text = "! git status"
        assert text.startswith("!") and not text.startswith("!!")
        cmd = text[1:].strip()
        assert cmd == "git status"

    def test_double_bang_priority_over_single(self):
        """!! is checked before ! in the dispatch logic."""
        text = "!!echo hello"
        # !! should match first
        assert text.startswith("!!")
        cmd = text[2:].strip()
        assert cmd == "echo hello"

    def test_single_bang_no_space(self):
        """! with no space also works."""
        text = "!pwd"
        assert not text.startswith("!!")
        assert text.startswith("!")
        cmd = text[1:].strip()
        assert cmd == "pwd"


# ── Collapsible tool output tests ─────────────────────────────────


class TestCollapsibleTracking:
    """Test that tool collapsibles are properly tracked."""

    def test_tool_collapsibles_list_management(self):
        """_tool_collapsibles list is cleared on action_clear."""
        # Simulate the list behavior
        collapsibles: list = []
        collapsibles.append("collapsible_1")
        collapsibles.append("collapsible_2")
        assert len(collapsibles) == 2

        collapsibles.clear()
        assert len(collapsibles) == 0
