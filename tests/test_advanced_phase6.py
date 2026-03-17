"""Phase 6 — Advanced features tests.

Covers:
  6.1 RPC (JSON-RPC helpers + server dispatch)
  6.2 SDK (public API imports)
  6.3 Piped stdin (CLI integration)
  6.4 Export (HTML rendering)
  6.6 Migrations (version tracking, registry, runner)
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from artel_ai.models import Message, Role, ToolCall, ToolResult

# ── 6.1  RPC helpers ──────────────────────────────────────────────


class TestRpcProtocolHelpers:
    """Test JSON-RPC 2.0 formatting helpers."""

    def test_jsonrpc_response(self):
        from artel_server.rpc import _jsonrpc_response

        raw = _jsonrpc_response(1, {"status": "ok"})
        obj = json.loads(raw)
        assert obj["jsonrpc"] == "2.0"
        assert obj["id"] == 1
        assert obj["result"] == {"status": "ok"}

    def test_jsonrpc_response_null_id(self):
        from artel_server.rpc import _jsonrpc_response

        obj = json.loads(_jsonrpc_response(None, "pong"))
        assert obj["id"] is None

    def test_jsonrpc_error(self):
        from artel_server.rpc import _jsonrpc_error

        raw = _jsonrpc_error(42, -32601, "Method not found")
        obj = json.loads(raw)
        assert obj["jsonrpc"] == "2.0"
        assert obj["id"] == 42
        assert obj["error"]["code"] == -32601
        assert obj["error"]["message"] == "Method not found"

    def test_jsonrpc_notification(self):
        from artel_server.rpc import _jsonrpc_notification

        raw = _jsonrpc_notification("event", {"type": "text_delta", "content": "hi"})
        obj = json.loads(raw)
        assert obj["jsonrpc"] == "2.0"
        assert "id" not in obj
        assert obj["method"] == "event"
        assert obj["params"]["type"] == "text_delta"


class TestRpcServerDispatch:
    """Test RpcServer.handle_request dispatch (no real session)."""

    @pytest.mark.asyncio
    async def test_ping(self):
        from artel_server.rpc import RpcServer

        server = RpcServer()
        written: list[str] = []
        server._write = lambda line: written.append(line)

        await server.handle_request({"id": 1, "method": "ping"})

        assert len(written) == 1
        resp = json.loads(written[0])
        assert resp["result"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_unknown_method(self):
        from artel_server.rpc import RpcServer

        server = RpcServer()
        written: list[str] = []
        server._write = lambda line: written.append(line)

        await server.handle_request({"id": 2, "method": "bogus"})

        resp = json.loads(written[0])
        assert resp["error"]["code"] == -32601
        assert "bogus" in resp["error"]["message"]

    @pytest.mark.asyncio
    async def test_shutdown(self):
        from artel_server.rpc import RpcServer

        server = RpcServer()
        written: list[str] = []
        server._write = lambda line: written.append(line)
        assert server._running is True

        await server.handle_request({"id": 3, "method": "shutdown"})

        resp = json.loads(written[0])
        assert resp["result"]["shutdown"] is True
        assert server._running is False

    @pytest.mark.asyncio
    async def test_cancel_without_session(self):
        from artel_server.rpc import RpcServer

        server = RpcServer()
        written: list[str] = []
        server._write = lambda line: written.append(line)

        await server.handle_request({"id": 4, "method": "cancel"})

        resp = json.loads(written[0])
        assert resp["result"]["cancelled"] is True

    @pytest.mark.asyncio
    async def test_compact_without_session(self):
        from artel_server.rpc import RpcServer

        server = RpcServer()
        written: list[str] = []
        server._write = lambda line: written.append(line)

        await server.handle_request({"id": 5, "method": "compact"})

        resp = json.loads(written[0])
        assert resp["error"]["code"] == -32000

    @pytest.mark.asyncio
    async def test_message_without_content(self):
        from artel_server.rpc import RpcServer

        server = RpcServer()
        written: list[str] = []
        server._write = lambda line: written.append(line)

        await server.handle_request({"id": 6, "method": "message", "params": {}})

        resp = json.loads(written[0])
        assert resp["error"]["code"] == -32602


# ── 6.2  SDK public API ──────────────────────────────────────────


class TestSdkPublicApi:
    """Test that artel_core exposes a clean public API."""

    def test_all_exports_importable(self):
        import artel_core

        assert hasattr(artel_core, "AgentSession")
        assert hasattr(artel_core, "AgentEvent")
        assert hasattr(artel_core, "AgentEventType")
        assert hasattr(artel_core, "ArtelConfig")
        assert hasattr(artel_core, "load_config")
        assert hasattr(artel_core, "Tool")
        assert hasattr(artel_core, "Extension")
        assert hasattr(artel_core, "HookDispatcher")
        assert hasattr(artel_core, "SessionStore")
        assert hasattr(artel_core, "export_html")

    def test_all_list_matches(self):
        import artel_core

        expected_subset = {
            "AgentEvent",
            "AgentEventType",
            "AgentSession",
            "Extension",
            "HookDispatcher",
            "SessionStore",
            "Tool",
            "ArtelConfig",
            "export_html",
            "load_config",
        }
        assert expected_subset.issubset(set(artel_core.__all__))

    def test_direct_imports(self):
        """Verify direct imports work."""
        from artel_core import export_html, load_config

        assert callable(export_html)
        assert callable(load_config)


# ── 6.3  Piped stdin ─────────────────────────────────────────────


class TestPipedStdin:
    """Test that CLI detects piped stdin and prepends it to the prompt."""

    def test_stdin_prepended_to_prompt(self, monkeypatch, tmp_path):
        """When stdin is a pipe, its content is prepended to -p prompt."""

        from artel_core import cli as cli_mod

        monkeypatch.setattr("sys.stdin", StringIO("piped content\nline 2"))
        monkeypatch.setattr("sys.stdin.isatty", lambda: False)

        captured: list[str] = []

        async def mock_print_mode(prompt, **kwargs):
            captured.append(prompt)

        monkeypatch.setattr(cli_mod, "_print_mode", mock_print_mode)
        monkeypatch.setattr(cli_mod.asyncio, "run", lambda coro: None)

        # Simulate: echo "piped content" | artel -p "explain"
        # We call the CLI function directly
        from click.testing import CliRunner

        runner = CliRunner()

        # Monkey-patch _print_mode at module level so CLI calls our version
        async def capture_print_mode(prompt, **kwargs):
            captured.append(prompt)

        monkeypatch.setattr(cli_mod, "_print_mode", capture_print_mode)

        # We need to intercept asyncio.run to actually run our coroutine
        def run_coro(coro):
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        monkeypatch.setattr(cli_mod.asyncio, "run", run_coro)

        runner.invoke(cli_mod.cli, ["-p", "explain this"], input="piped content\nline 2")

        assert len(captured) == 1
        assert "piped content" in captured[0]
        assert "explain this" in captured[0]

    def test_no_stdin_when_tty(self, monkeypatch):
        """When stdin is a TTY, prompt is used as-is."""

        from artel_core import cli as cli_mod

        captured: list[str] = []

        async def capture_print_mode(prompt, **kwargs):
            captured.append(prompt)

        monkeypatch.setattr(cli_mod, "_print_mode", capture_print_mode)

        def run_coro(coro):
            import asyncio

            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        monkeypatch.setattr(cli_mod.asyncio, "run", run_coro)

        from click.testing import CliRunner

        runner = CliRunner()
        runner.invoke(cli_mod.cli, ["-p", "just a prompt"])

        assert len(captured) == 1
        # The prompt should be exactly what was passed (no stdin prepended)
        assert captured[0] == "just a prompt"


# ── 6.4  Export HTML ──────────────────────────────────────────────


class TestExportHtml:
    """Test HTML export rendering."""

    def test_default_export_title_uses_artel_session(self):
        from artel_core.export import export_html

        html = export_html([Message(role=Role.USER, content="Hello")])

        assert "<title>Artel Session</title>" in html
        assert "<h1>Artel Session</h1>" in html

    def test_basic_export(self):
        from artel_core.export import export_html

        messages = [
            Message(role=Role.USER, content="Hello"),
            Message(role=Role.ASSISTANT, content="Hi there!"),
        ]
        html = export_html(messages, title="Test Session")

        assert "<!DOCTYPE html>" in html
        assert "<title>Test Session</title>" in html
        assert "Hello" in html
        assert "Hi there!" in html
        assert "2 messages" in html

    def test_export_escapes_html(self):
        from artel_core.export import export_html

        messages = [
            Message(role=Role.USER, content="<script>alert('xss')</script>"),
        ]
        html = export_html(messages)

        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_export_code_blocks(self):
        from artel_core.export import export_html

        messages = [
            Message(role=Role.ASSISTANT, content="```python\nprint('hello')\n```"),
        ]
        html = export_html(messages)

        assert "<pre><code>" in html
        assert "print(&#x27;hello&#x27;)" in html or "print(" in html

    def test_export_with_model_and_session(self):
        from artel_core.export import export_html

        messages = [
            Message(role=Role.USER, content="test"),
        ]
        html = export_html(messages, model="claude-sonnet-4", session_id="abc12345-rest")

        assert "claude-sonnet-4" in html
        assert "abc12345" in html

    def test_export_tool_calls(self):
        from artel_core.export import export_html

        messages = [
            Message(
                role=Role.ASSISTANT,
                content="Let me check.",
                tool_calls=[ToolCall(id="tc1", name="read_file", arguments={"path": "/tmp/x"})],
            ),
        ]
        html = export_html(messages)

        assert "read_file" in html

    def test_export_tool_results(self):
        from artel_core.export import export_html

        messages = [
            Message(
                role=Role.TOOL,
                content="",
                tool_result=ToolResult(tool_call_id="tc1", content="file contents here"),
            ),
        ]
        html = export_html(messages)

        assert "file contents here" in html

    def test_export_empty_messages(self):
        from artel_core.export import export_html

        html = export_html([], title="Empty")

        assert "<!DOCTYPE html>" in html
        assert "0 messages" in html

    def test_export_role_classes(self):
        from artel_core.export import _role_class

        assert _role_class(Role.USER) == "user"
        assert _role_class(Role.ASSISTANT) == "assistant"
        assert _role_class(Role.TOOL) == "tool"
        assert _role_class(Role.SYSTEM) == "system"

    def test_export_catalan_theme_styling(self):
        """Verify the Catppuccin Mocha CSS is embedded."""
        from artel_core.export import export_html

        html = export_html([Message(role=Role.USER, content="x")])

        assert "#1e1e2e" in html  # Catppuccin Mocha base
        assert "#89b4fa" in html  # Catppuccin Mocha blue


# ── 6.6  Migrations ──────────────────────────────────────────────


class TestMigrations:
    """Test the migration system."""

    def test_read_write_state(self, tmp_path, monkeypatch):
        from artel_core import migrations as mig_mod

        state_file = tmp_path / "state.json"
        monkeypatch.setattr(mig_mod, "_STATE_FILE", state_file)

        # Initially empty
        assert mig_mod._read_state() == {}

        # Write and read back
        mig_mod._write_state({"config_version": 5, "extra": "data"})
        state = mig_mod._read_state()
        assert state["config_version"] == 5
        assert state["extra"] == "data"

    def test_get_set_version(self, tmp_path, monkeypatch):
        from artel_core import migrations as mig_mod

        state_file = tmp_path / "state.json"
        monkeypatch.setattr(mig_mod, "_STATE_FILE", state_file)

        assert mig_mod.get_current_version() == 0
        mig_mod.set_version(3)
        assert mig_mod.get_current_version() == 3

    def test_migration_decorator(self):
        from artel_core.migrations import _MIGRATIONS

        # There should be at least the v1 built-in migration
        versions = [m.version for m in _MIGRATIONS]
        assert 1 in versions

    def test_pending_migrations(self, tmp_path, monkeypatch):
        from artel_core import migrations as mig_mod

        state_file = tmp_path / "state.json"
        monkeypatch.setattr(mig_mod, "_STATE_FILE", state_file)

        # Version 0 → all migrations pending
        pending = mig_mod.pending_migrations()
        assert len(pending) >= 1
        assert all(m.version > 0 for m in pending)

    def test_run_migrations(self, tmp_path, monkeypatch):
        from artel_core import migrations as mig_mod

        state_file = tmp_path / "state.json"
        monkeypatch.setattr(mig_mod, "_STATE_FILE", state_file)

        applied = mig_mod.run_migrations(tmp_path)
        assert len(applied) >= 1
        assert "v1" in applied[0]

        # After running, version should be updated
        assert mig_mod.get_current_version() >= 1

    def test_run_migrations_idempotent(self, tmp_path, monkeypatch):
        from artel_core import migrations as mig_mod

        state_file = tmp_path / "state.json"
        monkeypatch.setattr(mig_mod, "_STATE_FILE", state_file)

        first = mig_mod.run_migrations(tmp_path)
        second = mig_mod.run_migrations(tmp_path)

        assert len(first) >= 1
        assert len(second) == 0  # Nothing new to apply

    def test_check_and_migrate(self, tmp_path, monkeypatch):
        from artel_core import migrations as mig_mod

        state_file = tmp_path / "state.json"
        monkeypatch.setattr(mig_mod, "_STATE_FILE", state_file)

        mig_mod.check_and_migrate()

        assert mig_mod.get_current_version() >= mig_mod.CURRENT_VERSION

    def test_check_and_migrate_noop_when_current(self, tmp_path, monkeypatch):
        from artel_core import migrations as mig_mod

        state_file = tmp_path / "state.json"
        monkeypatch.setattr(mig_mod, "_STATE_FILE", state_file)

        mig_mod.set_version(mig_mod.CURRENT_VERSION)
        # Should return immediately, not run any migrations
        mig_mod.check_and_migrate()
        assert mig_mod.get_current_version() == mig_mod.CURRENT_VERSION

    def test_migration_failure_stops_chain(self, tmp_path, monkeypatch):
        from artel_core import migrations as mig_mod

        state_file = tmp_path / "state.json"
        monkeypatch.setattr(mig_mod, "_STATE_FILE", state_file)

        # Save original migrations
        original = mig_mod._MIGRATIONS.copy()
        call_log: list[int] = []

        try:
            mig_mod._MIGRATIONS.clear()

            @mig_mod.migration(100, "good migration")
            def _m100(config_dir: Path) -> None:
                call_log.append(100)

            @mig_mod.migration(101, "bad migration")
            def _m101(config_dir: Path) -> None:
                raise RuntimeError("boom")

            @mig_mod.migration(102, "never reached")
            def _m102(config_dir: Path) -> None:
                call_log.append(102)

            # Set version just below our test migrations
            mig_mod.set_version(99)

            applied = mig_mod.run_migrations(tmp_path)

            # m100 ran, m101 failed, m102 was skipped
            assert 100 in call_log
            assert 102 not in call_log
            assert len(applied) == 1
            assert mig_mod.get_current_version() == 100
        finally:
            mig_mod._MIGRATIONS.clear()
            mig_mod._MIGRATIONS.extend(original)

    def test_corrupt_state_file(self, tmp_path, monkeypatch):
        from artel_core import migrations as mig_mod

        state_file = tmp_path / "state.json"
        monkeypatch.setattr(mig_mod, "_STATE_FILE", state_file)

        # Write corrupt JSON
        state_file.write_text("not json at all", encoding="utf-8")
        assert mig_mod._read_state() == {}

    def test_state_dir_created(self, tmp_path, monkeypatch):
        from artel_core import migrations as mig_mod

        nested = tmp_path / "deep" / "nested" / "state.json"
        monkeypatch.setattr(mig_mod, "_STATE_FILE", nested)

        mig_mod._write_state({"config_version": 1})
        assert nested.exists()


# ── Integration: RPC + export ─────────────────────────────────────


class TestRpcExportIntegration:
    """Integration-level tests combining RPC + export features."""

    def test_export_roundtrip(self, tmp_path):
        """Generate HTML, write to file, verify file is valid HTML."""
        from artel_core.export import export_html

        messages = [
            Message(role=Role.USER, content="What is 2+2?"),
            Message(role=Role.ASSISTANT, content="The answer is 4."),
        ]
        html = export_html(messages, title="Math Session", model="test-model")

        out = tmp_path / "export.html"
        out.write_text(html, encoding="utf-8")

        content = out.read_text(encoding="utf-8")
        assert content.startswith("<!DOCTYPE html>")
        assert "Math Session" in content
        assert "test-model" in content
        assert "What is 2+2?" in content
        assert "The answer is 4." in content
