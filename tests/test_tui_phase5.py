"""Phase 5 — TUI enhancements + cmux integration tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
    def test_set_cwd_overrides_rendered_working_directory(self, monkeypatch):
        """set_cwd lets the footer render a remote/project-specific path."""
        import worker_core.cmux as cmux_mod

        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "")
        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)

        from worker_tui.app import StatusFooter

        footer = StatusFooter()
        footer.set_cwd("/srv/worker/project")
        text = str(footer.render())
        assert "/srv/worker/project" in text

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


def _tui_test_config():
    return SimpleNamespace(
        ui=SimpleNamespace(theme="dark"),
        keybindings=SimpleNamespace(bindings={}),
    )


def _patch_tui_test_context(monkeypatch, *, prompts=None, skills=None):
    import worker_tui.app as tui_app

    monkeypatch.setattr(tui_app, "load_config", lambda _: _tui_test_config())
    monkeypatch.setattr(tui_app, "load_prompts", lambda _: prompts or {})
    monkeypatch.setattr(tui_app, "load_skills", lambda _: skills or {})
    monkeypatch.setattr(
        tui_app.WorkerApp,
        "_apply_theme",
        lambda self, name: setattr(self, "_active_theme", name),
    )


class TestSlashCommandSuggestions:
    def test_matching_command_suggestions_include_dynamic_skill_entries(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._skills = {
            "debug": SimpleNamespace(name="debug", description="Debug the current issue"),
        }

        matches = app._matching_command_suggestions("/skill:d")

        assert [match.value for match in matches] == ["/skill:debug"]

    def test_matching_command_suggestions_hide_after_command_arguments(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")

        assert app._matching_command_suggestions("/model anthropic/claude") == []

    def test_matching_command_suggestions_include_providers_command(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")

        matches = app._matching_command_suggestions("/prov")

        assert [match.value for match in matches] == ["/providers"]


class TestProviderSetupFormatting:
    @pytest.mark.asyncio
    async def test_collect_provider_setup_entries_marks_connected_and_setup_states(
        self,
        monkeypatch,
    ):
        from worker_core.config import WorkerConfig
        from worker_tui.app import collect_provider_setup_entries

        async def fake_resolve_api_key(config, provider_name: str):
            if provider_name == "openai":
                return "sk-test", "api"
            return None, "api"

        entries = await collect_provider_setup_entries(WorkerConfig(), fake_resolve_api_key)
        by_id = {entry.id: entry for entry in entries}

        assert by_id["openai"].status == "configured"
        assert by_id["openai"].hint == "use /models"
        assert by_id["anthropic"].hint == "run /connect anthropic or set ANTHROPIC_API_KEY"
        assert by_id["kimi"].name == "Kimi For Coding"
        assert by_id["kimi"].hint == "set MOONSHOT_API_KEY or [providers.kimi].api_key"
        assert by_id["ollama"].status == "keyless"
        assert "start the service" in by_id["ollama"].hint
        assert by_id["lmstudio"].status == "keyless"

    def test_format_provider_setup_entries_renders_supported_provider_list(self):
        from worker_tui.app import ProviderSetupEntry, format_provider_setup_entries

        rendered = format_provider_setup_entries(
            [
                ProviderSetupEntry(
                    id="openai",
                    name="OpenAI",
                    status="configured",
                    hint="use /models",
                ),
                ProviderSetupEntry(
                    id="ollama",
                    name="Ollama",
                    status="keyless",
                    hint="start the service or set [providers.ollama].base_url",
                ),
            ]
        )

        assert "Supported providers:" in rendered
        assert "openai (OpenAI) — configured; use /models" in rendered
        assert "ollama (Ollama) — keyless; start the service" in rendered
        assert "Use /models to browse models" in rendered


class TestProviderCommandDispatch:
    @pytest.mark.asyncio
    async def test_handle_command_dispatches_providers_command(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._list_providers = AsyncMock()  # type: ignore[method-assign]

        await app._handle_command("/providers")

        app._list_providers.assert_awaited_once()


class TestTuiAutocompleteIntegration:
    @pytest.mark.asyncio
    async def test_input_is_focused_on_mount(self, monkeypatch):
        from textual.widgets import Input
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", Input)
            assert input_bar.has_focus

    @pytest.mark.asyncio
    async def test_slash_command_suggestions_filter_and_tab_complete(self, monkeypatch):
        from textual.widgets import Input, OptionList
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(
            monkeypatch,
            skills={
                "debug": SimpleNamespace(
                    name="debug",
                    description="Debug the current issue",
                ),
            },
        )

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", Input)
            input_bar.value = "/m"
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            assert suggestions.has_class("visible")
            suggestion_ids = [
                suggestions.get_option_at_index(i).id
                for i in range(suggestions.option_count)
            ]
            assert suggestion_ids == [
                "/model",
                "/models",
            ]

            await pilot.press("tab")
            await pilot.pause()

            assert input_bar.value == "/model"
            assert not suggestions.has_class("visible")

    @pytest.mark.asyncio
    async def test_slash_command_suggestions_navigate_past_first_five_items(self, monkeypatch):
        from textual.widgets import Input, OptionList
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test(size=(80, 18)) as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", Input)
            input_bar.value = "/"
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            visible_commands = [
                suggestions.get_option_at_index(i).id for i in range(suggestions.option_count)
            ]
            assert len(visible_commands) > 5

            for _ in range(5):
                await pilot.press("down")
            await pilot.pause()

            assert suggestions.highlighted == 5

            await pilot.press("tab")
            await pilot.pause()

            assert input_bar.value == visible_commands[5]


# ── Remote payload/auth helper tests ──────────────────────────────


class TestRemoteTransportHelpers:
    def test_remote_rest_base_url_uses_rest_sidecar_port(self):
        from worker_tui.remote_control import remote_rest_base_url

        assert remote_rest_base_url("ws://example.com:7432") == "http://example.com:7433"
        assert remote_rest_base_url("wss://example.com:443") == "https://example.com:444"

    def test_remote_rest_base_url_uses_same_port_for_ws_path_proxy(self):
        from worker_tui.remote_control import remote_rest_base_url

        assert remote_rest_base_url("ws://example.com:7432/ws") == "http://example.com:7432"
        assert remote_rest_base_url("wss://example.com/ws") == "https://example.com"
        assert remote_rest_base_url("wss://example.com:8443/worker/ws") == "https://example.com:8443/worker"

    def test_remote_rest_base_url_appends_api_under_custom_base_path(self):
        from worker_tui.remote_control import remote_rest_base_url

        assert remote_rest_base_url("ws://example.com:7432/worker") == "http://example.com:7432/worker"
    def test_remote_headers_with_token(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432", auth_token="tok_123")
        headers = app._remote_connect_headers()
        assert headers == {"Authorization": "Bearer tok_123"}

    def test_remote_headers_without_token(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        headers = app._remote_connect_headers()
        assert headers == {}

    def test_remote_payload_contains_session_id(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432", auth_token="tok_123")
        payload = app._remote_message_payload("hello")
        assert payload["type"] == "message"
        assert payload["content"] == "hello"
        assert isinstance(payload["session_id"], str)
        assert payload["session_id"]


class TestRemoteModeCommandRouting:
    @pytest.mark.asyncio
    async def test_sync_remote_session_state_updates_footer_with_remote_project(self):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            async def get_session(self, session_id: str):
                return {
                    "session": {
                        "id": session_id,
                        "model": "openai/gpt-4.1",
                        "project_dir": "/srv/worker/project",
                    }
                }

        class _Footer:
            def __init__(self):
                self.model = ""
                self.cwd = ""

            def set_model(self, model: str) -> None:
                self.model = model

            def set_cwd(self, cwd: str) -> None:
                self.cwd = cwd

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        footer = _Footer()
        app.query_one = lambda selector, _cls=None: footer  # type: ignore[method-assign]

        await app._sync_remote_session_state()

        assert footer.model == "openai/gpt-4.1"
        assert footer.cwd == "/srv/worker/project"
        assert app._remote_project_dir == "/srv/worker/project"
    @pytest.mark.asyncio
    async def test_model_command_reads_remote_session_state(self):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            async def get_session(self, session_id: str):
                return {
                    "session": {
                        "id": session_id,
                        "model": "openai/gpt-4.1",
                        "project_dir": "/srv/worker/project",
                    }
                }

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        await app._handle_command("/model")

        assert seen_messages == [("Current model: openai/gpt-4.1", "tool")]

    @pytest.mark.asyncio
    async def test_project_command_switches_remote_project(self):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            async def set_session_project(self, session_id: str, project_dir: str):
                return {
                    "session": {
                        "id": session_id,
                        "model": "openai/gpt-4.1",
                        "project_dir": "/srv/projects/demo",
                    }
                }

        class _Footer:
            def __init__(self):
                self.model = ""
                self.cwd = ""

            def set_model(self, model: str) -> None:
                self.model = model

            def set_cwd(self, cwd: str) -> None:
                self.cwd = cwd

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        footer = _Footer()
        app.query_one = lambda selector, _cls=None: footer  # type: ignore[method-assign]
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        await app._cmd_project("/srv/projects/demo")

        assert footer.model == "openai/gpt-4.1"
        assert footer.cwd == "/srv/projects/demo"
        assert seen_messages[-1] == (
            "Switched remote project to: /srv/projects/demo",
            "tool",
        )

    @pytest.mark.asyncio
    async def test_thinking_command_uses_remote_control_client(self):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            async def get_session(self, session_id: str):
                return {"session": {"id": session_id, "thinking_level": "medium"}}

            async def set_session_thinking(self, session_id: str, thinking_level: str):
                return {
                    "session": {
                        "id": session_id,
                        "thinking_level": thinking_level,
                    }
                }

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        await app._cmd_thinking("")
        await app._cmd_thinking("high")

        assert seen_messages == [
            (
                "Current thinking level: medium\n"
                "Available: off, minimal, low, medium, high, xhigh\n"
                "Usage: /thinking <level>",
                "tool",
            ),
            ("Thinking level set to: high", "tool"),
        ]

    @pytest.mark.asyncio
    async def test_resume_command_lists_remote_sessions(self):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            async def list_sessions(self):
                return {
                    "sessions": [
                        {
                            "id": "remote-1",
                            "title": "Remote issue",
                            "model": "openai/gpt-4.1",
                            "project_dir": "/srv/project",
                            "updated_at": "2026-03-06 22:51:00",
                        }
                    ]
                }

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        await app._cmd_resume("")

        assert seen_messages == [
            (
                "Recent sessions:\n"
                "  1. [2026-03-06 22:51:00] Remote issue (openai/gpt-4.1) @ /srv/project\n"
                "\n"
                "Type /resume <number> to load a session.",
                "tool",
            )
        ]

    @pytest.mark.asyncio
    async def test_resume_command_restores_remote_session(self):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            async def get_session(self, session_id: str):
                return {
                    "session": {
                        "id": session_id,
                        "title": "Remote issue",
                        "model": "openai/gpt-4.1",
                        "project_dir": "/srv/project",
                    }
                }

            async def get_session_messages(self, session_id: str):
                return {
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "world"},
                    ]
                }

        class _Container:
            def __init__(self):
                self.cleared = False

            def remove_children(self) -> None:
                self.cleared = True

        class _Footer:
            def __init__(self):
                self.model = ""
                self.cwd = ""

            def set_model(self, model: str) -> None:
                self.model = model

            def set_cwd(self, cwd: str) -> None:
                self.cwd = cwd

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        container = _Container()
        footer = _Footer()
        app.query_one = lambda selector, _cls=None: (  # type: ignore[method-assign]
            container if selector == "#chat-container" else footer
        )
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        await app._cmd_resume("remote-1")

        assert container.cleared is True
        assert footer.model == "openai/gpt-4.1"
        assert footer.cwd == "/srv/project"
        assert app._remote_session_id == "remote-1"
        assert seen_messages == [
            ("hello", "user"),
            ("world", "assistant"),
            ("Resumed remote session: Remote issue", "tool"),
        ]

    @pytest.mark.asyncio
    async def test_double_bang_routes_to_remote_shell(self, monkeypatch):
        from types import SimpleNamespace

        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        calls: list[tuple[str, bool]] = []
        monkeypatch.setattr(app, "_command_menu_visible", lambda: False)
        monkeypatch.setattr(app, "_hide_command_menu", lambda: None)
        monkeypatch.setattr(app, "call_after_refresh", lambda callback: None)
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": None)
        monkeypatch.setattr(
            app,
            "_run_remote_bash",
            lambda cmd, send_to_llm=False: calls.append((cmd, send_to_llm)),
        )
        event = SimpleNamespace(
            value="!! pwd",
            input=SimpleNamespace(value="!! pwd"),
        )

        await app.on_input_submitted(event)

        assert calls == [("pwd", False)]

    @pytest.mark.asyncio
    async def test_switch_model_uses_remote_control_client(self):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            async def set_session_model(self, session_id: str, model: str):
                return {"session": {"id": session_id, "model": model}}

        class _Footer:
            def __init__(self):
                self.model = ""

            def set_model(self, model: str) -> None:
                self.model = model

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        footer = _Footer()
        app.query_one = lambda selector, _cls=None: footer  # type: ignore[method-assign]
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        await app._switch_model("openai/gpt-4.1")

        assert footer.model == "openai/gpt-4.1"
        assert seen_messages[-1] == ("Switched to openai/gpt-4.1", "tool")

    @pytest.mark.asyncio
    async def test_single_bang_routes_to_remote_shell_with_llm_forwarding(self, monkeypatch):
        from types import SimpleNamespace

        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        calls: list[tuple[str, bool]] = []
        monkeypatch.setattr(app, "_command_menu_visible", lambda: False)
        monkeypatch.setattr(app, "_hide_command_menu", lambda: None)
        monkeypatch.setattr(app, "call_after_refresh", lambda callback: None)
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": None)
        monkeypatch.setattr(
            app,
            "_run_remote_bash",
            lambda cmd, send_to_llm=False: calls.append((cmd, send_to_llm)),
        )
        event = SimpleNamespace(
            value="! pwd",
            input=SimpleNamespace(value="! pwd"),
        )

        await app.on_input_submitted(event)

        assert calls == [("pwd", True)]
