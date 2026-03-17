"""Unit tests for shared workspace summary extraction."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "packages/artel-core/src"))


@dataclass(slots=True)
class StubSession:
    title: str = ""
    model: str = ""
    project_dir: str = ""
    thinking_level: str = "off"


@dataclass(slots=True)
class StubMessage:
    role: str
    content: str
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_result: dict[str, Any] | None = None


def test_summarize_workspace_captures_follow_context() -> None:
    from artel_core.workspace_summary import summarize_workspace

    summary = summarize_workspace(
        StubSession(
            title="Follow-first workspace",
            model="openai/gpt-4.1",
            project_dir="/srv/project",
            thinking_level="medium",
        ),
        [
            StubMessage(role="user", content="Inspect src/app.py and run tests."),
            StubMessage(
                role="assistant",
                content="Opened the file.",
                tool_calls=[
                    {
                        "name": "read_files",
                        "arguments": '{"files":[{"path":"src/app.py"}]}',
                    }
                ],
                tool_result={"content": "1|print('hi')", "is_error": False},
            ),
            StubMessage(role="user", content="$ uv run pytest tests/test_web_phase8.py"),
            StubMessage(role="tool", content="33 passed in 0.41s"),
        ],
        git_snapshot_loaded=True,
        git_snapshot_command="git --no-pager diff --stat -- src/app.py tests/test_web_phase8.py",
        git_snapshot_output=" M src/app.py\n M tests/test_web_phase8.py\n2 files changed",
        git_snapshot_paths=["src/app.py", "tests/test_web_phase8.py"],
        command="uv run pytest tests/test_web_phase8.py",
        output="33 passed in 0.41s",
        exit_code=0,
        recent_update_limit=4,
    )

    assert summary.task.title == "Follow-first workspace"
    assert summary.task.follow_mode is True
    assert summary.task.project_dir == "/srv/project"
    assert summary.task.model == "openai/gpt-4.1"
    assert summary.task.thinking_level == "medium"
    assert "current file: src/app.py" in summary.task.workspace_evidence
    assert "git focus: src/app.py, tests/test_web_phase8.py" in summary.task.workspace_evidence
    assert (
        "recent terminal: uv run pytest tests/test_web_phase8.py" in summary.task.workspace_evidence
    )
    assert "tool trail: read_files" in summary.task.workspace_evidence

    assert summary.focused_artifact.path == "src/app.py"
    assert summary.focused_artifact.source == "read_files"
    assert summary.focused_artifact.preview == "1|print('hi')"
    assert summary.focused_artifact.working_set == ["src/app.py"]

    assert summary.terminal_context.command == "uv run pytest tests/test_web_phase8.py"
    assert summary.terminal_context.output == "33 passed in 0.41s"
    assert summary.terminal_context.exit_code == 0

    assert summary.diff_snapshot.loaded_from_git is True
    assert summary.diff_snapshot.source_command.startswith("git --no-pager diff --stat")
    assert summary.diff_snapshot.paths == ["src/app.py", "tests/test_web_phase8.py"]
    assert "2 files changed" in summary.diff_snapshot.output

    assert summary.actor_status is not None
    assert summary.actor_status.title == "Tests passed"
    assert summary.recent_updates[-1].actor_status.title == "Tests passed"


def test_summarize_recent_update_classifies_commands_and_file_reads() -> None:
    from artel_core.workspace_summary import summarize_recent_update

    command_update = summarize_recent_update(
        StubMessage(
            role="assistant",
            content="Running the requested checks.",
            tool_calls=[
                {
                    "name": "run_shell_command",
                    "arguments": '{"command":"uv run pytest tests/test_web_phase8.py"}',
                }
            ],
        )
    )
    file_update = summarize_recent_update(
        StubMessage(
            role="assistant",
            content="Opened the current file.",
            tool_calls=[
                {
                    "name": "read_files",
                    "arguments": '{"files":[{"path":"packages/artel-web/src/artel_web/app.py"}]}',
                }
            ],
            tool_result={"content": "105|# Center: task-first entry", "is_error": False},
        )
    )

    assert command_update.actor_status.title == "Running tests"
    assert command_update.command == "uv run pytest tests/test_web_phase8.py"
    assert command_update.actor_status.detail == "uv run pytest tests/test_web_phase8.py"

    assert file_update.actor_status.title == "Inspecting files"
    assert file_update.tool_names == ["read_files"]
    assert file_update.tool_paths == ["packages/artel-web/src/artel_web/app.py"]


def test_follow_workspace_helpers_delegate_to_shared_logic() -> None:
    from artel_core.workspace_summary import (
        collect_follow_file_paths,
        collect_follow_update_messages,
        format_code_item_preview,
        has_follow_workspace_context,
        render_follow_updates_note,
        summarize_tool_activity,
    )

    messages = [
        StubMessage(role="user", content="Refine the workspace UX."),
        StubMessage(
            role="assistant",
            content="Opened the current file.",
            tool_calls=[
                {
                    "name": "read_files",
                    "arguments": '{"files":[{"path":"packages/artel-web/src/artel_web/app.py"}]}',
                }
            ],
            tool_result={"content": "105|# Center: task-first entry", "is_error": False},
        ),
        StubMessage(role="user", content="$ uv run pytest tests/test_web_phase8.py"),
        StubMessage(role="tool", content="35 passed in 0.48s"),
    ]

    updates = collect_follow_update_messages(messages)
    activity = summarize_tool_activity(messages[1])

    assert collect_follow_file_paths(messages) == ["packages/artel-web/src/artel_web/app.py"]
    assert len(updates) == 3
    assert render_follow_updates_note(len(updates), limit=4).startswith("Follow-first mode")
    assert has_follow_workspace_context(messages) is True
    assert format_code_item_preview(
        [
            "packages/artel-web/src/artel_web/app.py",
            "packages/artel-web/src/artel_web/controller.py",
            "packages/artel-web/src/artel_web/state.py",
        ],
        limit=2,
    ) == (
        "`packages/artel-web/src/artel_web/app.py`, "
        "`packages/artel-web/src/artel_web/controller.py` (+1 more)"
    )
    assert activity.calls[0].name == "read_files"
    assert activity.calls[0].paths == ["packages/artel-web/src/artel_web/app.py"]
    assert activity.result_content == "105|# Center: task-first entry"
