"""Phase 5 — TUI enhancements + cmux integration tests."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# ── cmux module tests ─────────────────────────────────────────────


class TestCmuxDetection:
    """Tests for cmux environment detection."""

    def test_can_manage_cmux_true_with_binary_outside_cmux(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/usr/local/bin/cmux")
        assert cmux_mod.can_manage_cmux() is True

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


class TestCmuxPreflight:
    """Tests for cmux interactive preflight."""

    def test_preflight_fails_when_binary_missing(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "")
        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)

        result = cmux_mod.preflight_cmux()

        assert result.ok is False
        assert result.code == "binary_missing"
        assert "requires cmux" in result.summary.lower()
        assert "Install cmux" in result.format_message()

    def test_preflight_fails_outside_cmux_workspace(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")
        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)

        result = cmux_mod.preflight_cmux()

        assert result.ok is False
        assert result.code == "unsupported_environment"
        assert "launched inside a cmux workspace" in result.summary

    def test_preflight_fails_when_socket_missing(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-test")
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")
        monkeypatch.setattr(cmux_mod.os.path, "exists", lambda path: False)

        result = cmux_mod.preflight_cmux()

        assert result.ok is False
        assert result.code == "socket_unavailable"
        assert result.workspace == "ws-test"

    def test_preflight_fails_when_capabilities_missing(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-test")
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")
        monkeypatch.setattr(cmux_mod.os.path, "exists", lambda path: True)
        monkeypatch.setattr(cmux_mod, "is_cmux_socket_reachable", lambda path: True)
        monkeypatch.setattr(cmux_mod, "probe_cmux_capabilities", lambda help_text=None: {"browser"})

        result = cmux_mod.preflight_cmux()

        assert result.ok is False
        assert result.code == "capabilities_missing"
        assert "workspace" in result.missing_capabilities
        assert "surface" in result.missing_capabilities

    def test_preflight_fails_when_socket_is_unreachable(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-test")
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")
        monkeypatch.setattr(cmux_mod.os.path, "exists", lambda path: True)
        monkeypatch.setattr(cmux_mod, "is_cmux_socket_reachable", lambda path: False)

        result = cmux_mod.preflight_cmux()

        assert result.ok is False
        assert result.code == "socket_unreachable"
        assert result.workspace == "ws-test"
        assert result.socket_path == cmux_mod.DEFAULT_CMUX_SOCKET_PATH

    def test_preflight_passes_when_runtime_is_ready(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-test")
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")
        monkeypatch.setattr(cmux_mod.os.path, "exists", lambda path: True)
        monkeypatch.setattr(cmux_mod, "is_cmux_socket_reachable", lambda path: True)
        monkeypatch.setattr(
            cmux_mod,
            "probe_cmux_capabilities",
            lambda help_text=None: set(cmux_mod.EXPECTED_CMUX_CAPABILITIES),
        )

        result = cmux_mod.preflight_cmux()

        assert result.ok is True
        assert result.workspace == "ws-test"
        assert result.binary_path == "/mock/cmux"

    def test_management_preflight_passes_without_cmux_workspace_env(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")
        monkeypatch.setattr(cmux_mod.os.path, "exists", lambda path: True)
        monkeypatch.setattr(cmux_mod, "is_cmux_socket_reachable", lambda path: True)
        monkeypatch.setattr(
            cmux_mod,
            "probe_cmux_capabilities",
            lambda help_text=None: {"workspace", "surface", "browser"},
        )

        result = cmux_mod.preflight_cmux_management()

        assert result.ok is True
        assert result.binary_path == "/mock/cmux"
        assert result.workspace == ""

    def test_management_preflight_reports_socket_failure_without_cmux_workspace_env(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")
        monkeypatch.setattr(cmux_mod.os.path, "exists", lambda path: False)

        result = cmux_mod.preflight_cmux_management()

        assert result.ok is False
        assert result.code == "socket_unavailable"
        assert "CMUX_WORKSPACE_ID is not required" in result.format_message()


class TestCmuxParsing:
    """Tests for typed cmux workspace/surface parsing helpers."""

    def test_parse_workspace_list_handles_kv_and_marked_current(self):
        import worker_core.cmux as cmux_mod

        records = cmux_mod.parse_workspace_list(
            "* id=ws-1 name=artel-main\nid=ws-2 name=scratch"
        )

        assert len(records) == 2
        assert records[0].id == "ws-1"
        assert records[0].name == "artel-main"
        assert records[0].current is True
        assert records[1].id == "ws-2"
        assert records[1].name == "scratch"
        assert records[1].current is False

    def test_parse_surface_list_handles_kv_and_workspace(self):
        import worker_core.cmux as cmux_mod

        records = cmux_mod.parse_surface_list(
            "* id=sf-1 title=dashboard workspace=ws-1\nid=sf-2 title=orchestrator workspace=ws-1"
        )

        assert len(records) == 2
        assert records[0].id == "sf-1"
        assert records[0].title == "dashboard"
        assert records[0].workspace == "ws-1"
        assert records[0].current is True
        assert records[1].id == "sf-2"
        assert records[1].title == "orchestrator"
        assert records[1].workspace == "ws-1"
        assert records[1].current is False


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

        await cmux_mod.notify("Artel", subtitle="Done", body="Task finished")

        assert captured_args == [
            "notify", "--title", "Artel",
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

        await cmux_mod.log("test error", level="error", source="artel")

        assert captured_args == [
            "log", "--level", "error", "--source", "artel", "--", "test error",
        ]

    @pytest.mark.asyncio
    async def test_workspace_create_builds_expected_args(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        calls = []

        async def fake_run(args, **kwargs):
            calls.append(list(args))
            if args[:1] == ["new-workspace"]:
                return "OK workspace:123"
            return "OK workspace:123"

        monkeypatch.setattr(cmux_mod, "_run", fake_run)

        result = await cmux_mod.workspace_create("artel-main")

        assert result == "workspace:123"
        assert calls == [
            ["new-workspace"],
            ["rename-workspace", "--workspace", "workspace:123", "artel-main"],
        ]

    @pytest.mark.asyncio
    async def test_surface_create_builds_expected_args(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        calls = []

        async def fake_run(args, **kwargs):
            calls.append(list(args))
            if args[:1] == ["new-surface"]:
                return "OK surface:123 pane:1 workspace:artel-main"
            return "OK"

        monkeypatch.setattr(cmux_mod, "_run", fake_run)

        result = await cmux_mod.surface_create(
            title="dashboard",
            command="artel",
            cwd="/srv/project",
            workspace="artel-main",
        )

        assert result == "surface:123"
        assert calls == [
            ["new-surface", "--workspace", "artel-main"],
            ["rename-tab", "--surface", "surface:123", "--workspace", "artel-main", "--title", "dashboard"],
            ["send", "--surface", "surface:123", "--workspace", "artel-main", "cd '/srv/project' && artel\n"],
        ]

    @pytest.mark.asyncio
    async def test_surface_focus_builds_expected_args(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setenv("CMUX_WORKSPACE_ID", "ws-test")
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        calls = []

        async def fake_run(args, **kwargs):
            calls.append(list(args))
            if args[:1] == ["identify"]:
                return '{"caller":{"pane_ref":"pane:9"}}'
            return "OK"

        monkeypatch.setattr(cmux_mod, "_run", fake_run)

        await cmux_mod.surface_focus("surface-123")

        assert calls == [
            ["identify", "--surface", "surface-123"],
            ["focus-pane", "--pane", "pane:9"],
        ]

    @pytest.mark.asyncio
    async def test_workspace_list_records_parses_cli_output(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        async def fake_run(args, **kwargs):
            assert args == ["list-workspaces"]
            return "* workspace:1  artel-main\nworkspace:2  scratch"

        monkeypatch.setattr(cmux_mod, "_run", fake_run)

        records = await cmux_mod.workspace_list_records()

        assert [record.id for record in records] == ["workspace:1", "workspace:2"]
        assert records[0].current is True
        assert records[0].name == "artel-main"

    @pytest.mark.asyncio
    async def test_surface_list_records_parses_cli_output(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        async def fake_run(args, **kwargs):
            assert args == ["list-pane-surfaces", "--workspace", "artel-main"]
            return "* surface:1  dashboard  [selected]"

        monkeypatch.setattr(cmux_mod, "_run", fake_run)

        records = await cmux_mod.surface_list_records(workspace="artel-main")

        assert len(records) == 1
        assert records[0].id == "surface:1"
        assert records[0].title == "dashboard"
        assert records[0].workspace == ""

    @pytest.mark.asyncio
    async def test_ensure_workspace_returns_existing_record(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        async def fake_list_records():
            return [cmux_mod.CmuxWorkspaceRecord(id="ws-1", name="artel-main", current=True)]

        async def fake_workspace_create(name: str = ""):
            raise AssertionError("workspace_create should not be called when workspace exists")

        monkeypatch.setattr(cmux_mod, "workspace_list_records", fake_list_records)
        monkeypatch.setattr(cmux_mod, "workspace_create", fake_workspace_create)

        record = await cmux_mod.ensure_workspace("artel-main")

        assert record is not None
        assert record.id == "ws-1"
        assert record.name == "artel-main"

    @pytest.mark.asyncio
    async def test_ensure_workspace_creates_when_missing(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        async def fake_list_records():
            return []

        async def fake_workspace_create(name: str = ""):
            assert name == "artel-main"
            return "ws-123"

        monkeypatch.setattr(cmux_mod, "workspace_list_records", fake_list_records)
        monkeypatch.setattr(cmux_mod, "workspace_create", fake_workspace_create)

        record = await cmux_mod.ensure_workspace("artel-main")

        assert record is not None
        assert record.id == "ws-123"
        assert record.name == "artel-main"

    @pytest.mark.asyncio
    async def test_ensure_surface_returns_existing_record(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        async def fake_surface_list_records(*, workspace: str = ""):
            assert workspace == "artel-main"
            return [cmux_mod.CmuxSurfaceRecord(id="sf-1", title="dashboard", workspace=workspace)]

        async def fake_surface_create(**kwargs):
            raise AssertionError("surface_create should not be called when surface exists")

        monkeypatch.setattr(cmux_mod, "surface_list_records", fake_surface_list_records)
        monkeypatch.setattr(cmux_mod, "surface_create", fake_surface_create)

        record = await cmux_mod.ensure_surface(title="dashboard", workspace="artel-main")

        assert record is not None
        assert record.id == "sf-1"
        assert record.title == "dashboard"

    @pytest.mark.asyncio
    async def test_ensure_surface_creates_when_missing(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)
        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "/mock/cmux")

        async def fake_surface_list_records(*, workspace: str = ""):
            assert workspace == "artel-main"
            return []

        async def fake_surface_create(**kwargs):
            assert kwargs == {
                "title": "dashboard",
                "command": "artel dashboard",
                "cwd": "/srv/project",
                "workspace": "artel-main",
            }
            return "sf-123"

        monkeypatch.setattr(cmux_mod, "surface_list_records", fake_surface_list_records)
        monkeypatch.setattr(cmux_mod, "surface_create", fake_surface_create)

        record = await cmux_mod.ensure_surface(
            title="dashboard",
            command="artel dashboard",
            cwd="/srv/project",
            workspace="artel-main",
        )

        assert record is not None
        assert record.id == "sf-123"
        assert record.title == "dashboard"
        assert record.workspace == "artel-main"

    @pytest.mark.asyncio
    async def test_ensure_artel_workspace_uses_default_name(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        async def fake_ensure_workspace(name: str):
            assert name == cmux_mod.DEFAULT_ARTEL_WORKSPACE_NAME
            return cmux_mod.CmuxWorkspaceRecord(id="ws-123", name=name)

        monkeypatch.setattr(cmux_mod, "ensure_workspace", fake_ensure_workspace)

        record = await cmux_mod.ensure_artel_workspace()

        assert record is not None
        assert record.id == "ws-123"
        assert record.name == cmux_mod.DEFAULT_ARTEL_WORKSPACE_NAME

    @pytest.mark.asyncio
    async def test_ensure_artel_dashboard_surface_uses_expected_defaults(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        async def fake_ensure_surface(**kwargs):
            assert kwargs == {
                "title": cmux_mod.DEFAULT_ARTEL_DASHBOARD_SURFACE_TITLE,
                "command": "artel web",
                "cwd": "/srv/project",
                "workspace": "ws-123",
            }
            return cmux_mod.CmuxSurfaceRecord(id="sf-dashboard", title=kwargs["title"], workspace="ws-123")

        monkeypatch.setattr(cmux_mod, "ensure_surface", fake_ensure_surface)

        record = await cmux_mod.ensure_artel_dashboard_surface(workspace="ws-123", cwd="/srv/project")

        assert record is not None
        assert record.id == "sf-dashboard"
        assert record.title == cmux_mod.DEFAULT_ARTEL_DASHBOARD_SURFACE_TITLE

    @pytest.mark.asyncio
    async def test_ensure_artel_orchestrator_surface_uses_expected_defaults(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        async def fake_ensure_surface(**kwargs):
            assert kwargs == {
                "title": cmux_mod.DEFAULT_ARTEL_ORCHESTRATOR_SURFACE_TITLE,
                "command": "artel",
                "cwd": "/srv/project",
                "workspace": "ws-123",
            }
            return cmux_mod.CmuxSurfaceRecord(id="sf-orchestrator", title=kwargs["title"], workspace="ws-123")

        monkeypatch.setattr(cmux_mod, "ensure_surface", fake_ensure_surface)

        record = await cmux_mod.ensure_artel_orchestrator_surface(workspace="ws-123", cwd="/srv/project")

        assert record is not None
        assert record.id == "sf-orchestrator"
        assert record.title == cmux_mod.DEFAULT_ARTEL_ORCHESTRATOR_SURFACE_TITLE

    @pytest.mark.asyncio
    async def test_bootstrap_artel_workspace_ensures_workspace_and_core_surfaces(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        calls: list[tuple[str, str]] = []

        async def fake_ensure_artel_workspace(*, workspace_name: str = cmux_mod.DEFAULT_ARTEL_WORKSPACE_NAME):
            calls.append(("workspace", workspace_name))
            return cmux_mod.CmuxWorkspaceRecord(id="ws-123", name=workspace_name)

        async def fake_ensure_artel_dashboard_surface(**kwargs):
            calls.append(("dashboard", kwargs["workspace"]))
            assert kwargs["title"] == "dashboard"
            assert kwargs["command"] == "artel web"
            assert kwargs["cwd"] == "/srv/project"
            return cmux_mod.CmuxSurfaceRecord(id="sf-dashboard", title="dashboard", workspace=kwargs["workspace"])

        async def fake_reuse_current_surface(**kwargs):
            calls.append(("reuse", kwargs["workspace"]))
            assert kwargs == {
                "title": "orchestrator",
                "workspace": "ws-123",
            }
            return cmux_mod.CmuxSurfaceRecord(id="sf-current", title="orchestrator", workspace="ws-123", current=True)

        async def fake_ensure_artel_orchestrator_surface(**kwargs):
            raise AssertionError("orchestrator surface should not be created when current surface is reused")

        monkeypatch.setattr(cmux_mod, "ensure_artel_workspace", fake_ensure_artel_workspace)
        monkeypatch.setattr(cmux_mod, "ensure_artel_dashboard_surface", fake_ensure_artel_dashboard_surface)
        monkeypatch.setattr(cmux_mod, "reuse_current_surface", fake_reuse_current_surface)
        monkeypatch.setattr(cmux_mod, "ensure_artel_orchestrator_surface", fake_ensure_artel_orchestrator_surface)
        monkeypatch.setattr(cmux_mod, "workspace_id", lambda: "ws-123")

        result = await cmux_mod.bootstrap_artel_workspace(cwd="/srv/project")

        assert result.workspace is not None
        assert result.workspace.id == "ws-123"
        assert result.dashboard is not None
        assert result.dashboard.id == "sf-dashboard"
        assert result.orchestrator is not None
        assert result.orchestrator.id == "sf-current"
        assert calls == [
            ("workspace", cmux_mod.DEFAULT_ARTEL_WORKSPACE_NAME),
            ("dashboard", "ws-123"),
            ("reuse", "ws-123"),
        ]

    @pytest.mark.asyncio
    async def test_bootstrap_artel_workspace_falls_back_to_creating_orchestrator_surface(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        calls: list[tuple[str, str]] = []

        async def fake_ensure_artel_workspace(*, workspace_name: str = cmux_mod.DEFAULT_ARTEL_WORKSPACE_NAME):
            return cmux_mod.CmuxWorkspaceRecord(id="ws-123", name=workspace_name)

        async def fake_ensure_artel_dashboard_surface(**kwargs):
            calls.append(("dashboard", kwargs["workspace"]))
            return cmux_mod.CmuxSurfaceRecord(id="sf-dashboard", title="dashboard", workspace=kwargs["workspace"])

        async def fake_reuse_current_surface(**kwargs):
            calls.append(("reuse", kwargs["workspace"]))
            return None

        async def fake_ensure_artel_orchestrator_surface(**kwargs):
            calls.append(("orchestrator", kwargs["workspace"]))
            assert kwargs["title"] == "orchestrator"
            assert kwargs["command"] == "artel"
            assert kwargs["cwd"] == "/srv/project"
            return cmux_mod.CmuxSurfaceRecord(id="sf-orchestrator", title="orchestrator", workspace=kwargs["workspace"])

        monkeypatch.setattr(cmux_mod, "ensure_artel_workspace", fake_ensure_artel_workspace)
        monkeypatch.setattr(cmux_mod, "ensure_artel_dashboard_surface", fake_ensure_artel_dashboard_surface)
        monkeypatch.setattr(cmux_mod, "reuse_current_surface", fake_reuse_current_surface)
        monkeypatch.setattr(cmux_mod, "ensure_artel_orchestrator_surface", fake_ensure_artel_orchestrator_surface)
        monkeypatch.setattr(cmux_mod, "workspace_id", lambda: "ws-123")

        result = await cmux_mod.bootstrap_artel_workspace(cwd="/srv/project")

        assert result.dashboard is not None
        assert result.dashboard.id == "sf-dashboard"
        assert result.orchestrator is not None
        assert result.orchestrator.id == "sf-orchestrator"
        assert calls == [
            ("dashboard", "ws-123"),
            ("reuse", "ws-123"),
            ("orchestrator", "ws-123"),
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

    def test_set_thinking_level_shows_next_to_model(self, monkeypatch):
        import worker_core.cmux as cmux_mod

        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "")
        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)

        from worker_tui.app import StatusFooter

        footer = StatusFooter()
        footer.set_model("anthropic/claude-sonnet")
        footer.set_thinking_level("high")
        text = str(footer.render())
        assert "anthropic/claude-sonnet [high]" in text
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

    def test_set_activity_marks_footer_busy(self, monkeypatch):
        """Footer renders an explicit busy/idle activity indicator."""
        import worker_core.cmux as cmux_mod

        monkeypatch.setattr(cmux_mod, "_CMUX_BIN", "")
        monkeypatch.delenv("CMUX_WORKSPACE_ID", raising=False)

        from worker_tui.app import StatusFooter

        footer = StatusFooter()
        footer.set_activity("thinking", busy=True)
        assert "● thinking" in str(footer.render())

        footer.set_activity("idle", busy=False)
        rendered = str(footer.render())
        assert "idle" in rendered
        assert "● thinking" not in rendered


# ── Bash !/!! tests ───────────────────────────────────────────────


class TestKeyboardLayoutFallbacks:
    def test_layout_safe_binding_variants_adds_cyrillic_alias_for_ctrl_shortcuts(self):
        from worker_tui.app import _layout_safe_binding_variants

        assert _layout_safe_binding_variants("ctrl+l") == ["ctrl+д"]
        assert _layout_safe_binding_variants("ctrl+p") == ["ctrl+з"]
        assert _layout_safe_binding_variants("ctrl+shift+c") == ["ctrl+shift+с"]
        assert _layout_safe_binding_variants("escape") == []


class TestCopyAssistantMessage:
    def test_copy_last_assistant_message_uses_clipboard(self):
        from worker_tui.app import MessageWidget, WorkerApp

        app = WorkerApp()
        app._assistant_message_history = [
            MessageWidget("first", role="assistant"),
            MessageWidget("final answer", role="assistant"),
        ]

        copied: list[str] = []
        seen_messages: list[tuple[str, str]] = []
        app.copy_to_clipboard = lambda text: copied.append(text)  # type: ignore[method-assign]
        app._add_message = lambda content, role="assistant": seen_messages.append((content, role))  # type: ignore[method-assign]

        app.action_copy_last_assistant_message()

        assert copied == ["final answer"]
        assert ("Copied last assistant message to clipboard.", "tool") in seen_messages

    def test_copy_last_assistant_message_reports_empty_state(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp()
        seen_messages: list[tuple[str, str]] = []
        app._add_message = lambda content, role="assistant": seen_messages.append((content, role))  # type: ignore[method-assign]

        app.action_copy_last_assistant_message()

        assert seen_messages == [("No assistant message available to copy.", "tool")]


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


class TestNewSessionCommand:
    @pytest.mark.asyncio
    async def test_new_alias_routes_to_clear(self, monkeypatch):
        from worker_tui.app import WorkerApp

        app = WorkerApp()
        called: list[str] = []

        async def _fake_clear() -> None:
            called.append("clear")

        monkeypatch.setattr(app, "action_clear", _fake_clear)

        await app._handle_command("/new")

        assert called == ["clear"]


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


async def _events(items):
    for item in items:
        yield item


class TestSlashCommandSuggestions:
    def test_matching_command_suggestions_include_dynamic_skill_entries(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._skills = {
            "debug": SimpleNamespace(name="debug", description="Debug the current issue"),
        }

        matches = app._matching_command_suggestions("/skill:d")

        assert [match.value for match in matches] == ["/skill:debug"]

    def test_matching_command_suggestions_hide_for_unknown_argument_context(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")

        assert app._matching_command_suggestions("/unknown something") == []

    def test_matching_command_suggestions_include_thinking_levels(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")

        matches = app._matching_command_suggestions("/thinking h")

        assert [match.value for match in matches] == ["high"]
        assert [match.completion for match in matches] == ["/thinking high"]

    def test_matching_command_suggestions_include_model_providers(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")

        matches = app._matching_command_suggestions("/model an")

        assert [match.value for match in matches] == ["anthropic/", "vertex_anthropic/"]
        assert [match.completion for match in matches] == ["/model anthropic/", "/model vertex_anthropic/"]

    def test_matching_command_suggestions_include_loaded_models(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._model_autocomplete_refs = [
            "anthropic/claude-sonnet-4-20250514",
            "openai/gpt-4.1",
        ]
        app._model_autocomplete_descriptions = {
            "anthropic/claude-sonnet-4-20250514": "Anthropic — Claude Sonnet 4, 200k ctx",
            "openai/gpt-4.1": "OpenAI — GPT-4.1, 128k ctx",
        }
        app._model_autocomplete_loaded = True

        matches = app._matching_command_suggestions("/model anthropic/cl")

        assert [match.value for match in matches] == ["anthropic/claude-sonnet-4-20250514"]
        assert [match.completion for match in matches] == ["/model anthropic/claude-sonnet-4-20250514"]

    def test_matching_command_suggestions_model_prefers_current_provider_and_substring(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._provider_model = "openai/gpt-4.1"
        app._model_autocomplete_refs = [
            "anthropic/gpt-helper",
            "openai/gpt-4.1",
            "openai/gpt-4.1-mini",
        ]
        app._model_autocomplete_descriptions = {
            "anthropic/gpt-helper": "Anthropic — GPT Helper",
            "openai/gpt-4.1": "OpenAI — GPT-4.1",
            "openai/gpt-4.1-mini": "OpenAI — GPT-4.1 Mini",
        }
        app._model_autocomplete_loaded = True

        provider_matches = app._matching_command_suggestions("/model gpt")
        model_matches = app._matching_command_suggestions("/model openai/gpt")

        assert [match.value for match in provider_matches] == [
            "openai/",
            "anthropic/",
        ]
        assert [match.value for match in model_matches] == [
            "openai/gpt-4.1",
            "openai/gpt-4.1-mini",
        ]

    def test_matching_command_suggestions_include_resume_entries(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._resume_autocomplete_suggestions = [
            SimpleNamespace(value="1", completion="/resume 1", search_text="1 remote-1 remote issue openai/gpt-4.1 /srv/project"),
            SimpleNamespace(value="remote-1", completion="/resume remote-1", search_text="remote-1 1 remote issue openai/gpt-4.1 /srv/project"),
        ]
        app._resume_autocomplete_loaded = True

        matches = app._matching_command_suggestions("/resume r")

        assert [match.value for match in matches] == ["1", "remote-1"]

    def test_matching_command_suggestions_resume_searches_by_title(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._resume_autocomplete_suggestions = [
            SimpleNamespace(value="1", completion="/resume 1", search_text="1 remote-1 billing issue openai/gpt-4.1 /srv/project"),
            SimpleNamespace(value="remote-1", completion="/resume remote-1", search_text="remote-1 1 billing issue openai/gpt-4.1 /srv/project"),
        ]
        app._resume_autocomplete_loaded = True

        matches = app._matching_command_suggestions("/resume bill")

        assert [match.value for match in matches] == ["1", "remote-1"]

    def test_matching_command_suggestions_include_image_remove_indexes(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._pending_attachments = [
            SimpleNamespace(path="/tmp/a.png", name="a.png"),
            SimpleNamespace(path="/tmp/b.png", name="b.png"),
        ]

        matches = app._matching_command_suggestions("/image-remove 2")

        assert [match.value for match in matches] == ["2"]
        assert [match.completion for match in matches] == ["/image-remove 2"]

    def test_matching_command_suggestions_include_real_fork_indexes(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._fork_autocomplete_suggestions = [
            SimpleNamespace(value="0", completion="/fork 0", search_text="0 user hello world"),
            SimpleNamespace(value="7", completion="/fork 7", search_text="7 assistant fix bug in parser"),
            SimpleNamespace(value="12", completion="/fork 12", search_text="12 user add tests"),
        ]
        app._fork_autocomplete_loaded = True

        matches = app._matching_command_suggestions("/fork pars")

        assert [match.value for match in matches] == ["7"]
        assert [match.completion for match in matches] == ["/fork 7"]

    def test_matching_command_suggestions_include_providers_command(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")

        matches = app._matching_command_suggestions("/prov")

        assert [match.value for match in matches] == ["/providers"]

    def test_matching_command_suggestions_mark_current_model_theme_and_thinking(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._provider_model = "openai/gpt-4.1"
        app._active_theme = "dracula"
        app._model_autocomplete_refs = ["openai/gpt-4.1", "openai/gpt-4.1-mini"]
        app._model_autocomplete_descriptions = {
            "openai/gpt-4.1": "OpenAI — GPT-4.1",
            "openai/gpt-4.1-mini": "OpenAI — GPT-4.1 Mini",
        }
        app._model_autocomplete_loaded = True
        app._session = SimpleNamespace(thinking_level="high")

        model_matches = app._matching_command_suggestions("/model openai/gpt")
        theme_matches = app._matching_command_suggestions("/theme dr")
        thinking_matches = app._matching_command_suggestions("/thinking h")

        assert [match.current for match in model_matches] == [True, False]
        assert [match.current for match in theme_matches] == [True]
        assert [match.current for match in thinking_matches] == [True]


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
        assert by_id["minimax"].name == "MiniMax"
        assert by_id["minimax"].hint == "set MINIMAX_API_KEY or [providers.minimax].api_key"
        assert by_id["ollama"].status == "keyless"
        assert "start the service" in by_id["ollama"].hint
        assert by_id["lmstudio"].status == "keyless"
        assert by_id["zai"].name == "Z.ai"
        assert by_id["zai"].hint == "set ZHIPU_API_KEY or [providers.zai].api_key"

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

class TestPermissionRequests:
    def test_worker_app_title_is_artel(self):
        from worker_tui.app import WorkerApp

        assert WorkerApp.TITLE == "Artel"
    @pytest.mark.asyncio
    async def test_permission_requests_are_queued_until_resolved(self, monkeypatch):
        from worker_tui.app import WorkerApp

        class _Panel:
            def __init__(self) -> None:
                self.opened: list[tuple[str, dict[str, str]]] = []
                self.closed = 0

            def open_request(self, tool_name: str, tool_args: dict[str, str]) -> None:
                self.opened.append((tool_name, dict(tool_args)))

            def close_request(self) -> None:
                self.closed += 1

        app = WorkerApp(remote_url="ws://localhost:7432")
        panel = _Panel()
        monkeypatch.setattr(app, "query_one", lambda selector, _cls=None: panel)
        monkeypatch.setattr(app, "call_after_refresh", lambda callback: None)

        first = asyncio.create_task(
            app._request_permission_decision("read", {"path": "README.md"})
        )
        await asyncio.sleep(0)
        second = asyncio.create_task(
            app._request_permission_decision("bash", {"command": "pwd"})
        )
        await asyncio.sleep(0)

        assert panel.opened == [("read", {"path": "README.md"})]

        app._resolve_permission_panel_decision("once")
        await asyncio.sleep(0)

        assert await first == "once"
        assert panel.opened == [
            ("read", {"path": "README.md"}),
            ("bash", {"command": "pwd"}),
        ]

        app._resolve_permission_panel_decision("deny")
        await asyncio.sleep(0)

        assert await second == "deny"

    @pytest.mark.asyncio
    async def test_ask_permission_notifies_cmux_with_artel_branding(self, monkeypatch):
        import worker_tui.app as tui_app
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        set_status_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        notify_calls: list[tuple[str, str, str | None]] = []

        async def fake_set_status(*args, **kwargs):
            set_status_calls.append((args, kwargs))

        async def fake_notify(title: str, subtitle: str = "", body: str | None = None):
            notify_calls.append((title, subtitle, body))

        monkeypatch.setattr(tui_app.cmux, "set_status", fake_set_status)
        monkeypatch.setattr(tui_app.cmux, "notify", fake_notify)
        monkeypatch.setattr(app, "_request_permission_decision", AsyncMock(return_value="once"))

        allowed = await app._ask_permission("bash", {"command": "pwd"})

        assert allowed is True
        assert set_status_calls == [
            (
                ("state", "permission: bash"),
                {"icon": "lock", "color": "#fab387"},
            )
        ]
        assert notify_calls == [("Artel", "Permission required: bash", None)]


class TestPromptAndSkillHints:
    def test_empty_prompts_hint_prefers_artel_paths(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp()
        app._prompts = {}
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        app._cmd_prompts()

        assert seen_messages == [
            (
                "No prompt templates found.\n"
                "Place .md files in ~/.config/artel/prompts/ or .artel/prompts/.\n"
                "Legacy Worker prompt paths are still supported.",
                "tool",
            )
        ]

    def test_empty_skills_hint_prefers_artel_paths(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp()
        app._skills = {}
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        app._cmd_skills_list()

        assert seen_messages == [
            (
                "No skills found.\n"
                "Place .md files in ~/.config/artel/skills/ or .artel/skills/.\n"
                "Legacy Worker skill paths are still supported.",
                "tool",
            )
        ]


class TestBoardSidebar:
    @pytest.mark.asyncio
    async def test_sidebar_toggle_and_board_commands(self, monkeypatch, tmp_path):
        from worker_tui.app import BoardSidebar, WorkerApp

        _patch_tui_test_context(monkeypatch)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".artel").mkdir()
        (tmp_path / ".artel" / "tasks.md").write_text("- [ ] Existing task\n", encoding="utf-8")
        (tmp_path / ".artel" / "operator-notes.md").write_text("remember this\n", encoding="utf-8")
        monkeypatch.setattr("worker_tui.app.WorkerApp._init_local_session", AsyncMock())

        app = WorkerApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            sidebar = app.query_one("#board-sidebar", BoardSidebar)
            assert not sidebar.has_class("visible")

            app.action_toggle_sidebar()
            await pilot.pause()
            assert sidebar.has_class("visible")

            await app._handle_command("/tasks")
            await app._handle_command("/notes")
            await app._handle_command("/task-add New task")
            await app._handle_command("/task-done 1")
            await pilot.pause()

            assert "New task" in sidebar.tasks_text()
            assert "remember this" in sidebar.notes_text()
            rendered = (tmp_path / ".artel" / "tasks.md").read_text(encoding="utf-8")
            assert "- [x] Existing task" in rendered
            assert "- [ ] New task" in rendered

    @pytest.mark.asyncio
    async def test_cyrillic_ctrl_shortcuts_trigger_critical_actions(self, monkeypatch, tmp_path):
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".artel").mkdir()
        monkeypatch.setattr("worker_tui.app.WorkerApp._init_local_session", AsyncMock())

        app = WorkerApp()
        copied: list[str] = []
        palette_calls: list[str] = []
        monkeypatch.setattr(app, "copy_to_clipboard", lambda text: copied.append(text))
        monkeypatch.setattr(app, "action_command_palette", lambda: palette_calls.append("palette"))
        app._assistant_message_history = [SimpleNamespace(content="copied reply")]

        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+з")
            await pilot.pause()
            assert palette_calls == ["palette"]

            await pilot.press("ctrl+д")
            await pilot.pause()
            assert len(app.query_one("#chat-container").children) == 0

            await pilot.press("ctrl+и")
            await pilot.pause()
            assert app._sidebar_visible is True

            await pilot.press("ctrl+т")
            await pilot.pause()
            assert app.query_one("#notes-editor").has_focus

            await pilot.press("ctrl+shift+с")
            await pilot.pause()
            assert copied == ["copied reply"]

    @pytest.mark.asyncio
    async def test_notes_open_focuses_notes_editor(self, monkeypatch, tmp_path):
        from textual.widgets import TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".artel").mkdir()
        monkeypatch.setattr("worker_tui.app.WorkerApp._init_local_session", AsyncMock())

        app = WorkerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app._handle_command("/notes-open")
            await pilot.pause()
            notes_editor = app.query_one("#notes-editor", TextArea)
            assert app._sidebar_visible is True

    @pytest.mark.asyncio
    async def test_notes_editor_debounced_save_creates_file_readable_by_tool(self, monkeypatch, tmp_path):
        from textual.widgets import Static, TextArea
        from worker_core.tools.builtins import ReadOperatorNotesTool
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".artel").mkdir()
        monkeypatch.setattr("worker_tui.app.WorkerApp._init_local_session", AsyncMock())

        app = WorkerApp()
        app._board_save_delay = 0.05
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_focus_notes()
            await pilot.pause()
            notes_editor = app.query_one("#notes-editor", TextArea)
            notes_editor.load_text("remember to revisit task UX")
            await pilot.pause()
            await asyncio.sleep(0.08)
            await pilot.pause()
            status = app.query_one("#board-status", Static)
            assert str(status.render()) == "Operator notes saved"

        notes_path = tmp_path / ".artel" / "operator-notes.md"
        assert notes_path.exists()
        assert notes_path.read_text(encoding="utf-8").strip() == "remember to revisit task UX"

        tool = ReadOperatorNotesTool(str(tmp_path))
        rendered = await tool.execute()
        assert "1|remember to revisit task UX" in rendered

    @pytest.mark.asyncio
    async def test_poll_board_state_refreshes_sidebar_after_external_task_change(self, monkeypatch, tmp_path):
        from worker_tui.app import BoardSidebar, WorkerApp

        _patch_tui_test_context(monkeypatch)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".artel").mkdir()
        (tmp_path / ".artel" / "tasks.md").write_text("- [ ] Initial\n", encoding="utf-8")
        monkeypatch.setattr("worker_tui.app.WorkerApp._init_local_session", AsyncMock())

        app = WorkerApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            sidebar = app.query_one("#board-sidebar", BoardSidebar)
            app.action_toggle_sidebar()
            await pilot.pause()
            assert "Initial" in sidebar.tasks_text()

            (tmp_path / ".artel" / "tasks.md").write_text("- [ ] Initial\n- [ ] External update\n", encoding="utf-8")
            await app._poll_board_state_once()
            await pilot.pause()

            assert "External update" in sidebar.tasks_text()


class TestTuiAutocompleteIntegration:
    @pytest.mark.asyncio
    async def test_input_is_focused_on_mount(self, monkeypatch):
        from textual.widgets import TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            assert input_bar.has_focus

    @pytest.mark.asyncio
    async def test_slash_command_suggestions_filter_and_tab_complete(self, monkeypatch):
        from textual.widgets import OptionList, TextArea
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

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/m")
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

            assert input_bar.text == "/model"
            assert not suggestions.has_class("visible")

    @pytest.mark.asyncio
    async def test_thinking_argument_suggestions_tab_complete(self, monkeypatch):
        from textual.widgets import OptionList, TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/thinking h")
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            assert suggestions.has_class("visible")
            suggestion_ids = [
                suggestions.get_option_at_index(i).id
                for i in range(suggestions.option_count)
            ]
            assert suggestion_ids == ["/thinking high"]

            await pilot.press("tab")
            await pilot.pause()

            assert input_bar.text == "/thinking high"
            assert not suggestions.has_class("visible")

    @pytest.mark.asyncio
    async def test_model_argument_suggestions_tab_complete(self, monkeypatch):
        from textual.widgets import OptionList, TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._model_autocomplete_refs = ["anthropic/claude-sonnet-4-20250514"]
        app._model_autocomplete_descriptions = {
            "anthropic/claude-sonnet-4-20250514": "Anthropic — Claude Sonnet 4, 200k ctx"
        }
        app._model_autocomplete_loaded = True

        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/model anthropic/cl")
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            assert suggestions.has_class("visible")
            suggestion_ids = [
                suggestions.get_option_at_index(i).id
                for i in range(suggestions.option_count)
            ]
            assert suggestion_ids == ["/model anthropic/claude-sonnet-4-20250514"]

            await pilot.press("tab")
            await pilot.pause()

            assert input_bar.text == "/model anthropic/claude-sonnet-4-20250514"
            assert not suggestions.has_class("visible")

    @pytest.mark.asyncio
    async def test_cd_argument_suggestions_tab_complete(self, monkeypatch, tmp_path):
        from textual.widgets import OptionList, TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)
        (tmp_path / "src").mkdir()
        (tmp_path / "scripts").mkdir()
        monkeypatch.chdir(tmp_path)

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/cd s")
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            assert suggestions.has_class("visible")
            suggestion_ids = [
                suggestions.get_option_at_index(i).id
                for i in range(suggestions.option_count)
            ]
            assert suggestion_ids == [
                f"/cd {tmp_path / 'scripts'}",
                f"/cd {tmp_path / 'src'}",
            ]

            await pilot.press("tab")
            await pilot.pause()

            assert input_bar.text == f"/cd {tmp_path / 'scripts'}"
            assert not suggestions.has_class("visible")

    @pytest.mark.asyncio
    async def test_cd_argument_suggestions_quote_paths_with_spaces(self, monkeypatch, tmp_path):
        from textual.widgets import OptionList, TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)
        spaced = tmp_path / "my project"
        spaced.mkdir()
        monkeypatch.chdir(tmp_path)

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/cd my")
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            suggestion_ids = [
                suggestions.get_option_at_index(i).id
                for i in range(suggestions.option_count)
            ]
            assert suggestion_ids == [f"/cd '{spaced}'"]

            await pilot.press("tab")
            await pilot.pause()

            assert input_bar.text == f"/cd '{spaced}'"

    @pytest.mark.asyncio
    async def test_resume_argument_suggestions_tab_complete(self, monkeypatch):
        from textual.widgets import OptionList, TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._resume_autocomplete_suggestions = [
            SimpleNamespace(
                value="1",
                description="Remote issue",
                completion="/resume 1",
                search_text="1 remote-1 remote issue openai/gpt-4.1 /srv/project",
            ),
            SimpleNamespace(
                value="remote-1",
                description="Remote issue",
                completion="/resume remote-1",
                search_text="remote-1 1 remote issue openai/gpt-4.1 /srv/project",
            ),
        ]
        app._resume_autocomplete_loaded = True

        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/resume r")
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            assert suggestions.has_class("visible")
            suggestion_ids = [
                suggestions.get_option_at_index(i).id
                for i in range(suggestions.option_count)
            ]
            assert suggestion_ids == ["/resume 1", "/resume remote-1"]

            await pilot.press("tab")
            await pilot.pause()

            assert input_bar.text == "/resume 1"
            assert not suggestions.has_class("visible")

    @pytest.mark.asyncio
    async def test_image_argument_suggestions_tab_complete(self, monkeypatch, tmp_path):
        from textual.widgets import OptionList, TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)
        image_path = tmp_path / "shot.png"
        image_path.write_bytes(b"png")
        (tmp_path / "notes.txt").write_text("hello", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/image s")
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            assert suggestions.has_class("visible")
            suggestion_ids = [
                suggestions.get_option_at_index(i).id
                for i in range(suggestions.option_count)
            ]
            assert suggestion_ids == [f"/image {image_path}"]

            await pilot.press("tab")
            await pilot.pause()

            assert input_bar.text == f"/image {image_path}"
            assert not suggestions.has_class("visible")

    @pytest.mark.asyncio
    async def test_image_argument_suggestions_quote_paths_with_spaces(self, monkeypatch, tmp_path):
        from textual.widgets import OptionList, TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)
        image_path = tmp_path / "screen shot.png"
        image_path.write_bytes(b"png")
        monkeypatch.chdir(tmp_path)

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/image scr")
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            suggestion_ids = [
                suggestions.get_option_at_index(i).id
                for i in range(suggestions.option_count)
            ]
            assert suggestion_ids == [f"/image '{image_path}'"]

            await pilot.press("tab")
            await pilot.pause()

            assert input_bar.text == f"/image '{image_path}'"

    @pytest.mark.asyncio
    async def test_image_remove_argument_suggestions_tab_complete(self, monkeypatch):
        from textual.widgets import OptionList, TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._pending_attachments = [
            SimpleNamespace(path="/tmp/a.png", mime_type="image/png", name="a.png"),
            SimpleNamespace(path="/tmp/b.png", mime_type="image/png", name="b.png"),
        ]

        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/image-remove 2")
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            assert suggestions.has_class("visible")
            suggestion_ids = [
                suggestions.get_option_at_index(i).id
                for i in range(suggestions.option_count)
            ]
            assert suggestion_ids == ["/image-remove 2"]

            await pilot.press("tab")
            await pilot.pause()

            assert input_bar.text == "/image-remove 2"
            assert not suggestions.has_class("visible")

    @pytest.mark.asyncio
    async def test_browser_argument_suggestions_tab_complete(self, monkeypatch):
        from textual.widgets import OptionList, TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/browser ht")
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            assert suggestions.has_class("visible")
            suggestion_ids = [
                suggestions.get_option_at_index(i).id
                for i in range(suggestions.option_count)
            ]
            assert suggestion_ids == ["/browser https://", "/browser http://"]

            await pilot.press("tab")
            await pilot.pause()

            assert input_bar.text == "/browser https://"
            assert not suggestions.has_class("visible")

    @pytest.mark.asyncio
    async def test_fork_argument_suggestions_tab_complete(self, monkeypatch):
        from textual.widgets import OptionList, TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._fork_autocomplete_suggestions = [
            SimpleNamespace(
                value="0",
                description="[user] hello world",
                completion="/fork 0",
                search_text="0 user hello world",
            ),
            SimpleNamespace(
                value="7",
                description="[assistant] fix bug in parser",
                completion="/fork 7",
                search_text="7 assistant fix bug in parser",
            ),
        ]
        app._fork_autocomplete_loaded = True

        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/fork pars")
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            assert suggestions.has_class("visible")
            suggestion_ids = [
                suggestions.get_option_at_index(i).id
                for i in range(suggestions.option_count)
            ]
            assert suggestion_ids == ["/fork 7"]

            await pilot.press("tab")
            await pilot.pause()

            assert input_bar.text == "/fork 7"
            assert not suggestions.has_class("visible")

    @pytest.mark.asyncio
    async def test_current_value_is_marked_in_option_labels(self, monkeypatch):
        from textual.widgets import OptionList, TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")

        async with app.run_test() as pilot:
            await pilot.pause()
            app._active_theme = "dracula"

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/theme dr")
            await pilot.pause()

            suggestions = app.query_one("#command-suggestions", OptionList)
            prompt = suggestions.get_option_at_index(0).prompt
            assert str(prompt).startswith("✓ dracula —")

    @pytest.mark.asyncio
    async def test_slash_command_suggestions_navigate_past_first_five_items(self, monkeypatch):
        from textual.widgets import OptionList, TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test(size=(80, 18)) as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.load_text("/")
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

            assert input_bar.text == visible_commands[5]

    @pytest.mark.asyncio
    async def test_multiline_composer_submits_full_text_and_clears_input(self, monkeypatch):
        from textual.widgets import TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        sent: list[str] = []
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": None)
        monkeypatch.setattr(app, "call_after_refresh", lambda callback: None)
        monkeypatch.setattr(app, "_run_remote", lambda text: sent.append(text))

        async with app.run_test() as pilot:
            await pilot.pause()
            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.focus()
            input_bar.load_text("line 1\nline 2")

            await app.action_submit_composer()
            await pilot.pause()

            assert sent == ["line 1\nline 2"]
            assert input_bar.text == ""

    @pytest.mark.asyncio
    async def test_enter_submits_composer(self, monkeypatch):
        from textual.widgets import TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        sent: list[str] = []
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": None)
        monkeypatch.setattr(app, "call_after_refresh", lambda callback: None)
        monkeypatch.setattr(app, "_run_remote", lambda text: sent.append(text))

        async with app.run_test() as pilot:
            await pilot.pause()
            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.focus()
            input_bar.load_text("hello")
            input_bar.move_cursor((0, len("hello")))

            await pilot.press("enter")
            await pilot.pause()

            assert sent == ["hello"]
            assert input_bar.text == ""

    @pytest.mark.asyncio
    async def test_shift_enter_inserts_newline_without_submit(self, monkeypatch):
        from textual.widgets import TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        sent: list[str] = []
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": None)
        monkeypatch.setattr(app, "call_after_refresh", lambda callback: None)
        monkeypatch.setattr(app, "_run_remote", lambda text: sent.append(text))

        async with app.run_test() as pilot:
            await pilot.pause()
            input_bar = app.query_one("#input-bar", TextArea)
            input_bar.focus()
            input_bar.load_text("hello")
            input_bar.move_cursor((0, len("hello")))

            await pilot.press("shift+enter")
            await pilot.pause()

            assert sent == []
            assert input_bar.text == "hello\n"

    @pytest.mark.asyncio
    async def test_pending_attachments_bar_becomes_visible(self, monkeypatch, tmp_path):
        from worker_ai.models import ImageAttachment
        from worker_tui.app import PendingAttachmentsBar, WorkerApp

        _patch_tui_test_context(monkeypatch)

        image_path = tmp_path / "shot.png"
        image_path.write_bytes(b"png-data")

        app = WorkerApp(remote_url="ws://localhost:7432")
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": None)

        async with app.run_test() as pilot:
            await pilot.pause()
            bar = app.query_one("#pending-attachments", PendingAttachmentsBar)
            assert not bar.has_class("visible")

            app._queue_attachment(
                ImageAttachment(path=str(image_path), mime_type="image/png", name="shot.png")
            )
            await pilot.pause()

            assert bar.has_class("visible")
            rendered = str(bar.render())
            assert "shot.png" in rendered
            assert "image/png" in rendered

    @pytest.mark.asyncio
    async def test_permission_panel_is_inline_and_restores_input_focus(self, monkeypatch):
        from textual.containers import Vertical
        from textual.widgets import TextArea
        from worker_tui.app import PermissionPanel, WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()

            decision_task = asyncio.create_task(
                app._request_permission_decision("bash", {"command": "printf hello"})
            )
            await pilot.pause()

            panel = app.query_one("#permission-panel", PermissionPanel)
            main = app.query_one("#main-content", Vertical)
            input_bar = app.query_one("#input-bar", TextArea)

            assert list(main.children)[0] is panel
            assert panel.has_class("visible")
            assert panel.has_focus
            assert not input_bar.has_focus

            panel.action_approve_all()
            await pilot.pause()

            assert await decision_task == "all"
            assert not panel.has_class("visible")
            assert input_bar.has_focus


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

    def test_remote_payload_includes_attachments(self, tmp_path):
        from worker_ai.models import ImageAttachment
        from worker_tui.app import WorkerApp

        image_path = tmp_path / "shot.png"
        image_path.write_bytes(b"png-data")

        app = WorkerApp(remote_url="ws://localhost:7432", auth_token="tok_123")
        payload = app._remote_message_payload(
            "hello",
            attachments=[
                ImageAttachment(path=str(image_path), mime_type="image/png", name="shot.png")
            ],
        )
        assert payload["attachments"] == [
            {"path": str(image_path), "mime_type": "image/png", "name": "shot.png"}
        ]


class TestManagedLocalServer:
    @pytest.mark.asyncio
    async def test_ensure_managed_local_server_reuses_healthy_registry(self, tmp_path, monkeypatch):
        import worker_tui.local_server as local_server_mod

        handle = local_server_mod.LocalServerHandle(
            remote_url="ws://127.0.0.1:9011",
            auth_token="artel_existing",
            project_dir=str(tmp_path),
            pid=777,
        )
        local_server_mod._save_registry(handle)

        async def fake_server_matches_project(candidate):
            assert candidate == handle
            return True

        monkeypatch.setattr(
            local_server_mod,
            "_server_matches_project",
            fake_server_matches_project,
        )
        monkeypatch.setattr(
            local_server_mod,
            "load_config",
            lambda _: (_ for _ in ()).throw(AssertionError("load_config should not be called")),
        )

        reused = await local_server_mod.ensure_managed_local_server(str(tmp_path))

        assert reused == handle

    @pytest.mark.asyncio
    async def test_ensure_managed_local_server_starts_detached_server_and_saves_registry(
        self,
        tmp_path,
        monkeypatch,
    ):
        import json

        import worker_tui.local_server as local_server_mod

        config = SimpleNamespace(
            server=SimpleNamespace(auth_token="artel_configured", port=7432),
        )
        started: dict[str, object] = {}

        class _Process:
            pid = 4321
            returncode = None

            def poll(self):
                return None

        def fake_popen(command, **kwargs):
            started["command"] = command
            started["kwargs"] = kwargs
            return _Process()

        async def fake_wait_until_ready(handle, process):
            started["handle"] = handle
            started["process"] = process

        monkeypatch.setattr(local_server_mod, "load_config", lambda _: config)
        monkeypatch.setattr(local_server_mod, "_pick_port", lambda preferred_port: 9011)
        monkeypatch.setattr(
            local_server_mod,
            "_wait_until_ready",
            fake_wait_until_ready,
        )
        monkeypatch.setattr(local_server_mod.subprocess, "Popen", fake_popen)

        handle = await local_server_mod.ensure_managed_local_server(str(tmp_path))

        assert handle.remote_url == "ws://127.0.0.1:9011"
        assert handle.auth_token == "artel_configured"
        assert handle.project_dir == str(tmp_path)
        assert handle.pid == 4321
        assert started["command"] == local_server_mod._server_command(9011, "artel_configured")
        assert started["kwargs"]["cwd"] == str(tmp_path)
        assert started["kwargs"]["start_new_session"] is True
        registry = json.loads((tmp_path / ".artel" / "server.json").read_text())
        assert registry["remote_url"] == handle.remote_url
        assert registry["auth_token"] == handle.auth_token
        assert registry["pid"] == handle.pid

    @pytest.mark.asyncio
    async def test_restart_managed_local_server_kills_existing_pid_and_restarts(
        self,
        tmp_path,
        monkeypatch,
    ):
        import worker_tui.local_server as local_server_mod

        existing = local_server_mod.LocalServerHandle(
            remote_url="ws://127.0.0.1:9011",
            auth_token="artel_existing",
            project_dir=str(tmp_path),
            pid=777,
        )
        local_server_mod._save_registry(existing)
        killed: list[tuple[int, int]] = []
        restarted = local_server_mod.LocalServerHandle(
            remote_url="ws://127.0.0.1:9012",
            auth_token="artel_new",
            project_dir=str(tmp_path),
            pid=888,
        )

        def fake_kill(pid: int, sig: int) -> None:
            killed.append((pid, sig))

        async def fake_ensure(project_dir: str | None = None, ensure_tray: bool = True):
            assert project_dir == str(tmp_path)
            return restarted

        monkeypatch.setattr(local_server_mod.os, "kill", fake_kill)
        monkeypatch.setattr(local_server_mod, "ensure_managed_local_server", fake_ensure)
        monkeypatch.setattr(local_server_mod, "_managed_server_processes", lambda project_dir: [])

        handle = await local_server_mod.restart_managed_local_server(str(tmp_path))

        assert handle == restarted
        assert killed == [(777, local_server_mod.signal.SIGTERM)]
        assert not (tmp_path / ".artel" / "server.json").exists()


class TestRemoteModeCommandRouting:
    @pytest.mark.asyncio
    async def test_server_restart_command_restarts_local_managed_server(self, monkeypatch):
        import worker_tui.app as tui_app
        from worker_tui.app import WorkerApp

        restarted = SimpleNamespace(remote_url="ws://127.0.0.1:9999", auth_token="artel_new")
        calls: list[str] = []

        async def fake_restart(project_dir: str, ensure_tray: bool = True):
            calls.append(project_dir)
            return restarted

        async def fake_sync(self):
            calls.append("sync")

        monkeypatch.setattr(tui_app, "restart_managed_local_server", fake_restart)
        monkeypatch.setattr(WorkerApp, "_sync_remote_session_state", fake_sync)

        app = WorkerApp(remote_url="ws://127.0.0.1:7432", auth_token="old")
        messages: list[tuple[str, str]] = []
        app._add_message = lambda content, role="assistant": messages.append((content, role))  # type: ignore[method-assign]

        await app._cmd_server_restart()

        assert calls[0]
        assert calls[1] == "sync"
        assert app.remote_url == "ws://127.0.0.1:9999"
        assert app.auth_token == "artel_new"
        assert app._remote_control_client is None
        assert messages[-1] == (
            "Managed local Artel server restarted: ws://127.0.0.1:9999",
            "tool",
        )

    @pytest.mark.asyncio
    async def test_server_restart_command_rejects_non_local_remote(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://example.com:7432", auth_token="tok")
        messages: list[tuple[str, str]] = []
        app._add_message = lambda content, role="assistant": messages.append((content, role))  # type: ignore[method-assign]

        await app._cmd_server_restart()

        assert messages[-1] == (
            "Server restart is only supported for local managed Artel servers.",
            "error",
        )

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
    async def test_sync_remote_session_state_falls_back_to_server_info(self):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            async def get_session(self, session_id: str):
                raise RuntimeError(f"missing session: {session_id}")

            async def get_server_info(self):
                return {
                    "default_model": "openai/gpt-4.1",
                    "project_dir": "/srv/fallback/project",
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
        assert footer.cwd == "/srv/fallback/project"
        assert app._provider_model == "openai/gpt-4.1"
        assert app._remote_project_dir == "/srv/fallback/project"
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

    def test_remote_extension_commands_appear_in_command_suggestions(self):
        from worker_tui.app import WorkerApp

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_extension_commands = {"delegate"}

        values = [suggestion.value for suggestion in app._command_suggestions()]

        assert "/delegate" in values

    @pytest.mark.asyncio
    async def test_handle_command_dispatches_remote_extension_command(self):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            async def list_session_commands(self, session_id: str):
                return {"commands": ["delegate"]}

            async def run_session_command(self, session_id: str, command_name: str, arg: str):
                return {"command": command_name, "output": f"delegated:{arg}"}

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        await app._handle_command("/delegate inspect src")

        assert app._remote_extension_commands == {"delegate"}
        assert seen_messages == [("delegated:inspect src", "tool")]

    @pytest.mark.asyncio
    async def test_restore_initial_remote_session_continue_uses_latest_session(self):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            async def list_sessions(self):
                return {"sessions": [{"id": "remote-latest"}]}

        app = WorkerApp(remote_url="ws://localhost:7432", continue_session=True)
        app._remote_control_client = _RemoteClient()
        app._resume_remote_session = AsyncMock()  # type: ignore[method-assign]
        app._sync_remote_session_state = AsyncMock()  # type: ignore[method-assign]

        await app._restore_initial_remote_session()

        app._resume_remote_session.assert_awaited_once_with("remote-latest")
        app._sync_remote_session_state.assert_not_awaited()

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

            async def list_session_commands(self, session_id: str):
                return {"commands": []}

            async def get_session_tasks(self, session_id: str):
                return {"content": ""}

            async def get_session_notes(self, session_id: str):
                return {"content": ""}

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
        app.run_worker = lambda *args, **kwargs: None  # type: ignore[method-assign]
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
    async def test_resume_command_restores_remote_tool_cards(self):
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

            async def list_session_commands(self, session_id: str):
                return {"commands": []}

            async def get_session_tasks(self, session_id: str):
                return {"content": ""}

            async def get_session_notes(self, session_id: str):
                return {"content": ""}

            async def get_session_messages(self, session_id: str):
                return {
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {
                            "role": "assistant",
                            "content": "I'll inspect that",
                            "tool_calls": [
                                {"id": "tc1", "name": "read", "arguments": {"path": "README.md"}},
                            ],
                        },
                        {
                            "role": "tool",
                            "tool_result": {"tool_call_id": "tc1", "content": "1|# Title", "is_error": False},
                        },
                    ]
                }

        class _Container:
            def remove_children(self) -> None:
                pass

        class _Footer:
            def set_model(self, model: str) -> None:
                pass

            def set_cwd(self, cwd: str) -> None:
                pass

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        app._active_tool_cards = {}
        app._tool_call_names = {}
        app.run_worker = lambda *args, **kwargs: None  # type: ignore[method-assign]
        container = _Container()
        footer = _Footer()
        app.query_one = lambda selector, _cls=None: (  # type: ignore[method-assign]
            container if selector == "#chat-container" else footer
        )
        seen_messages: list[tuple[str, str]] = []
        started_tool_cards: list[tuple[str, str, str]] = []
        finished_tool_cards: list[tuple[str, str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )
        app._start_tool_card = (  # type: ignore[method-assign]
            lambda call_id, *, title, body="": started_tool_cards.append((call_id, title, body))
        )
        app._finish_tool_card = (  # type: ignore[method-assign]
            lambda call_id, *, title, body, markdown=False, display=None, kind="text", status_badge="", status_variant="neutral": finished_tool_cards.append((call_id, title, body))
        )

        await app._cmd_resume("remote-1")

        assert seen_messages == [
            ("hello", "user"),
            ("I'll inspect that", "assistant"),
            ("Resumed remote session: Remote issue", "tool"),
        ]
        assert started_tool_cards == [("tc1", "⚙ read README.md", "")]
        assert finished_tool_cards == [("tc1", "✓ read", "1|# Title")]

    @pytest.mark.asyncio
    async def test_resume_command_restores_remote_reasoning_blocks(self):
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

            async def list_session_commands(self, session_id: str):
                return {"commands": []}

            async def get_session_tasks(self, session_id: str):
                return {"content": ""}

            async def get_session_notes(self, session_id: str):
                return {"content": ""}

            async def get_session_messages(self, session_id: str):
                return {
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {
                            "role": "assistant",
                            "reasoning": "first thought",
                            "content": "world",
                        },
                    ]
                }

        class _Container:
            def remove_children(self) -> None:
                pass

        class _Footer:
            def set_model(self, model: str) -> None:
                pass

            def set_cwd(self, cwd: str) -> None:
                pass

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        app.run_worker = lambda *args, **kwargs: None  # type: ignore[method-assign]
        container = _Container()
        footer = _Footer()
        app.query_one = lambda selector, _cls=None: (  # type: ignore[method-assign]
            container if selector == "#chat-container" else footer
        )
        seen_messages: list[tuple[str, str]] = []
        seen_reasoning: list[str] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )
        app._add_reasoning_block = (  # type: ignore[method-assign]
            lambda content="": seen_reasoning.append(content)
        )

        await app._cmd_resume("remote-1")

        assert seen_reasoning == ["first thought"]
        assert seen_messages == [
            ("hello", "user"),
            ("world", "assistant"),
            ("Resumed remote session: Remote issue", "tool"),
        ]

    @pytest.mark.asyncio
    async def test_resume_command_restores_local_reasoning_blocks(self, tmp_path):
        from worker_ai.models import Message, Role
        from worker_core.sessions import SessionStore
        from worker_tui.app import WorkerApp

        class _Container:
            def remove_children(self) -> None:
                pass

        class _Session:
            def __init__(self):
                self.session_id = "current-session"
                self.messages = [Message(role=Role.SYSTEM, content="system")]
                self.thinking_level = "off"

        store = SessionStore(str(tmp_path / "sessions.db"))
        await store.open()
        try:
            await store.create_session("local-1", "openai/gpt-4.1", title="Local issue")
            await store.add_message("local-1", Message(role=Role.USER, content="hello"))
            await store.add_message(
                "local-1",
                Message(role=Role.ASSISTANT, reasoning="first thought", content="world"),
            )

            app = WorkerApp()
            app._store = store
            app._session = _Session()
            app._provider_model = "openai/gpt-4.1"
            app._tool_collapsibles = []
            container = _Container()
            app.query_one = lambda selector, _cls=None: container  # type: ignore[method-assign]
            seen_messages: list[tuple[str, str]] = []
            seen_reasoning: list[str] = []
            app._add_message = (  # type: ignore[method-assign]
                lambda content, role="assistant": seen_messages.append((content, role))
            )
            app._add_reasoning_block = (  # type: ignore[method-assign]
                lambda content="": seen_reasoning.append(content)
            )

            await app._resume_session("local-1")
        finally:
            await store.close()

        assert seen_reasoning == ["first thought"]
        assert seen_messages == [
            ("hello", "user"),
            ("world", "assistant"),
            ("Resumed session: Local issue", "tool"),
        ]

    @pytest.mark.asyncio
    async def test_resume_command_restores_local_tool_cards(self, tmp_path):
        from worker_ai.models import Message, Role, ToolCall, ToolResult
        from worker_core.sessions import SessionStore
        from worker_tui.app import WorkerApp

        class _Container:
            def remove_children(self) -> None:
                pass

        class _Session:
            def __init__(self):
                self.session_id = "current-session"
                self.messages = [Message(role=Role.SYSTEM, content="system")]
                self.thinking_level = "off"

        store = SessionStore(str(tmp_path / "sessions.db"))
        await store.open()
        try:
            await store.create_session("local-1", "openai/gpt-4.1", title="Local issue")
            await store.add_message("local-1", Message(role=Role.USER, content="hello"))
            await store.add_message(
                "local-1",
                Message(
                    role=Role.ASSISTANT,
                    content="I'll inspect that",
                    tool_calls=[ToolCall(id="tc1", name="read", arguments={"path": "README.md"})],
                ),
            )
            await store.add_message(
                "local-1",
                Message(
                    role=Role.TOOL,
                    tool_result=ToolResult(tool_call_id="tc1", content="1|# Title", is_error=False),
                ),
            )

            app = WorkerApp()
            app._store = store
            app._session = _Session()
            app._provider_model = "openai/gpt-4.1"
            app._tool_collapsibles = []
            app._active_tool_cards = {}
            container = _Container()
            app.query_one = lambda selector, _cls=None: container  # type: ignore[method-assign]
            seen_messages: list[tuple[str, str]] = []
            started_tool_cards: list[tuple[str, str, str]] = []
            finished_tool_cards: list[tuple[str, str, str]] = []
            app._add_message = (  # type: ignore[method-assign]
                lambda content, role="assistant": seen_messages.append((content, role))
            )
            app._start_tool_card = (  # type: ignore[method-assign]
                lambda call_id, *, title, body="": started_tool_cards.append((call_id, title, body))
            )
            app._finish_tool_card = (  # type: ignore[method-assign]
                lambda call_id, *, title, body, markdown=False, display=None, kind="text", status_badge="", status_variant="neutral": finished_tool_cards.append((call_id, title, body))
            )

            await app._resume_session("local-1")
        finally:
            await store.close()

        assert seen_messages == [
            ("hello", "user"),
            ("I'll inspect that", "assistant"),
            ("Resumed session: Local issue", "tool"),
        ]
        assert started_tool_cards == [("tc1", "⚙ read README.md", "")]
        assert finished_tool_cards == [("tc1", "✓ read", "1|# Title")]

    @pytest.mark.asyncio
    async def test_resume_command_restores_local_thinking_level(self, tmp_path):
        from worker_ai.models import Message, Role
        from worker_core.sessions import SessionStore
        from worker_tui.app import WorkerApp

        class _Container:
            def __init__(self):
                self.cleared = False

            def remove_children(self) -> None:
                self.cleared = True

        class _Session:
            def __init__(self):
                self.session_id = "current-session"
                self.messages = [Message(role=Role.SYSTEM, content="system")]
                self.thinking_level = "off"

        store = SessionStore(str(tmp_path / "sessions.db"))
        await store.open()
        try:
            await store.create_session(
                "local-1",
                "openai/gpt-4.1",
                title="Local issue",
                thinking_level="high",
            )
            await store.add_message("local-1", Message(role=Role.USER, content="hello"))
            await store.add_message("local-1", Message(role=Role.ASSISTANT, content="world"))

            app = WorkerApp()
            app._store = store
            app._session = _Session()
            app._provider_model = "openai/gpt-4.1"
            app._tool_collapsibles = ["tool"]
            container = _Container()
            app.query_one = lambda selector, _cls=None: container  # type: ignore[method-assign]
            seen_messages: list[tuple[str, str]] = []
            app._add_message = (  # type: ignore[method-assign]
                lambda content, role="assistant": seen_messages.append((content, role))
            )

            await app._resume_session("local-1")
        finally:
            await store.close()

        assert container.cleared is True
        assert app._tool_collapsibles == []
        assert app._session is not None
        assert app._session.session_id == "local-1"
        assert app._session.thinking_level == "high"
        assert [message.content for message in app._session.messages[1:]] == ["hello", "world"]
        assert seen_messages == [
            ("hello", "user"),
            ("world", "assistant"),
            ("Resumed session: Local issue", "tool"),
        ]

    @pytest.mark.asyncio
    async def test_fork_command_passes_remote_message_index(self):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            def __init__(self):
                self.calls: list[tuple[str, int | None]] = []

            async def fork_session(self, session_id: str, *, message_index: int | None = None):
                self.calls.append((session_id, message_index))
                return {"session_id": "forked-session"}

        app = WorkerApp(remote_url="ws://localhost:7432")
        client = _RemoteClient()
        app._remote_control_client = client
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        await app._cmd_fork("7")

        assert client.calls == [(app._remote_session_id, 7)]
        assert seen_messages[0][1] == "tool"
        assert "message 7" in seen_messages[0][0]
        assert "/resume" in seen_messages[0][0]

    @pytest.mark.asyncio
    async def test_skill_command_uses_remote_control_client(self):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            async def inject_skill(self, session_id: str, skill: str):
                return {"status": "ok", "session_id": session_id, "skill": skill}

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        await app._cmd_skill("debug")

        assert seen_messages == [("Skill 'debug' loaded into session.", "tool")]

    @pytest.mark.asyncio
    async def test_export_command_writes_remote_session_history(self, tmp_path, monkeypatch):
        from worker_tui.app import WorkerApp

        class _RemoteClient:
            async def get_session(self, session_id: str):
                return {"session": {"id": session_id, "model": "openai/gpt-4.1"}}

            async def get_session_messages(self, session_id: str):
                return {
                    "messages": [
                        {"role": "user", "content": "hello"},
                        {"role": "assistant", "content": "world"},
                        {"role": "system", "content": "ignored"},
                    ]
                }

        monkeypatch.chdir(tmp_path)

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        await app._cmd_export("remote.html")

        html = (tmp_path / "remote.html").read_text(encoding="utf-8")
        assert "hello" in html
        assert "world" in html
        assert "ignored" not in html
        assert seen_messages == [("Exported to remote.html", "tool")]

    @pytest.mark.asyncio
    async def test_reload_command_reloads_remote_session_and_local_resources(self, monkeypatch):
        import worker_tui.app as tui_app
        from worker_tui.app import WorkerApp

        class _Footer:
            def __init__(self):
                self.model = ""
                self.cwd = ""

            def set_model(self, model: str) -> None:
                self.model = model

            def set_cwd(self, cwd: str) -> None:
                self.cwd = cwd

        class _RemoteClient:
            async def reload_session(self, session_id: str):
                return {
                    "session": {
                        "id": session_id,
                        "model": "openai/gpt-4.1",
                        "project_dir": "/srv/project",
                    }
                }

        mounted: list[object] = []

        class _Ext:
            async def mount(self, app):
                mounted.append(app)

        async def fake_reload_tui_extensions(existing, *, context):
            assert context.runtime == "tui"
            return [_Ext(), _Ext()]

        monkeypatch.setattr(tui_app, "load_config", lambda _: _tui_test_config())
        monkeypatch.setattr(
            tui_app,
            "reload_tui_extensions_async",
            fake_reload_tui_extensions,
        )
        monkeypatch.setattr(tui_app, "load_prompts", lambda _: {"fix": "Prompt"})
        monkeypatch.setattr(
            tui_app,
            "load_skills",
            lambda _: {"debug": SimpleNamespace(name="debug", description="Debug")},
        )

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._remote_control_client = _RemoteClient()
        footer = _Footer()
        app.query_one = lambda selector, _cls=None: footer  # type: ignore[method-assign]
        app._tui_extensions = [object()]
        registered: list[object] = []
        app._register_tui_extension_keybindings = (  # type: ignore[method-assign]
            lambda ext: registered.append(ext)
        )
        seen_messages: list[tuple[str, str]] = []
        app._add_message = (  # type: ignore[method-assign]
            lambda content, role="assistant": seen_messages.append((content, role))
        )

        await app._cmd_reload()

        assert len(mounted) == 2
        assert len(registered) == 2
        assert footer.model == "openai/gpt-4.1"
        assert footer.cwd == "/srv/project"
        assert seen_messages[-1] == (
            "Reloaded remote session, 2 tui extension(s), 1 prompt(s), 1 skill(s)",
            "tool",
        )

    @pytest.mark.asyncio
    async def test_run_local_creates_new_reasoning_block_after_tool_call(self, monkeypatch):
        from worker_core.agent import AgentEvent, AgentEventType
        from worker_tui.app import WorkerApp

        app = WorkerApp()
        app._session = SimpleNamespace(
            run=lambda text: _events(
                [
                    AgentEvent(type=AgentEventType.REASONING_DELTA, content="pre-tool "),
                    AgentEvent(
                        type=AgentEventType.TOOL_CALL,
                        tool_name="read",
                        tool_args={"path": "x"},
                        tool_call_id="tc1",
                    ),
                    AgentEvent(type=AgentEventType.TOOL_RESULT, content="ok", tool_name="read", tool_call_id="tc1"),
                    AgentEvent(type=AgentEventType.REASONING_DELTA, content="post-tool"),
                    AgentEvent(type=AgentEventType.TEXT_DELTA, content="done"),
                    AgentEvent(type=AgentEventType.DONE),
                ]
            ),
            _estimate_tokens=lambda: 0,
            context_window=0,
        )
        app._input_price = 0.0
        app._output_price = 0.0

        reasoning_blocks: list[str] = []
        assistant_messages: list[str] = []
        started_tool_cards: list[tuple[str, str, str]] = []
        finished_tool_cards: list[tuple[str, str, str, bool]] = []
        log_calls: list[tuple[str, str, str]] = []
        notify_calls: list[tuple[str, str, str | None]] = []

        class _StreamingWidget:
            def __init__(self, sink: list[str]):
                self.sink = sink
                self.sink.append("")

            def append_content(self, delta: str) -> None:
                self.sink[-1] += delta

        class _Footer:
            def update_usage(self, *args, **kwargs) -> None:
                pass

            def update_context_pct(self, *args, **kwargs) -> None:
                pass

        app._add_reasoning_block = (  # type: ignore[method-assign]
            lambda content="": _StreamingWidget(reasoning_blocks)
        )

        def _add_message(content: str, role: str = "assistant"):
            if role == "assistant":
                return _StreamingWidget(assistant_messages)
            assistant_messages.append(content)
            return None

        app._add_message = _add_message  # type: ignore[method-assign]
        app._start_tool_card = (  # type: ignore[method-assign]
            lambda call_id, *, title, body="": started_tool_cards.append((call_id, title, body))
        )
        app._finish_tool_card = (  # type: ignore[method-assign]
            lambda call_id, *, title, body, markdown=False, display=None, kind="text", status_badge="", status_variant="neutral": finished_tool_cards.append((call_id, title, body, markdown))
        )
        app.query_one = lambda selector, _cls=None: _Footer()  # type: ignore[method-assign]
        monkeypatch.setattr(app, "call_after_refresh", lambda callback: None)
        monkeypatch.setattr(app, "_scroll_to_bottom", lambda: None)
        monkeypatch.setattr(app, "_tool_collapsibles", [])

        async def _noop(*args, **kwargs):
            return None

        async def _log(message: str, level: str = "info", source: str = "") -> None:
            log_calls.append((message, level, source))

        async def _notify(
            title: str,
            subtitle: str = "",
            body: str | None = None,
        ) -> None:
            notify_calls.append((title, subtitle, body))

        import worker_tui.app as tui_app

        status_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        progress_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def _status(*args, **kwargs):
            status_calls.append((args, kwargs))

        async def _progress(*args, **kwargs):
            progress_calls.append((args, kwargs))

        monkeypatch.setattr(tui_app.cmux, "set_status", _status)
        monkeypatch.setattr(tui_app.cmux, "notify", _notify)
        monkeypatch.setattr(tui_app.cmux, "set_progress", _progress)
        monkeypatch.setattr(tui_app.cmux, "log", _log)

        await app._run_local.__wrapped__(app, "hello")

        assert reasoning_blocks == ["pre-tool ", "post-tool"]
        assert assistant_messages == ["done"]
        assert started_tool_cards[0][1] == "⚙ read x"
        assert finished_tool_cards[0][1].startswith("✓ read")
        assert started_tool_cards[0][0] == finished_tool_cards[0][0]
        assert ("tool: read", "info", "artel") in log_calls
        assert (("context", "0"), {"icon": "database", "color": "#89dceb"}) in status_calls
        assert progress_calls == []
        assert notify_calls == [("Artel", "Task complete", None)]

    @pytest.mark.asyncio
    async def test_sync_cmux_session_metadata_sets_project_branch_and_model(self, monkeypatch, tmp_path):
        from worker_tui.app import WorkerApp
        import worker_tui.app as tui_app

        app = WorkerApp()
        app._provider_model = "openai/gpt-4.1"

        calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        async def fake_set_status(*args, **kwargs):
            calls.append((args, kwargs))

        class _Result:
            returncode = 0
            stdout = "main\n"

        monkeypatch.setattr(tui_app.cmux, "set_status", fake_set_status)
        monkeypatch.setattr(tui_app.os, "getcwd", lambda: str(tmp_path))
        monkeypatch.setattr(tui_app.subprocess, "run", lambda *args, **kwargs: _Result())

        await app._sync_cmux_session_metadata()

        assert calls == [
            (("project", str(tmp_path)), {"icon": "folder", "color": "#94e2d5"}),
            (("branch", "main"), {"icon": "git-branch", "color": "#cba6f7"}),
            (("model", "openai/gpt-4.1"), {"icon": "cpu", "color": "#89b4fa"}),
        ]

    @pytest.mark.asyncio
    async def test_run_remote_creates_new_reasoning_block_after_tool_call(self, monkeypatch):
        from worker_tui.app import WorkerApp

        class _WebSocket:
            def __init__(self, messages: list[dict[str, object]]):
                self._messages = [json.dumps(message) for message in messages]

            async def send(self, raw: str) -> None:
                pass

            def __aiter__(self):
                self._iter = iter(self._messages)
                return self

            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration as exc:
                    raise StopAsyncIteration from exc

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._ws = _WebSocket(
            [
                {"type": "reasoning_delta", "content": "pre-tool "},
                {"type": "tool_call", "tool": "read", "args": {"path": "x"}, "call_id": "tc1"},
                {"type": "tool_result", "tool": "read", "output": "ok", "call_id": "tc1"},
                {"type": "reasoning_delta", "content": "post-tool"},
                {"type": "text_delta", "content": "done"},
                {"type": "done"},
            ]
        )

        reasoning_blocks: list[str] = []
        assistant_messages: list[str] = []
        started_tool_cards: list[tuple[str, str, str]] = []
        finished_tool_cards: list[tuple[str, str, str, bool]] = []

        class _StreamingWidget:
            def __init__(self, sink: list[str]):
                self.sink = sink
                self.sink.append("")

            def append_content(self, delta: str) -> None:
                self.sink[-1] += delta

        class _Footer:
            def update_usage(self, *args, **kwargs) -> None:
                pass

        app._add_reasoning_block = (  # type: ignore[method-assign]
            lambda content="": _StreamingWidget(reasoning_blocks)
        )

        def _add_message(content: str, role: str = "assistant"):
            if role == "assistant":
                return _StreamingWidget(assistant_messages)
            assistant_messages.append(content)
            return None

        app._add_message = _add_message  # type: ignore[method-assign]
        app._start_tool_card = (  # type: ignore[method-assign]
            lambda call_id, *, title, body="": started_tool_cards.append((call_id, title, body))
        )
        app._finish_tool_card = (  # type: ignore[method-assign]
            lambda call_id, *, title, body, markdown=False, display=None, kind="text", status_badge="", status_variant="neutral": finished_tool_cards.append((call_id, title, body, markdown))
        )
        app.query_one = lambda selector, _cls=None: _Footer()  # type: ignore[method-assign]
        monkeypatch.setattr(app, "call_after_refresh", lambda callback: None)
        monkeypatch.setattr(app, "_scroll_to_bottom", lambda: None)

        await app._run_remote.__wrapped__(app, "hello")

        assert reasoning_blocks == ["pre-tool ", "post-tool"]
        assert assistant_messages == ["done"]
        assert started_tool_cards[0][1] == "⚙ read x"
        assert finished_tool_cards[0][1].startswith("✓ read")
        assert started_tool_cards[0][0] == finished_tool_cards[0][0]

    @pytest.mark.asyncio
    async def test_run_remote_refreshes_board_on_board_event(self, monkeypatch):
        from worker_tui.app import WorkerApp

        class _WebSocket:
            def __init__(self, messages: list[dict[str, object]]):
                self._messages = [json.dumps(message) for message in messages]

            async def send(self, raw: str) -> None:
                pass

            def __aiter__(self):
                self._iter = iter(self._messages)
                return self

            async def __anext__(self):
                try:
                    return next(self._iter)
                except StopIteration as exc:
                    raise StopAsyncIteration from exc

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._ws = _WebSocket(
            [
                {"type": "board_event", "event": "task_added", "payload": {"task_id": 4, "title": "refresh board"}},
                {"type": "done"},
            ]
        )

        loaded: list[str] = []
        app._load_board_state = AsyncMock(side_effect=lambda: loaded.append("loaded"))  # type: ignore[method-assign]
        app._add_message = lambda content, role="assistant": None  # type: ignore[method-assign]
        app._add_tool_message = lambda content: None  # type: ignore[method-assign]
        app.query_one = lambda selector, _cls=None: SimpleNamespace(update_usage=lambda *args, **kwargs: None)  # type: ignore[method-assign]
        monkeypatch.setattr(app, "call_after_refresh", lambda callback: None)
        monkeypatch.setattr(app, "_scroll_to_bottom", lambda: None)

        await app._run_remote.__wrapped__(app, "hello")

        assert loaded == ["loaded"]

    @pytest.mark.asyncio
    async def test_handle_remote_permission_request_sends_selected_decision(self):
        import json

        from worker_tui.app import WorkerApp

        class _WebSocket:
            def __init__(self):
                self.sent: list[dict[str, str]] = []

            async def send(self, raw: str) -> None:
                self.sent.append(json.loads(raw))

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._ws = _WebSocket()
        app._request_permission_decision = AsyncMock(return_value="all")  # type: ignore[method-assign]

        await app._handle_remote_permission_request(
            {
                "request_id": "req-1",
                "tool": "read",
                "args": {"path": "README.md"},
            }
        )

        app._request_permission_decision.assert_awaited_once_with(  # type: ignore[attr-defined]
            "read",
            {"path": "README.md"},
        )
        assert app._auto_approve_all is True
        assert app._ws.sent == [  # type: ignore[union-attr]
            {
                "type": "approve_tool",
                "request_id": "req-1",
                "decision": "all",
            }
        ]

    @pytest.mark.asyncio
    async def test_busy_local_input_is_queued_as_steering(self, monkeypatch):
        from types import SimpleNamespace

        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp()
        steer_calls: list[str] = []
        user_messages: list[tuple[str, str]] = []
        app._session = SimpleNamespace(steer=lambda text: steer_calls.append(text))
        app._run_busy = True
        monkeypatch.setattr(app, "_command_menu_visible", lambda: False)
        monkeypatch.setattr(app, "_hide_command_menu", lambda: None)
        monkeypatch.setattr(app, "call_after_refresh", lambda callback: None)
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": user_messages.append((content, role)))

        event = SimpleNamespace(value="please change approach", input=SimpleNamespace(value="please change approach"))
        await app.on_input_submitted(event)

        assert steer_calls == ["please change approach"]
        assert ("please change approach", "user") in user_messages
        assert ("Steering queued.", "tool") in user_messages

    @pytest.mark.asyncio
    async def test_busy_remote_input_is_sent_as_steering(self, monkeypatch):
        from types import SimpleNamespace

        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        app._run_busy = True
        sent: list[dict[str, object]] = []
        user_messages: list[tuple[str, str]] = []
        monkeypatch.setattr(app, "_command_menu_visible", lambda: False)
        monkeypatch.setattr(app, "_hide_command_menu", lambda: None)
        monkeypatch.setattr(app, "call_after_refresh", lambda callback: None)
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": user_messages.append((content, role)))

        async def _fake_send_remote_event(payload: dict[str, object]) -> None:
            sent.append(payload)

        monkeypatch.setattr(app, "_send_remote_event", _fake_send_remote_event)

        event = SimpleNamespace(value="please change approach", input=SimpleNamespace(value="please change approach"))
        await app.on_input_submitted(event)

        assert sent == [{"type": "steer", "content": "please change approach", "session_id": app._remote_session_id}]
        assert ("Steering queued.", "tool") in user_messages

    @pytest.mark.asyncio
    async def test_image_command_adds_pending_attachment(self, monkeypatch, tmp_path):
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        image_path = tmp_path / "shot.png"
        image_path.write_bytes(b"png-data")

        app = WorkerApp()
        seen_messages: list[tuple[str, str]] = []
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": seen_messages.append((content, role)))

        async def _supports_vision() -> bool:
            return True

        monkeypatch.setattr(app, "_model_supports_vision", _supports_vision)

        await app._cmd_image(str(image_path))

        assert len(app._pending_attachments) == 1
        assert app._pending_attachments[0].path == str(image_path.resolve())
        assert ("Attached image: shot.png", "tool") in seen_messages

    @pytest.mark.asyncio
    async def test_submit_with_attachment_passes_it_to_local_run(self, monkeypatch, tmp_path):
        from types import SimpleNamespace

        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        image_path = tmp_path / "shot.png"
        image_path.write_bytes(b"png-data")

        app = WorkerApp()
        seen_messages: list[tuple[str, str]] = []
        captured: list[tuple[str, int]] = []
        app._store = None
        app._session = SimpleNamespace(session_id="s1")
        app._pending_attachments = [SimpleNamespace(path=str(image_path), mime_type="image/png", name="shot.png")]

        async def _supports_vision() -> bool:
            return True

        monkeypatch.setattr(app, "_model_supports_vision", _supports_vision)
        monkeypatch.setattr(app, "_command_menu_visible", lambda: False)
        monkeypatch.setattr(app, "_hide_command_menu", lambda: None)
        monkeypatch.setattr(app, "call_after_refresh", lambda callback: None)
        monkeypatch.setattr(app, "_clear_composer", lambda: None)
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": seen_messages.append((content, role)))
        monkeypatch.setattr(app, "_run_local", lambda text, attachments=None: captured.append((text, len(attachments or []))))

        event = SimpleNamespace(value="see image", input=SimpleNamespace(value="see image"))
        await app.on_input_submitted(event)

        assert captured == [("see image", 1)]
        assert app._pending_attachments == []
        assert any(role == "user" and "shot.png" in content and "see image" in content for content, role in seen_messages)

    @pytest.mark.asyncio
    async def test_image_clear_removes_pending_attachments(self, monkeypatch, tmp_path):
        from worker_ai.models import ImageAttachment
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        image_path = tmp_path / "shot.png"
        image_path.write_bytes(b"png-data")

        app = WorkerApp()
        seen_messages: list[tuple[str, str]] = []
        app._pending_attachments = [
            ImageAttachment(path=str(image_path), mime_type="image/png", name="shot.png")
        ]
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": seen_messages.append((content, role)))

        app._cmd_image_clear()

        assert app._pending_attachments == []
        assert ("Cleared 1 pending image attachment(s).", "tool") in seen_messages

    @pytest.mark.asyncio
    async def test_image_remove_deletes_selected_attachment(self, monkeypatch, tmp_path):
        from worker_ai.models import ImageAttachment
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        image_a = tmp_path / "a.png"
        image_b = tmp_path / "b.png"
        image_a.write_bytes(b"a")
        image_b.write_bytes(b"b")

        app = WorkerApp()
        seen_messages: list[tuple[str, str]] = []
        app._pending_attachments = [
            ImageAttachment(path=str(image_a), mime_type="image/png", name="a.png"),
            ImageAttachment(path=str(image_b), mime_type="image/png", name="b.png"),
        ]
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": seen_messages.append((content, role)))

        app._cmd_image_remove("1")

        assert [a.name for a in app._pending_attachments] == ["b.png"]
        assert ("Removed pending image: a.png", "tool") in seen_messages

    @pytest.mark.asyncio
    async def test_image_paste_queues_clipboard_attachment(self, monkeypatch, tmp_path):
        from worker_ai.models import ImageAttachment
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        image_path = tmp_path / "clipboard.png"
        image_path.write_bytes(b"png-data")

        app = WorkerApp()
        seen_messages: list[tuple[str, str]] = []

        async def _supports_vision() -> bool:
            return True

        monkeypatch.setattr(app, "_model_supports_vision", _supports_vision)
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": seen_messages.append((content, role)))
        monkeypatch.setattr(
            app,
            "_paste_image_from_clipboard",
            lambda: ImageAttachment(path=str(image_path), mime_type="image/png", name="clipboard.png"),
        )

        await app._cmd_image_paste()

        assert len(app._pending_attachments) == 1
        assert app._pending_attachments[0].name == "clipboard.png"
        assert ("Attached image from clipboard: clipboard.png", "tool") in seen_messages

    @pytest.mark.asyncio
    async def test_image_paste_reports_clipboard_error(self, monkeypatch):
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp()
        seen_messages: list[tuple[str, str]] = []

        async def _supports_vision() -> bool:
            return True

        monkeypatch.setattr(app, "_model_supports_vision", _supports_vision)
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": seen_messages.append((content, role)))

        def _raise() -> object:
            raise RuntimeError("Clipboard image paste is unavailable")

        monkeypatch.setattr(app, "_paste_image_from_clipboard", _raise)

        await app._cmd_image_paste()

        assert ("Clipboard image paste is unavailable", "error") in seen_messages

    @pytest.mark.asyncio
    async def test_pasted_image_path_is_queued_as_attachment(self, monkeypatch, tmp_path):
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        image_path = tmp_path / "drop.png"
        image_path.write_bytes(b"png-data")

        app = WorkerApp()
        seen_messages: list[tuple[str, str]] = []

        async def _supports_vision() -> bool:
            return True

        monkeypatch.setattr(app, "_model_supports_vision", _supports_vision)
        monkeypatch.setattr(app, "_add_message", lambda content, role="assistant": seen_messages.append((content, role)))

        handled = await app._maybe_handle_pasted_image_reference(str(image_path))

        assert handled is True
        assert len(app._pending_attachments) == 1
        assert app._pending_attachments[0].name == "drop.png"
        assert ("Attached pasted image reference(s): drop.png", "tool") in seen_messages


    @pytest.mark.asyncio
    async def test_regular_paste_inserts_text_once(self, monkeypatch):
        from textual import events
        from textual.widgets import TextArea
        from worker_tui.app import WorkerApp

        _patch_tui_test_context(monkeypatch)

        app = WorkerApp(remote_url="ws://localhost:7432")
        async with app.run_test() as pilot:
            await pilot.pause()

            input_bar = app.query_one("#input-bar", TextArea)
            event = events.Paste("hello")
            event._set_forwarded()
            input_bar.post_message(event)
            await pilot.pause()

            assert input_bar.text == "hello"

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
