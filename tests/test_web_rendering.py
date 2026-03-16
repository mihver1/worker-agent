"""Rendering helper tests for Artel Web."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "packages/worker-core/src"))
sys.path.insert(0, str(_REPO_ROOT / "packages/worker-web/src"))


@dataclass(slots=True)
class StubMessage:
    role: str
    content: str
    reasoning: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_result: dict[str, Any] | None = None


@dataclass(slots=True)
class StubSession:
    id: str = "sess-1"
    title: str = ""
    model: str = ""
    project_dir: str = ""
    updated_at: str = "now"
    messages: int = 0
    thinking_level: str = "off"


@dataclass(slots=True)
class StubPrompt:
    name: str
    preview: str


@dataclass(slots=True)
class StubSkill:
    name: str
    description: str


@dataclass(slots=True)
class StubExtensionCommand:
    name: str


@dataclass(slots=True)
class StubInstalledExtension:
    name: str
    version: str = ""
    source: str = ""


@dataclass(slots=True)
class StubBatchUpdateEntry:
    name: str
    ok: bool
    message: str


@dataclass(slots=True)
class StubBatchUpdateResult:
    results: list[StubBatchUpdateEntry]


@dataclass(slots=True)
class StubProvider:
    id: str
    name: str
    status: str
    hint: str


@dataclass(slots=True)
class StubModel:
    id: str
    name: str
    context_window: int
    full_id: str = ""


@dataclass(slots=True)
class StubProviderModels:
    id: str
    name: str
    models: list[StubModel]


@dataclass(slots=True)
class StubServerInfo:
    version: str
    runtime_mode: str
    project_dir: str
    sessions_db: str
    default_model: str
    auth_enabled: bool
    max_sessions: int
    loaded_extensions: int
    provider_overlay_path: str = ""


@dataclass(slots=True)
class StubConfigPaths:
    config_dir: str
    global_config: str
    project_config: str
    sessions_db: str
    provider_overlay: str


@dataclass(slots=True)
class StubDiagnostics:
    active_sessions: int = 0
    loaded_extensions: int = 0
    pending_oauth: int = 0
    permission_requests: int = 0
    auto_approve_sessions: int = 0
    project_dir_exists: bool = False
    global_config_exists: bool = False
    project_config_exists: bool = False
    provider_overlay_exists: bool = False
    sessions_db_exists: bool = False


@dataclass(slots=True)
class StubRawConfig:
    scope: str
    path: str
    exists: bool
    content: str


def test_render_message_markdown_includes_reasoning_and_tools() -> None:
    from worker_web.rendering import render_message_markdown, render_tool_activity_markdown

    message = StubMessage(
        role="assistant",
        content="answer",
        reasoning="thinking",
        tool_calls=[{"name": "read", "arguments": '{"path":"x"}'}],
        tool_result={"content": "file content"},
    )

    rendered = render_message_markdown(message)
    tool_rendered = render_tool_activity_markdown(message)

    assert "ARTEL" in rendered
    assert "thinking" in rendered
    assert "answer" in rendered
    assert "read" in tool_rendered
    assert "file content" in tool_rendered


def test_render_follow_workspace_helpers_surface_task_file_diff_terminal_and_activity() -> None:
    from worker_web.rendering import (
        render_follow_diff_markdown,
        render_follow_file_markdown,
        render_follow_task_markdown,
        render_follow_terminal_markdown,
        render_follow_tool_activity_markdown,
    )

    session = StubSession(
        title="Follow-first workspace",
        model="openai/gpt-4.1",
        project_dir="/srv/project",
        messages=4,
        thinking_level="medium",
    )
    messages = [
        StubMessage(role="user", content="Implement the follow-first workspace in worker-web."),
        StubMessage(
            role="assistant",
            content="Opened the current file.",
            tool_calls=[
                {
                    "name": "read_files",
                    "arguments": '{"files":[{"path":"packages/worker-web/src/worker_web/app.py"}]}',
                }
            ],
            tool_result={"content": "56| with ui.column().classes(...)", "is_error": False},
        ),
        StubMessage(role="user", content="$ uv run pytest tests/test_web_phase8.py"),
        StubMessage(role="tool", content="26 passed in 0.39s"),
    ]

    task_markdown = render_follow_task_markdown(
        session,
        messages,
        default_project_dir="/srv/default",
        default_model="anthropic/claude",
    )
    file_markdown = render_follow_file_markdown(messages)
    diff_markdown = render_follow_diff_markdown(messages)
    terminal_markdown = render_follow_terminal_markdown(messages)
    activity_markdown = render_follow_tool_activity_markdown(messages)

    assert "Follow-first workspace" in task_markdown
    assert "/srv/project" in task_markdown
    assert "openai/gpt-4.1" in task_markdown
    assert "packages/worker-web/src/worker_web/app.py" in file_markdown
    assert "read_files" in file_markdown
    assert "56| with ui.column().classes(...)" in file_markdown
    assert "+ packages/worker-web/src/worker_web/app.py" in diff_markdown
    assert "uv run pytest tests/test_web_phase8.py" in terminal_markdown
    assert "26 passed in 0.39s" in terminal_markdown
    assert "read_files" in activity_markdown
    assert "tool result" in activity_markdown


def test_has_follow_workspace_context_distinguishes_task_first_from_follow_first() -> None:
    from worker_web.rendering import has_follow_workspace_context

    task_first_messages = [
        StubMessage(role="user", content="Plan the worker-web refactor."),
        StubMessage(role="assistant", content="I can outline the next steps."),
    ]
    follow_messages = [
        StubMessage(
            role="assistant",
            content="Opened a file.",
            tool_calls=[{"name": "read_files", "arguments": '{"files":[{"path":"src/app.py"}]}'}],
            tool_result={"content": "1|print('hi')", "is_error": False},
        )
    ]
    terminal_messages = [
        StubMessage(role="user", content="$ pwd"),
        StubMessage(role="tool", content="/srv/project"),
    ]

    assert has_follow_workspace_context(task_first_messages) is False
    assert has_follow_workspace_context(follow_messages) is True
    assert has_follow_workspace_context(terminal_messages) is True
    assert has_follow_workspace_context([], git_snapshot_paths=["src/app.py"]) is True


def test_render_follow_task_markdown_modes() -> None:
    from worker_web.rendering import render_follow_task_markdown

    task_first = render_follow_task_markdown(
        StubSession(
            title="Task-first workspace",
            model="openai/gpt-4.1",
            project_dir="/srv/project",
            messages=2,
        ),
        [
            StubMessage(role="user", content="Refine the workspace UX."),
            StubMessage(role="assistant", content="I can start with the task framing."),
        ],
        default_project_dir="/srv/default",
        default_model="openai/gpt-4.1",
        follow_mode=False,
    )
    follow = render_follow_task_markdown(
        StubSession(
            title="Follow-first workspace",
            model="openai/gpt-4.1",
            project_dir="/srv/project",
            messages=4,
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
        ],
        default_project_dir="/srv/default",
        default_model="openai/gpt-4.1",
        follow_mode=True,
        git_snapshot_paths=["src/app.py", "tests/test_web_phase8.py"],
        command="uv run pytest tests/test_web_phase8.py",
        output="33 passed in 0.41s",
        exit_code=0,
    )

    assert "Task-first mode is active" in task_first
    assert "`! cmd`" in task_first
    assert "`!! cmd`" in task_first
    assert "Workspace evidence" in follow
    assert "current file" in follow
    assert "git focus" in follow
    assert "recent terminal" in follow
    assert "tool trail" in follow


def test_render_follow_file_and_updates() -> None:
    from worker_web.rendering import (
        collect_follow_update_messages,
        render_follow_file_markdown,
        render_follow_update_markdown,
        render_follow_updates_note,
    )

    controller_args = '{"files":[{"path":"packages/worker-web/src/worker_web/controller.py"}]}'
    app_args = '{"files":[{"path":"packages/worker-web/src/worker_web/app.py"}]}'
    file_rendered = render_follow_file_markdown(
        [
            StubMessage(
                role="assistant",
                content="Inspected the controller.",
                tool_calls=[
                    {
                        "name": "read_files",
                        "arguments": controller_args,
                    }
                ],
                tool_result={"content": "615|def render_messages(...)", "is_error": False},
            ),
            StubMessage(
                role="assistant",
                content="Opened the current file.",
                tool_calls=[
                    {
                        "name": "read_files",
                        "arguments": app_args,
                    }
                ],
                tool_result={"content": "105|# Center: task-first entry", "is_error": False},
            ),
        ]
    )
    messages = [
        StubMessage(role="user", content="Inspect the follow-first layout."),
        StubMessage(
            role="assistant",
            content="Opened the current file.",
            tool_calls=[
                {
                    "name": "read_files",
                    "arguments": app_args,
                }
            ],
            tool_result={"content": "105|# Center: task-first entry", "is_error": False},
        ),
        StubMessage(role="user", content="$ uv run pytest tests/test_web_phase8.py"),
        StubMessage(role="tool", content="35 passed in 0.48s"),
    ]
    updates = collect_follow_update_messages(messages)

    assert "working set" in file_rendered
    assert "packages/worker-web/src/worker_web/controller.py" in file_rendered
    assert "packages/worker-web/src/worker_web/app.py" in file_rendered
    assert len(updates) == 3
    assert "Follow-first mode" in render_follow_updates_note(len(updates), limit=4)
    assert "Inspecting files" in render_follow_update_markdown(updates[0])
    assert "Running tests" in render_follow_update_markdown(updates[1])
    assert "Tests passed" in render_follow_update_markdown(updates[2])


def test_render_diff_models_and_admin_helpers() -> None:
    from worker_web.rendering import (
        render_config_paths_markdown,
        render_effective_config_markdown,
        render_extension_batch_update_markdown,
        render_extension_commands_markdown,
        render_follow_diff_markdown,
        render_installed_extensions_markdown,
        render_models_markdown,
        render_prompts_markdown,
        render_providers_markdown,
        render_raw_config_markdown,
        render_server_diagnostics_markdown,
        render_server_info_markdown,
        render_skills_markdown,
    )

    diff_rendered = render_follow_diff_markdown(
        [],
        git_snapshot_loaded=True,
        git_snapshot_command=(
            "git --no-pager diff --stat -- packages/worker-web/src/worker_web/app.py"
        ),
        git_snapshot_output=(
            " M packages/worker-web/src/worker_web/app.py\n1 file changed, 10 insertions(+)"
        ),
        git_snapshot_paths=["packages/worker-web/src/worker_web/app.py"],
    )
    models = render_models_markdown(
        [
            StubProviderModels(
                id="openai",
                name="OpenAI",
                models=[
                    StubModel(
                        id="gpt-4.1",
                        name="GPT-4.1",
                        context_window=128000,
                        full_id="openai/gpt-4.1",
                    )
                ],
            )
        ]
    )
    info = render_server_info_markdown(
        StubServerInfo(
            version="0.1.0",
            runtime_mode="server",
            project_dir="/srv/project",
            sessions_db="/srv/sessions.db",
            default_model="openai/gpt-4.1",
            auth_enabled=True,
            max_sessions=10,
            loaded_extensions=2,
            provider_overlay_path="/srv/overlay.json",
        )
    )
    paths = render_config_paths_markdown(
        StubConfigPaths(
            global_config="/home/me/.config/artel/config.toml",
            project_config="/srv/project/.artel/config.toml",
            sessions_db="/srv/sessions.db",
            provider_overlay="/srv/overlay.json",
            config_dir="/home/me/.config/artel",
        )
    )
    effective = render_effective_config_markdown(
        {"providers": {"openai": {"api_key": "***REDACTED***"}}}
    )
    diagnostics = render_server_diagnostics_markdown(
        StubDiagnostics(active_sessions=1, project_dir_exists=True, sessions_db_exists=True)
    )
    raw = render_raw_config_markdown(
        StubRawConfig(
            scope="project",
            path="/srv/project/.artel/config.toml",
            exists=True,
            content='[agent]\nmodel = "openai/gpt-4.1"',
        )
    )
    prompts = render_prompts_markdown([StubPrompt(name="review", preview="Review code")])
    skills = render_skills_markdown([StubSkill(name="python", description="Use pytest")])
    commands = render_extension_commands_markdown([StubExtensionCommand(name="echo")])
    installed = render_installed_extensions_markdown(
        [
            StubInstalledExtension(
                name="worker-ext-demo", version="1.0.0", source="git+https://example.com/demo.git"
            )
        ]
    )
    updates = render_extension_batch_update_markdown(
        StubBatchUpdateResult(
            results=[StubBatchUpdateEntry(name="worker-ext-demo", ok=True, message="updated")]
        )
    )
    providers = render_providers_markdown(
        [StubProvider(id="openai", name="OpenAI", status="ok", hint="ready")]
    )

    assert "Git workspace snapshot" in diff_rendered
    assert "1 file changed, 10 insertions(+)" in diff_rendered
    assert "openai/gpt-4.1" in models
    assert "128k ctx" in models
    assert "Server info" in info
    assert "/srv/project/.artel/config.toml" in paths
    assert "***REDACTED***" in effective
    assert "active sessions" in diagnostics
    assert "Raw config (project)" in raw
    assert "review" in prompts
    assert "python" in skills
    assert "/echo" in commands
    assert "worker-ext-demo" in installed
    assert "updated" in updates
    assert "OpenAI" in providers
