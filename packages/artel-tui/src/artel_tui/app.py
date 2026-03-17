"""Artel TUI — Textual application."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import os
import shlex
import subprocess
import tempfile
import urllib.parse
import uuid
import webbrowser
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from functools import lru_cache
from itertools import groupby
from pathlib import Path
from types import MethodType
from typing import Any

import artel_core.cmux as cmux
from artel_ai.attachments import is_supported_image_path, normalize_image_attachment
from artel_ai.models import ImageAttachment, Role
from artel_core.agent import AgentEventType, AgentSession
from artel_core.board import (
    add_task_to_markdown,
    operator_notes_path,
    read_project_board_file,
    render_numbered_text,
    tasks_path,
    update_task_in_markdown,
    write_project_board_file,
)
from artel_core.bootstrap import (
    bootstrap_runtime,
    create_agent_session_from_bootstrap,
    provider_requires_api_key,
)
from artel_core.cmux import is_cmux
from artel_core.config import load_config, resolve_model
from artel_core.extensions import (
    ExtensionContext,
    load_tui_extensions_async,
    reload_tui_extensions_async,
)
from artel_core.git_surface import (
    render_git_diff,
    render_git_help,
    render_git_status,
    restore_all,
    restore_path,
    restore_paths,
)
from artel_core.prompts import load_prompts, render_prompt
from artel_core.provider_resolver import (
    get_effective_model_info,
    get_effective_provider_catalog,
    get_provider_config,
    get_provider_env_vars,
)
from artel_core.rules import (
    SessionRuleOverrides,
    add_rule,
    clear_session_rule_overrides,
    delete_rule,
    effective_rule_state,
    get_rule,
    list_rules,
    move_rule,
    reset_rule_for_session,
    set_rule_enabled_for_session,
    update_rule,
)
from artel_core.session_rewind import collect_last_ai_changed_paths
from artel_core.sessions import SessionStore
from artel_core.skills import inject_skill, load_skills
from artel_core.tool_display import format_tool_call_display, format_tool_result_display
from artel_core.tools.builtins import create_builtin_tools
from pygments import lex
from pygments.lexers import TextLexer, get_lexer_for_filename, guess_lexer, guess_lexer_for_filename
from pygments.styles import get_style_by_name
from pygments.util import ClassNotFound
from rich.markdown import Markdown
from rich.style import Style
from rich.table import Table
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Header,
    Input,
    OptionList,
    Static,
    TextArea,
    Tree,
)
from textual.widgets import (
    Markdown as MarkdownWidget,
)
from textual.widgets.option_list import Option

from artel_tui.credential_forwarding import collect_forward_credentials
from artel_tui.local_server import restart_managed_local_server
from artel_tui.remote_control import RemoteControlClient
from artel_tui.server_registry import (
    SavedArtelServer,
    default_server_name,
    load_saved_servers,
    remove_saved_server,
    upsert_saved_server,
)

_RU_QWERTY_KEY_ALIASES: dict[str, str] = {
    "q": "й",
    "w": "ц",
    "e": "у",
    "r": "к",
    "t": "е",
    "y": "н",
    "u": "г",
    "i": "ш",
    "o": "щ",
    "p": "з",
    "[": "х",
    "]": "ъ",
    "a": "ф",
    "s": "ы",
    "d": "в",
    "f": "а",
    "g": "п",
    "h": "р",
    "j": "о",
    "k": "л",
    "l": "д",
    ";": "ж",
    "'": "э",
    "z": "я",
    "x": "ч",
    "c": "с",
    "v": "м",
    "b": "и",
    "n": "т",
    "m": "ь",
    ",": "б",
    ".": "ю",
}

_DIFF_SYNTAX_THEME = "ansi_dark"
_DIFF_PYGMENTS_STYLE = "monokai"
_MAX_PASTED_IMAGE_REFERENCE_CHARS = 512


def _layout_safe_binding_variants(key: str) -> list[str]:
    """Return layout aliases for a Textual key string when possible."""

    normalized = str(key or "").strip().lower()
    if not normalized:
        return []
    parts = normalized.split("+")
    base = parts[-1]
    alias = _RU_QWERTY_KEY_ALIASES.get(base)
    if not alias:
        return []
    parts[-1] = alias
    variant = "+".join(parts)
    if variant == normalized:
        return []
    return [variant]


def _diff_metadata_style(line: str) -> str:
    if line.startswith("+++"):
        return "bold green"
    if line.startswith("---"):
        return "bold red"
    if line.startswith("@@"):
        return "bold cyan"
    if line.startswith("diff --git"):
        return "bold magenta"
    if line.startswith("index "):
        return "dim"
    return "dim"


def _diff_prefix_style(prefix: str) -> str:
    if prefix == "+":
        return "bold green"
    if prefix == "-":
        return "bold red"
    return "dim"


def _resolve_diff_lexer(path: str, diff_text: str) -> Any:
    sample_lines: list[str] = []
    for line in diff_text.splitlines():
        if line == "…" or line.startswith(("diff --git", "index ", "@@", "---", "+++")):
            continue
        if line and line[0] in {"+", "-", " "}:
            sample_lines.append(line[1:])
        else:
            sample_lines.append(line)
        if len(sample_lines) >= 40:
            break

    sample = "\n".join(sample_lines).strip()
    normalized_path = str(path or "").strip()

    if normalized_path and sample:
        with suppress(ClassNotFound):
            return guess_lexer_for_filename(normalized_path, sample)
    if normalized_path:
        with suppress(ClassNotFound):
            return get_lexer_for_filename(normalized_path)
    if sample:
        with suppress(ClassNotFound):
            return guess_lexer(sample)
    return TextLexer()


@lru_cache(maxsize=16)
def _pygments_style(theme_name: str) -> Any:
    return get_style_by_name(theme_name)


@lru_cache(maxsize=512)
def _rich_style_for_token(theme_name: str, token_type: Any) -> Style | None:
    spec = _pygments_style(theme_name).style_for_token(token_type)
    if not spec:
        return None
    return Style(
        color=f"#{spec['color']}" if spec.get("color") else None,
        bold=bool(spec.get("bold", False)),
        italic=bool(spec.get("italic", False)),
        underline=bool(spec.get("underline", False)),
    )


def _highlight_diff_code_text(code: str, lexer: Any) -> Text:
    highlighted = Text()
    source = code or " "
    try:
        for token_type, value in lex(source, lexer):
            if not value:
                continue
            if value.endswith("\n"):
                value = value[:-1]
                if not value:
                    continue
            highlighted.append(value, style=_rich_style_for_token(_DIFF_PYGMENTS_STYLE, token_type))
    except Exception:
        highlighted = Text(source.rstrip("\n") or " ")
    return highlighted if highlighted.plain else Text(" ")


def _build_syntax_highlighted_diff(path: str, diff_text: str) -> Table:
    lexer = _resolve_diff_lexer(path, diff_text)
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(width=1, no_wrap=True)
    table.add_column(ratio=1)

    for line in diff_text.splitlines() or [""]:
        if not line:
            table.add_row(Text(" "), Text(""))
            continue
        if line == "…" or line.startswith(("diff --git", "index ", "@@", "---", "+++")):
            table.add_row(Text(" "), Text(line, style=_diff_metadata_style(line)))
            continue
        prefix = line[0]
        if prefix in {"+", "-", " "}:
            table.add_row(
                Text(prefix, style=_diff_prefix_style(prefix)),
                _highlight_diff_code_text(line[1:] or " ", lexer),
            )
            continue
        table.add_row(Text(" "), Text(line))

    return table


class ComposerTextArea(TextArea):
    """Composer textarea with chat-style submit/newline shortcuts."""

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.app.call_next(self.app.action_submit_composer)
            return
        if event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)

    async def _on_paste(self, event: events.Paste) -> None:
        handled = await self.app._maybe_handle_pasted_image_reference(event.text)
        if handled:
            event.stop()
            event.prevent_default()
            return
        # Textual will continue dispatching default handlers up the MRO after this
        # override returns. Prevent the implicit base-class dispatch before calling
        # TextArea's paste handler manually so regular text paste is applied once.
        event.prevent_default()
        await super()._on_paste(event)


@dataclass(frozen=True, slots=True)
class ProviderSetupEntry:
    """A provider entry shown by the /providers command."""

    id: str
    name: str
    status: str
    hint: str


def _provider_ids_for_listing(config: Any) -> list[str]:
    from artel_ai.provider_specs import iter_provider_specs

    provider_ids = [spec.id for spec in iter_provider_specs()]
    for provider_id in config.providers:
        if provider_id not in provider_ids:
            provider_ids.append(provider_id)
    return provider_ids


def _looks_local_base_url(base_url: str) -> bool:
    return base_url.startswith("http://localhost") or base_url.startswith("http://127.0.0.1")


def _provider_setup_hint(
    provider_id: str,
    *,
    env_vars: tuple[str, ...],
    oauth_supported: bool,
    requires_api_key: bool,
    base_url: str,
) -> str:
    config_path = f"[providers.{provider_id}]"
    if oauth_supported and env_vars:
        return f"run /connect {provider_id} or set {env_vars[0]}"
    if oauth_supported:
        return f"run /connect {provider_id}"
    if not requires_api_key:
        if provider_id == "bedrock":
            return f"configure AWS credentials or {config_path}"
        if provider_id in {"google_vertex", "vertex_anthropic"}:
            return f"set {config_path}.project / .location or use ADC"
        if _looks_local_base_url(base_url) or provider_id in {"ollama", "lmstudio", "llama.cpp"}:
            return f"start the service or set {config_path}.base_url"
        return f"configure {config_path}"
    if env_vars:
        return f"set {env_vars[0]} or {config_path}.api_key"
    return f"configure {config_path}"


def _provider_setup_hint_for_config(config: Any, provider_id: str) -> str:
    from artel_ai.oauth import list_oauth_provider_names
    from artel_ai.provider_specs import get_provider_spec

    spec = get_provider_spec(provider_id)
    canonical_id = spec.id if spec is not None else provider_id
    provider_config = get_provider_config(config, provider_id)
    runtime_base_url = (
        provider_config.base_url
        if provider_config and provider_config.base_url
        else (spec.default_base_url if spec is not None else "")
    )
    oauth_supported = canonical_id in set(list_oauth_provider_names())
    return _provider_setup_hint(
        canonical_id,
        env_vars=tuple(get_provider_env_vars(config, provider_id)),
        oauth_supported=oauth_supported,
        requires_api_key=provider_requires_api_key(config, provider_id),
        base_url=runtime_base_url,
    )


async def collect_provider_setup_entries(
    config: Any,
    resolve_api_key: Callable[[Any, str], Awaitable[tuple[str | None, str]]],
) -> list[ProviderSetupEntry]:
    from artel_ai.oauth import list_oauth_provider_names
    from artel_ai.provider_specs import get_provider_spec

    oauth_providers = set(list_oauth_provider_names())
    entries: list[ProviderSetupEntry] = []
    for provider_id in _provider_ids_for_listing(config):
        provider_config = get_provider_config(config, provider_id)
        spec = get_provider_spec(provider_id)
        canonical_id = spec.id if spec is not None else provider_id
        display_name = (
            provider_config.name
            if provider_config and provider_config.name
            else (spec.display_name if spec is not None else provider_id)
        )
        env_vars = tuple(get_provider_env_vars(config, provider_id))
        requires_key = provider_requires_api_key(config, provider_id)
        runtime_base_url = (
            provider_config.base_url
            if provider_config and provider_config.base_url
            else (spec.default_base_url if spec is not None else "")
        )
        api_key, auth_type = await resolve_api_key(config, provider_id)

        if api_key:
            status = "connected (oauth)" if auth_type == "oauth" else "configured"
            hint = "use /models"
        elif provider_config is not None and requires_key:
            status = "partially configured"
            hint = _provider_setup_hint(
                canonical_id,
                env_vars=env_vars,
                oauth_supported=canonical_id in oauth_providers,
                requires_api_key=requires_key,
                base_url=runtime_base_url,
            )
        elif not requires_key:
            status = "keyless"
            hint = _provider_setup_hint(
                canonical_id,
                env_vars=env_vars,
                oauth_supported=canonical_id in oauth_providers,
                requires_api_key=requires_key,
                base_url=runtime_base_url,
            )
        else:
            status = "needs setup"
            hint = _provider_setup_hint(
                canonical_id,
                env_vars=env_vars,
                oauth_supported=canonical_id in oauth_providers,
                requires_api_key=requires_key,
                base_url=runtime_base_url,
            )

        entries.append(
            ProviderSetupEntry(
                id=canonical_id,
                name=display_name,
                status=status,
                hint=hint,
            )
        )
    return entries


def format_provider_setup_entries(entries: list[ProviderSetupEntry]) -> str:
    if not entries:
        return "No supported providers found."

    lines = ["Supported providers:"]
    for entry in entries:
        lines.append(f"  {entry.id} ({entry.name}) — {entry.status}; {entry.hint}")
    lines.append("")
    lines.append("Use /models to browse models after a provider is configured.")
    return "\n".join(lines)


# ── Widgets ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SlashCommandSuggestion:
    """A slash command entry shown in the autocomplete dropdown."""

    value: str
    description: str
    completion: str = ""
    search_text: str = ""
    current: bool = False


@dataclass(slots=True)
class PendingPermissionRequest:
    """A permission request waiting for an inline user decision."""

    tool_name: str
    tool_args: dict[str, Any]
    future: asyncio.Future[str]


@dataclass(frozen=True, slots=True)
class ServerDockNodeData:
    """Typed metadata stored in server dock tree nodes."""

    kind: str
    remote_url: str = ""
    auth_token: str = ""
    session_id: str = ""
    project_dir: str = ""
    name: str = ""


BUILTIN_COMMAND_SUGGESTIONS: tuple[SlashCommandSuggestion, ...] = (
    SlashCommandSuggestion("/help", "show available commands"),
    SlashCommandSuggestion("/rules", "list configured rules"),
    SlashCommandSuggestion("/rule", "manage rules"),
    SlashCommandSuggestion("/rule add", "add a rule via inline editor"),
    SlashCommandSuggestion("/rule edit", "edit a rule via inline editor"),
    SlashCommandSuggestion("/rule delete", "delete a rule"),
    SlashCommandSuggestion("/rule enable", "enable a rule for this session"),
    SlashCommandSuggestion("/rule disable", "disable a rule for this session"),
    SlashCommandSuggestion("/rule persist", "change persisted rule state"),
    SlashCommandSuggestion("/rule move", "move a rule to change precedence"),
    SlashCommandSuggestion("/rule reset", "reset session rule override"),
    SlashCommandSuggestion("/model", "show current model or switch model"),
    SlashCommandSuggestion("/models", "list available models"),
    SlashCommandSuggestion("/project", "show or change the active project"),
    SlashCommandSuggestion("/cd", "alias for /project"),
    SlashCommandSuggestion("/providers", "list supported providers and setup hints"),
    SlashCommandSuggestion("/connect", "log in to a provider"),
    SlashCommandSuggestion("/resume", "resume a saved session"),
    SlashCommandSuggestion("/sessions", "list recent sessions"),
    SlashCommandSuggestion("/compact", "compact conversation history"),
    SlashCommandSuggestion("/name", "rename the current session"),
    SlashCommandSuggestion("/tree", "show the session message tree"),
    SlashCommandSuggestion("/fork", "fork from a message index"),
    SlashCommandSuggestion("/prompts", "list prompt templates"),
    SlashCommandSuggestion("/skill:", "load a skill into the session"),
    SlashCommandSuggestion("/skills", "list available skills"),
    SlashCommandSuggestion("/thinking", "set the thinking level"),
    SlashCommandSuggestion("/theme", "switch the active theme"),
    SlashCommandSuggestion("/export", "export the session to HTML"),
    SlashCommandSuggestion("/reload", "reload extensions, prompts, and skills"),
    SlashCommandSuggestion("/image", "attach an image file to the next message"),
    SlashCommandSuggestion("/image-paste", "paste an image from the clipboard"),
    SlashCommandSuggestion("/image-clear", "clear pending image attachments"),
    SlashCommandSuggestion("/image-remove", "remove a pending image attachment by index"),
    SlashCommandSuggestion("/copy", "copy the last assistant message"),
    SlashCommandSuggestion("/server-add", "save an Artel server in the left dock"),
    SlashCommandSuggestion("/server-remove", "remove a saved Artel server from the left dock"),
    SlashCommandSuggestion("/server-select", "connect to a saved Artel server by name or URL"),
    SlashCommandSuggestion("/server-dock", "toggle the left server/project/session dock"),
    SlashCommandSuggestion("/delegates", "show orchestrated delegated runs in the current window"),
    SlashCommandSuggestion("/agents", "legacy alias for /delegates"),
    SlashCommandSuggestion("/mcp", "show MCP config sources, connections, and errors"),
    SlashCommandSuggestion(
        "/schedules", "inspect and control scheduled tasks on the active server"
    ),
    SlashCommandSuggestion("/wt", "manage git worktrees for the current repository"),
    SlashCommandSuggestion("/tasks", "show the shared task board"),
    SlashCommandSuggestion("/task-add", "add a task to the shared task board"),
    SlashCommandSuggestion("/task-done", "mark a task as done"),
    SlashCommandSuggestion("/notes", "show operator notes"),
    SlashCommandSuggestion("/notes-open", "focus the operator notes editor"),
    SlashCommandSuggestion("/cancel", "cancel the active run"),
    SlashCommandSuggestion("/server-restart", "restart the managed local Artel server"),
    SlashCommandSuggestion("/split", "open a cmux split pane"),
    SlashCommandSuggestion("/browser", "open a cmux browser pane"),
    SlashCommandSuggestion("/new", "start a new session in the current window"),
    SlashCommandSuggestion("/clear", "clear chat and start a new session"),
    SlashCommandSuggestion("/quit", "exit the TUI"),
)


class PendingAttachmentsBar(Static):
    """Inline composer attachment list."""

    DEFAULT_CSS = """
    PendingAttachmentsBar {
        display: none;
        margin: 0 1;
        padding: 0 1;
        background: $surface;
        color: $text-muted;
    }
    PendingAttachmentsBar.visible {
        display: block;
    }
    """

    def set_attachments(self, attachments: list[ImageAttachment]) -> None:
        if attachments:
            lines: list[str] = []
            for index, attachment in enumerate(attachments, start=1):
                name = attachment.name or Path(attachment.path).name
                size = ""
                try:
                    bytes_size = Path(attachment.path).stat().st_size
                    if bytes_size >= 1024 * 1024:
                        size = f" — {bytes_size / (1024 * 1024):.1f} MB"
                    elif bytes_size >= 1024:
                        size = f" — {bytes_size / 1024:.1f} KB"
                    else:
                        size = f" — {bytes_size} B"
                except Exception:
                    pass
                mime = f" ({attachment.mime_type})" if attachment.mime_type else ""
                lines.append(f"📎 [{index}] {name}{mime}{size}")
            self.update("\n".join(lines))
            self.add_class("visible")
        else:
            self.update("")
            self.remove_class("visible")


class ServerDockTree(Tree[ServerDockNodeData]):
    """Tree with a click target for per-node actions rendered as leading ⋮."""

    ACTION_GUTTER_WIDTH = 4

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._suppress_next_select = False

    def action_select_cursor(self) -> None:
        if self._suppress_next_select:
            self._suppress_next_select = False
            return
        super().action_select_cursor()

    def render_label(self, node: Any, base_style: Style, style: Style) -> Text:
        label = super().render_label(node, base_style, style)
        prefix = Text("⋮ ", style=style + Style(dim=True))
        prefix.stylize(Style(meta={"dock_action": True, "node": node._id}))
        return Text.assemble(prefix, label)

    async def _on_click(self, event: events.Click) -> None:
        line = int(event.y) - self.gutter.top + int(self.scroll_offset.y)
        if 0 <= line < len(self._tree_lines):
            tree_line = self._tree_lines[line]
            depth = max(0, len(tree_line.path) - 1)
            action_x_start = self.gutter.left + max(2, self.guide_depth) * depth
            relative_x = int(event.x)
            if action_x_start <= relative_x < action_x_start + self.ACTION_GUTTER_WIDTH:
                node = self.get_node_at_line(line)
                if node is not None and isinstance(node.data, ServerDockNodeData):
                    self._suppress_next_select = True
                    self.cursor_line = line
                    self.post_message(ServerDockActionRequested(node))
                    event.stop()
                    return
        await super()._on_click(event)


class ServerDockActionRequested(events.Message):
    """Posted when user clicks the leading ⋮ action gutter for a dock node."""

    def __init__(self, node: Any) -> None:
        super().__init__()
        self.node = node


class DockActionInvoked(events.Message):
    """Posted when the inline dock action panel requests an action."""

    def __init__(self, action: str) -> None:
        super().__init__()
        self.action = action


class DockActionConfirmed(events.Message):
    """Posted when the inline dock action panel confirms a destructive action."""

    def __init__(self, action: str) -> None:
        super().__init__()
        self.action = action


class DockActionClosed(events.Message):
    """Posted when the inline dock action panel is closed."""


class DockInputSubmitted(events.Message):
    """Posted when the inline dock input panel submits a value."""

    def __init__(self, mode: str, value: str) -> None:
        super().__init__()
        self.mode = mode
        self.value = value


class DockInputClosed(events.Message):
    """Posted when the inline dock input panel is closed."""

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode


class InlineInputSubmitted(events.Message):
    """Posted when the main inline input panel submits a value."""

    def __init__(self, mode: str, value: str) -> None:
        super().__init__()
        self.mode = mode
        self.value = value


class InlineInputClosed(events.Message):
    """Posted when the main inline input panel is closed."""

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode


class RuleEditorSubmitted(events.Message):
    """Posted when the inline rule editor submits a payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__()
        self.payload = payload


class RuleEditorClosed(events.Message):
    """Posted when the inline rule editor is closed."""


class ServerDockSidebar(Static):
    """Left dock with saved Artel servers grouped into projects and sessions."""

    DEFAULT_CSS = """
    ServerDockSidebar {
        width: 36;
        min-width: 28;
        max-width: 52;
        height: 1fr;
        margin: 0 0 0 1;
        padding: 0 1;
        background: $surface;
        border-right: tall $primary 30%;
    }
    ServerDockSidebar.hidden {
        display: none;
    }
    #server-dock-title {
        text-style: bold;
        margin-top: 1;
    }
    #server-dock-help {
        color: $text-muted;
        margin-bottom: 1;
    }
    #server-dock-actions {
        height: 3;
        margin-bottom: 1;
    }
    #server-dock-actions Button {
        margin-right: 1;
        min-width: 8;
    }
    #server-dock-tree {
        height: 1fr;
        min-height: 12;
        border: round $primary 20%;
        padding: 0 1;
    }
    #server-dock-status {
        color: $text-muted;
        margin: 1 0 0 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Servers", id="server-dock-title")
        yield Static(
            "Saved Artel servers grouped as server → project → session",
            id="server-dock-help",
        )
        with Horizontal(id="server-dock-actions"):
            yield Button("Add", id="server-dock-add", variant="primary")
            yield Button("Refresh", id="server-dock-refresh")
            yield Button("Hide", id="server-dock-hide")
        yield ServerDockTree("Servers", id="server-dock-tree")
        yield DockInputPanel(id="server-dock-input-panel")
        yield DockActionPanel(id="server-dock-action-panel")
        yield Static("", id="server-dock-status")

    def on_mount(self) -> None:
        tree = self.query_one("#server-dock-tree", Tree)
        tree.show_root = False
        tree.root.expand()

    def set_visible(self, visible: bool) -> None:
        if visible:
            self.remove_class("hidden")
        else:
            self.add_class("hidden")

    def tree(self) -> Tree[ServerDockNodeData]:
        return self.query_one("#server-dock-tree", Tree)

    def set_status(self, text: str) -> None:
        self.query_one("#server-dock-status", Static).update(text)

    def input_panel(self) -> DockInputPanel:
        return self.query_one("#server-dock-input-panel", DockInputPanel)

    def action_panel(self) -> DockActionPanel:
        return self.query_one("#server-dock-action-panel", DockActionPanel)


class BoardSidebar(Static):
    """Right sidebar with tasks and operator notes."""

    DEFAULT_CSS = """
    BoardSidebar {
        display: none;
        width: 36;
        min-width: 28;
        max-width: 48;
        height: 1fr;
        margin: 0 1 0 0;
        padding: 0 1;
        background: $surface;
        border-left: tall $primary 30%;
    }
    BoardSidebar.visible {
        display: block;
    }
    #board-title, #notes-title {
        text-style: bold;
        margin-top: 1;
    }
    #board-help, #notes-help {
        color: $text-muted;
        margin-bottom: 1;
    }
    #tasks-editor {
        height: 1fr;
        min-height: 8;
    }
    #notes-editor {
        height: 8;
        min-height: 5;
    }
    #board-status {
        color: $text-muted;
        margin: 1 0 0 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Tasks", id="board-title")
        yield Static("Shared project work board for you and the agent", id="board-help")
        yield TextArea("", id="tasks-editor")
        yield Static("Operator Notes", id="notes-title")
        yield Static("Private scratchpad; agent reads only on request", id="notes-help")
        yield TextArea("", id="notes-editor")
        yield Static("", id="board-status")

    def set_visible(self, visible: bool) -> None:
        if visible:
            self.add_class("visible")
        else:
            self.remove_class("visible")

    def set_tasks(self, content: str) -> None:
        self.query_one("#tasks-editor", TextArea).load_text(content)

    def set_notes(self, content: str) -> None:
        self.query_one("#notes-editor", TextArea).load_text(content)

    def tasks_text(self) -> str:
        return self.query_one("#tasks-editor", TextArea).text

    def notes_text(self) -> str:
        return self.query_one("#notes-editor", TextArea).text

    def focus_tasks(self) -> None:
        self.query_one("#tasks-editor", TextArea).focus()

    def focus_notes(self) -> None:
        self.query_one("#notes-editor", TextArea).focus()

    def set_status(self, text: str) -> None:
        self.query_one("#board-status", Static).update(text)


class MessageWidget(Static):
    """A single message bubble in the chat."""

    DEFAULT_CSS = """
    MessageWidget {
        margin: 0 1;
        padding: 0 1;
    }
    MessageWidget > Markdown {
        background: transparent;
        margin: 0;
        padding: 0;
    }
    .user-message {
        background: $primary-background;
        color: $text;
        border-left: thick $primary;
    }
    .assistant-message {
        background: $surface;
        color: $text;
    }
    .reasoning-message {
        background: $surface;
        color: $text;
    }
    .tool-message {
        background: $surface;
        color: $text-muted;
        text-style: italic;
    }
    .tool-message-title {
        text-style: bold;
        color: $text;
        margin-bottom: 1;
    }
    .tool-message-body {
        color: $text;
    }
    .tool-message-result-row {
        margin-top: 1;
        height: auto;
    }
    .tool-message-result-title {
        color: $success;
        text-style: bold;
    }
    .tool-message-badge {
        color: $warning;
        text-style: bold;
    }
    .tool-message-badge-success {
        color: $success;
        text-style: bold;
    }
    .tool-message-badge-error {
        color: $error;
        text-style: bold;
    }
    .tool-diff-stats {
        color: $text-muted;
        margin-bottom: 1;
    }
    .tool-diff-body {
        color: $text;
    }
    .error-message {
        background: $error 20%;
        color: $error;
    }
    """

    def __init__(self, content: str, role: str = "assistant", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.role = role
        self._content = content
        self._markdown: MarkdownWidget | None = None
        self._markdown_stream: Any | None = None
        self._scroll_callback: Callable[[], None] | None = None
        self._scroll_timer: Timer | None = None
        self.add_class(f"{role}-message")

    def compose(self) -> ComposeResult:
        if self.role in {"assistant", "reasoning"}:
            self._markdown = MarkdownWidget(self._content)
            yield self._markdown

    def render(self) -> Markdown | Text | str:
        if self.role in {"assistant", "reasoning"}:
            return ""
        if self.role == "user":
            return Text(f"❯ {self._content}")
        if self.role == "tool":
            return Text(self._content)
        if self.role == "error":
            return Text(f"✗ {self._content}")
        return Text(self._content)

    @property
    def content(self) -> str:
        return self._content

    def set_scroll_callback(self, callback: Callable[[], None] | None) -> None:
        self._scroll_callback = callback

    async def on_mount(self) -> None:
        if self._markdown is not None:
            self._markdown_stream = MarkdownWidget.get_stream(self._markdown)

    async def on_unmount(self, event: events.Unmount) -> None:
        del event
        await self._stop_markdown_stream()

    async def _stop_markdown_stream(self) -> None:
        stream = self._markdown_stream
        self._markdown_stream = None
        if stream is not None:
            with contextlib.suppress(Exception):
                await stream.stop()

    def _schedule_scroll(self) -> None:
        if self._scroll_callback is None:
            return
        if self._scroll_timer is not None:
            self._scroll_timer.stop()
        self._scroll_timer = self.set_timer(0.016, self._run_scheduled_scroll)

    def _run_scheduled_scroll(self) -> None:
        self._scroll_timer = None
        if self._scroll_callback is not None:
            self._scroll_callback()

    def _schedule_background_task(
        self,
        task: Any,
        *,
        exclusive: bool = False,
        thread: bool = False,
    ) -> None:
        getattr(self, "run_" + "work" + "er")(task, exclusive=exclusive, thread=thread)

    def append_content(self, delta: str) -> None:
        if not delta:
            return
        self._content += delta
        if self._markdown is not None:
            if self._markdown_stream is not None:
                self._schedule_background_task(
                    self._markdown_stream.write(delta),
                    exclusive=False,
                    thread=False,
                )
            else:
                self._markdown.append(delta)
            self._schedule_scroll()
        else:
            self.refresh(layout=True)


class DiffWidget(Static):
    """Compact diff widget with stat header and diff body."""

    def __init__(
        self,
        path: str,
        stats: str,
        diff_text: str,
        *,
        show_header: bool = True,
        show_stats: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._path = path
        self._stats = stats
        self._diff_text = diff_text
        self._show_header = show_header
        self._show_stats = show_stats
        if self._show_header:
            self.add_class("tool-message")

    def compose(self) -> ComposeResult:
        if self._show_header:
            yield Static(self._path, classes="tool-message-title", markup=False)
        if self._show_stats and self._stats:
            yield Static(self._stats, classes="tool-diff-stats", markup=False)
        if self._diff_text:
            yield Static(
                _build_syntax_highlighted_diff(self._path, self._diff_text),
                classes="tool-diff-body",
            )


class ToolCard(Static):
    """Single card representing a tool call and its eventual result."""

    DEFAULT_CSS = """
    ToolCard {
        height: auto;
    }
    ToolCard > .tool-message-result-row {
        height: auto;
    }
    .tool-card-scroll {
        height: auto;
        max-height: 16;
        margin-top: 1;
    }
    .tool-card-scroll > Markdown {
        background: transparent;
        margin: 0;
        padding: 0;
    }
    .tool-card-scroll > .tool-message-body,
    .tool-card-scroll > DiffWidget,
    .tool-card-scroll > .tool-diff-body {
        margin-top: 0;
    }
    """

    def __init__(
        self,
        call_title: str,
        call_body: str = "",
        *,
        result_title: str = "",
        result_body: str = "",
        result_markdown: bool = False,
        result_display: dict[str, Any] | None = None,
        result_kind: str = "text",
        result_status_badge: str = "",
        result_status_variant: str = "neutral",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._call_title = call_title
        self._call_body = call_body
        self._result_title = result_title
        self._result_body = result_body
        self._result_markdown = result_markdown
        self._result_display = result_display
        self._result_kind = result_kind
        self._result_status_badge = result_status_badge
        self._result_status_variant = result_status_variant
        self.add_class("tool-message")

    def set_result(
        self,
        *,
        title: str,
        body: str,
        markdown: bool = False,
        display: dict[str, Any] | None = None,
        kind: str = "text",
        status_badge: str = "",
        status_variant: str = "neutral",
    ) -> None:
        self._result_title = title
        self._result_body = body
        self._result_markdown = markdown
        self._result_display = display
        self._result_kind = kind
        self._result_status_badge = status_badge
        self._result_status_variant = status_variant
        self.refresh(layout=True, recompose=True)

    def compose(self) -> ComposeResult:
        yield Static(self._call_title, classes="tool-message-title", markup=False)
        if self._call_body:
            with VerticalScroll(classes="tool-card-scroll"):
                yield Static(self._call_body, classes="tool-message-body", markup=False)
        if self._result_title or self._result_status_badge:
            with Horizontal(classes="tool-message-result-row"):
                if self._result_title:
                    yield Static(
                        self._result_title,
                        classes="tool-message-result-title",
                        markup=False,
                    )
                if self._result_status_badge:
                    badge_classes = "tool-message-badge"
                    if self._result_status_variant == "success":
                        badge_classes += " tool-message-badge-success"
                    elif self._result_status_variant == "error":
                        badge_classes += " tool-message-badge-error"
                    yield Static(
                        self._result_status_badge,
                        classes=badge_classes,
                        markup=False,
                    )
        if self._result_kind == "file_diff":
            with VerticalScroll(classes="tool-card-scroll"):
                yield DiffWidget(
                    str(
                        self._result_title or self._result_display.get("path", "")
                        if isinstance(self._result_display, dict)
                        else self._result_title
                    ),
                    self._result_status_badge,
                    self._result_body,
                    show_header=False,
                    show_stats=False,
                )
        elif self._result_body:
            with VerticalScroll(classes="tool-card-scroll"):
                if self._result_markdown:
                    yield MarkdownWidget(self._result_body)
                else:
                    preserve_whitespace = self._result_kind == "block"
                    yield Static(
                        self._result_body,
                        classes="tool-message-body",
                        markup=False,
                        expand=not preserve_whitespace,
                    )


class StatusFooter(Static):
    """Custom footer showing model, tokens, cost, context %."""

    DEFAULT_CSS = """
    StatusFooter {
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._model: str = ""
        self._thinking_level: str = ""
        self._total_input: int = 0
        self._total_output: int = 0
        self._total_cost: float = 0.0
        self._context_pct: float = 0.0
        self._cwd: str = ""
        self._activity_label: str = "idle"
        self._busy: bool = False
        self._in_cmux: bool = is_cmux()

    def render(self) -> Text:
        parts: list[str] = []
        if self._model:
            model_label = self._model
            if self._thinking_level:
                model_label = f"{model_label} [{self._thinking_level}]"
            parts.append(model_label)
        activity = self._activity_label.strip() or "idle"
        if self._busy:
            parts.append(f"● {activity}")
        else:
            parts.append(activity)
        parts.append(f"{self._total_input + self._total_output} tok")
        if self._total_cost > 0:
            parts.append(f"${self._total_cost:.4f}")
        if self._context_pct > 0:
            parts.append(f"ctx {self._context_pct:.0%}")
        # Show current working directory (~ for home)
        cwd = self._cwd or os.getcwd()
        home = os.path.expanduser("~")
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home) :]
        parts.append(cwd)
        if self._in_cmux:
            parts.append("cmux")
        return Text(" \u2502 ".join(parts))

    def update_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        input_price: float = 0.0,
        output_price: float = 0.0,
    ) -> None:
        self._total_input += input_tokens
        self._total_output += output_tokens
        self._total_cost += (
            input_tokens * input_price / 1_000_000 + output_tokens * output_price / 1_000_000
        )
        self.refresh()

    def update_context_pct(self, estimated_tokens: int, context_window: int) -> None:
        if context_window > 0:
            self._context_pct = estimated_tokens / context_window
        self.refresh()

    def set_model(self, model: str) -> None:
        self._model = model
        self.refresh()

    def set_thinking_level(self, level: str) -> None:
        self._thinking_level = level.strip()
        self.refresh()

    def set_cwd(self, cwd: str) -> None:
        self._cwd = cwd
        self.refresh()

    def set_activity(self, label: str, *, busy: bool) -> None:
        self._activity_label = label.strip() or ("working" if busy else "idle")
        self._busy = busy
        self.refresh()


# ── Permission panel ──────────────────────────────────────────


class PermissionPanel(Static):
    """Inline panel asking to approve or deny a tool call."""

    BINDINGS = [
        Binding("y", "approve_once", "Allow once", show=False),
        Binding("a", "approve_all", "Allow all", show=False),
        Binding("n", "deny", "Deny", show=False),
        Binding("escape", "deny", "Deny", show=False),
    ]
    DEFAULT_CSS = """
    PermissionPanel {
        display: none;
        height: auto;
        margin: 0 1;
        padding: 0 1;
        background: $error 15%;
        border: thick $error;
        color: $text;
    }
    PermissionPanel.visible {
        display: block;
    }
    #permission-title {
        text-style: bold;
        color: $error;
        margin-bottom: 1;
    }
    #permission-detail {
        margin-bottom: 1;
        color: $text;
    }
    #permission-hint {
        margin-bottom: 1;
        color: $text-muted;
    }
    #permission-buttons {
        height: auto;
    }
    #permission-buttons Button {
        margin: 0 1 0 0;
    }
    """
    can_focus = True

    def __init__(
        self,
        on_decision: Callable[[str], None],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._on_decision = on_decision
        self._tool_name = ""
        self._tool_args: dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        yield Static("", id="permission-title")
        yield Static("", id="permission-detail")
        yield Static(
            "Keys: [y] allow once, [a] allow all, [n]/[esc] deny",
            id="permission-hint",
        )
        with Horizontal(id="permission-buttons"):
            yield Button("[y] Allow once", id="permission-btn-once", variant="primary")
            yield Button("[a] Allow all", id="permission-btn-all", variant="success")
            yield Button("[n] Deny", id="permission-btn-deny", variant="error")

    def open_request(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        self._tool_name = tool_name
        self._tool_args = dict(tool_args)
        self.query_one("#permission-title", Static).update(
            f"⚠ Permission required: {self._tool_name}"
        )
        self.query_one("#permission-detail", Static).update(self._detail_text())
        self.add_class("visible")
        self.focus()

    def close_request(self) -> None:
        self._tool_name = ""
        self._tool_args = {}
        self.query_one("#permission-title", Static).update("")
        self.query_one("#permission-detail", Static).update("")
        self.remove_class("visible")

    def _detail_text(self) -> str:
        if self._tool_name == "bash":
            return str(self._tool_args.get("command", ""))[:300]
        return ", ".join(f"{key}={value!r}" for key, value in self._tool_args.items())[:300]

    def _submit(self, decision: str) -> None:
        self._on_decision(decision)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "permission-btn-once":
            self._submit("once")
        elif event.button.id == "permission-btn-all":
            self._submit("all")
        else:
            self._submit("deny")

    def action_approve_once(self) -> None:
        self._submit("once")

    def action_approve_all(self) -> None:
        self._submit("all")

    def action_deny(self) -> None:
        self._submit("deny")


class DockInputPanel(Static):
    """Inline input panel for dock flows such as add server."""

    DEFAULT_CSS = """
    DockInputPanel {
        dock: bottom;
        height: auto;
        margin-top: 1;
        padding: 1 1;
        background: $surface;
        border: round $primary 40%;
        display: none;
    }
    DockInputPanel.visible {
        display: block;
    }
    #dock-input-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #dock-input-detail {
        color: $text-muted;
        margin-bottom: 1;
    }
    #dock-input-field {
        margin-bottom: 1;
    }
    #dock-input-buttons {
        height: 3;
        align: right middle;
    }
    #dock-input-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._mode: str = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="dock-input-title")
        yield Static("", id="dock-input-detail")
        yield Input(placeholder="", id="dock-input-field")
        with Horizontal(id="dock-input-buttons"):
            yield Button("Submit", id="dock-input-submit", variant="primary")
            yield Button("Cancel", id="dock-input-cancel")

    def on_mount(self) -> None:
        self.close()

    def open(self, *, mode: str, title: str, detail: str, placeholder: str = "") -> None:
        self._mode = mode
        self.query_one("#dock-input-title", Static).update(title)
        self.query_one("#dock-input-detail", Static).update(detail)
        field = self.query_one("#dock-input-field", Input)
        field.placeholder = placeholder
        field.value = ""
        self.add_class("visible")
        field.focus()

    def close(self) -> None:
        self._mode = ""
        self.remove_class("visible")

    def is_open(self) -> bool:
        return self.has_class("visible")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dock-input-submit":
            self._submit()
            return
        self.post_message(DockInputClosed(self._mode))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "dock-input-field":
            self._submit()

    def _submit(self) -> None:
        value = self.query_one("#dock-input-field", Input).value.strip()
        self.post_message(DockInputSubmitted(self._mode, value))


class DockActionPanel(Static):
    """Inline action panel for the server dock that does not hide the rest of the UI."""

    DEFAULT_CSS = """
    DockActionPanel {
        dock: bottom;
        height: auto;
        max-height: 16;
        margin-top: 1;
        padding: 1 1;
        background: $surface;
        border: round $primary 40%;
        display: none;
    }
    DockActionPanel.visible {
        display: block;
    }
    #dock-action-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #dock-action-help {
        color: $text-muted;
        margin-bottom: 1;
    }
    #dock-action-confirm {
        color: $warning;
        margin-bottom: 1;
        display: none;
    }
    DockActionPanel.confirming #dock-action-confirm {
        display: block;
    }
    #dock-action-options {
        height: auto;
        max-height: 8;
        margin-bottom: 1;
    }
    #dock-action-buttons {
        height: 3;
        align: right middle;
    }
    #dock-action-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._confirm_action: str | None = None

    def compose(self) -> ComposeResult:
        yield Static("Actions", id="dock-action-title")
        yield Static("Choose an action for the selected tree item.", id="dock-action-help")
        yield Static("", id="dock-action-confirm")
        yield OptionList(id="dock-action-options")
        with Horizontal(id="dock-action-buttons"):
            yield Button("Run", id="dock-action-run", variant="primary")
            yield Button("Close", id="dock-action-close")

    def on_mount(self) -> None:
        self.close()

    def open(self, title: str, actions: list[tuple[str, str]]) -> None:
        self._confirm_action = None
        self.remove_class("confirming")
        self.query_one("#dock-action-title", Static).update(title)
        self.query_one("#dock-action-help", Static).update(
            "Choose an action for the selected tree item."
        )
        self.query_one("#dock-action-confirm", Static).update("")
        self.query_one("#dock-action-run", Button).label = "Run"
        options = self.query_one("#dock-action-options", OptionList)
        options.clear_options()
        options.add_options([Option(label, id=value) for value, label in actions])
        options.highlighted = 0 if actions else None
        self.add_class("visible")
        options.focus()

    def close(self) -> None:
        self._confirm_action = None
        self.remove_class("visible")
        self.remove_class("confirming")

    def is_open(self) -> bool:
        return self.has_class("visible")

    def selected_action(self) -> str | None:
        options = self.query_one("#dock-action-options", OptionList)
        if options.highlighted is None:
            return None
        return options.get_option_at_index(options.highlighted).id

    def request_confirmation(self, action: str, prompt: str) -> None:
        self._confirm_action = action
        self.add_class("confirming")
        help_text = "Destructive action requires confirmation."
        self.query_one("#dock-action-help", Static).update(help_text)
        self.query_one("#dock-action-confirm", Static).update(prompt)
        self.query_one("#dock-action-run", Button).label = "Confirm"
        self.query_one("#dock-action-run", Button).variant = "error"
        self.query_one("#dock-action-close", Button).label = "Cancel"
        self.query_one("#dock-action-run", Button).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "dock-action-options":
            return
        action = event.option.id
        if action:
            self.post_message(DockActionInvoked(str(action)))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dock-action-run":
            if self._confirm_action:
                self.post_message(DockActionConfirmed(self._confirm_action))
                return
            action = self.selected_action()
            if action:
                self.post_message(DockActionInvoked(str(action)))
            return
        self.post_message(DockActionClosed())


class InlineInputPanel(Static):
    """Inline input panel for non-dock flows such as remote OAuth code paste."""

    DEFAULT_CSS = """
    InlineInputPanel {
        display: none;
        height: auto;
        margin: 0 1 1 1;
        padding: 1 1;
        background: $surface;
        border: round $primary 40%;
    }
    InlineInputPanel.visible {
        display: block;
    }
    #inline-input-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #inline-input-detail {
        color: $text-muted;
        margin-bottom: 1;
    }
    #inline-input-field {
        margin-bottom: 1;
    }
    #inline-input-buttons {
        height: 3;
        align: right middle;
    }
    #inline-input-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._mode: str = ""

    def compose(self) -> ComposeResult:
        yield Static("", id="inline-input-title")
        yield Static("", id="inline-input-detail")
        yield Input(placeholder="", id="inline-input-field")
        with Horizontal(id="inline-input-buttons"):
            yield Button("Submit", id="inline-input-submit", variant="primary")
            yield Button("Cancel", id="inline-input-cancel")

    def on_mount(self) -> None:
        self.close()

    def open(self, *, mode: str, title: str, detail: str, placeholder: str = "") -> None:
        self._mode = mode
        self.query_one("#inline-input-title", Static).update(title)
        self.query_one("#inline-input-detail", Static).update(detail)
        field = self.query_one("#inline-input-field", Input)
        field.placeholder = placeholder
        field.value = ""
        self.add_class("visible")
        field.focus()

    def close(self) -> None:
        self._mode = ""
        self.remove_class("visible")

    def is_open(self) -> bool:
        return self.has_class("visible")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "inline-input-submit":
            self._submit()
            return
        self.post_message(InlineInputClosed(self._mode))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "inline-input-field":
            self._submit()

    def _submit(self) -> None:
        value = self.query_one("#inline-input-field", Input).value.strip()
        self.post_message(InlineInputSubmitted(self._mode, value))


class InlineRuleEditorPanel(Static):
    """Inline rule editor panel replacing fullscreen modal rule dialogs."""

    DEFAULT_CSS = """
    InlineRuleEditorPanel {
        display: none;
        height: auto;
        margin: 0 1 1 1;
        padding: 1 1;
        background: $surface;
        border: round $primary 40%;
    }
    InlineRuleEditorPanel.visible {
        display: block;
    }
    #rule-editor-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #rule-editor-help {
        margin-bottom: 1;
        color: $text-muted;
    }
    #rule-editor-scope,
    #rule-editor-enabled {
        margin-bottom: 1;
    }
    #rule-editor-text {
        height: 10;
        margin-bottom: 1;
    }
    #rule-editor-buttons {
        height: 3;
        align: right middle;
    }
    #rule-editor-buttons Button {
        margin-left: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="rule-editor-title")
        yield Static("Set scope, enter rule text, then save.", id="rule-editor-help")
        yield Input(value="project", placeholder="project or global", id="rule-editor-scope")
        yield Input(value="true", placeholder="true or false", id="rule-editor-enabled")
        yield TextArea("", id="rule-editor-text")
        with Horizontal(id="rule-editor-buttons"):
            yield Button("Save", id="btn-save-rule", variant="primary")
            yield Button("Cancel", id="btn-cancel-rule")

    def on_mount(self) -> None:
        self.close()

    def open(
        self,
        *,
        title: str,
        text: str = "",
        scope: str = "project",
        enabled: bool = True,
    ) -> None:
        self.query_one("#rule-editor-title", Static).update(title)
        self.query_one("#rule-editor-scope", Input).value = (
            scope if scope in {"project", "global"} else "project"
        )
        self.query_one("#rule-editor-enabled", Input).value = "true" if enabled else "false"
        self.query_one("#rule-editor-text", TextArea).load_text(text)
        self.add_class("visible")
        self.query_one("#rule-editor-text", TextArea).focus()

    def close(self) -> None:
        self.remove_class("visible")

    def is_open(self) -> bool:
        return self.has_class("visible")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save-rule":
            self._submit()
            return
        self.post_message(RuleEditorClosed())

    def _submit(self) -> None:
        scope = self.query_one("#rule-editor-scope", Input).value.strip().lower()
        enabled_raw = self.query_one("#rule-editor-enabled", Input).value.strip().lower()
        text = self.query_one("#rule-editor-text", TextArea).text.strip()
        if scope not in {"project", "global"}:
            scope = "project"
        enabled = enabled_raw not in {"false", "0", "no", "off", "disabled"}
        self.post_message(RuleEditorSubmitted({"scope": scope, "enabled": enabled, "text": text}))


# ── Main App ──────────────────────────────────────────────────


class ArtelApp(App):
    """Textual TUI for the Artel coding agent."""

    TITLE = "Artel"

    CSS = """
    #app-body {
        height: 1fr;
    }
    #server-dock-sidebar {
        width: 36;
    }
    #main-content {
        height: 1fr;
        width: 1fr;
    }
    #chat-scroll {
        height: 1fr;
    }
    #chat-container {
        height: auto;
    }
    #command-suggestions {
        display: none;
        height: 5;
        margin: 0 1;
        background: $surface;
        color: $text;
    }
    #command-suggestions.visible {
        display: block;
    }
    #input-bar {
        height: 6;
        min-height: 3;
        max-height: 12;
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+с", "quit", "Quit", show=False),
        Binding("ctrl+p", "command_palette", "Command palette", show=False),
        Binding("ctrl+з", "command_palette", "Command palette", show=False),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("ctrl+д", "clear", "Clear", show=False),
        Binding("ctrl+o", "toggle_tools", "Toggle tools"),
        Binding("ctrl+щ", "toggle_tools", "Toggle tools", show=False),
        Binding("ctrl+x", "server_dock_actions", "Server dock actions", show=False),
        Binding("ctrl+ч", "server_dock_actions", "Server dock actions", show=False),
        Binding("ctrl+g", "toggle_server_dock", "Toggle server dock", show=False),
        Binding("ctrl+п", "toggle_server_dock", "Toggle server dock", show=False),
        Binding("ctrl+b", "toggle_sidebar", "Toggle board"),
        Binding("ctrl+и", "toggle_sidebar", "Toggle board", show=False),
        Binding("ctrl+t", "focus_tasks", "Focus tasks", show=False),
        Binding("ctrl+е", "focus_tasks", "Focus tasks", show=False),
        Binding("ctrl+n", "focus_notes", "Focus notes", show=False),
        Binding("ctrl+т", "focus_notes", "Focus notes", show=False),
        Binding("ctrl+shift+c", "copy_last_assistant_message", "Copy last reply", show=False),
        Binding("ctrl+shift+с", "copy_last_assistant_message", "Copy last reply", show=False),
    ]

    def __init__(
        self,
        *,
        remote_url: str = "",
        auth_token: str = "",
        forward_credentials: str = "",
        continue_session: bool = False,
        resume_id: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.remote_url = remote_url
        self.auth_token = auth_token
        self._forward_credentials_spec = forward_credentials
        self._continue_session = continue_session
        self._resume_id = resume_id
        self._session: AgentSession | None = None
        self._store: SessionStore | None = None
        self._extensions: list[Any] = []
        self._tui_extensions: list[Any] = []
        self._current_widget: MessageWidget | None = None
        self._ws: Any = None  # websocket connection for remote mode
        self._remote_session_id = str(uuid.uuid4())
        self._remote_control_client: RemoteControlClient | None = None
        self._remote_project_dir: str = ""
        self._remote_extension_commands: set[str] = set()
        self._prompts: dict[str, str] = {}  # loaded prompt templates
        self._skills: dict[str, Any] = {}  # loaded skills (Skill objects)
        self._active_theme: str = "dark"
        self._remote_rule_overrides: dict[str, Any] = {}
        self._local_rule_overrides = SessionRuleOverrides.empty()
        self._tool_collapsibles: list[Collapsible] = []
        self._active_tool_cards: dict[str, ToolCard] = {}
        self._tool_call_names: dict[str, str] = {}
        self._input_price: float = 0.0  # per 1M tokens
        self._output_price: float = 0.0
        self._auto_approve_all: bool = False
        self._provider_model: str = ""  # "provider/model" for DB storage
        self._model_autocomplete_refs: list[str] = []
        self._model_autocomplete_descriptions: dict[str, str] = {}
        self._model_autocomplete_loaded: bool = False
        self._model_autocomplete_loading: bool = False
        self._resume_autocomplete_suggestions: list[SlashCommandSuggestion] = []
        self._resume_autocomplete_loaded: bool = False
        self._resume_autocomplete_loading: bool = False
        self._fork_autocomplete_suggestions: list[SlashCommandSuggestion] = []
        self._fork_autocomplete_loaded: bool = False
        self._fork_autocomplete_loading: bool = False
        self._suppress_next_command_menu_update: bool = False
        self._pending_permission_requests: list[PendingPermissionRequest] = []
        self._active_permission_request: PendingPermissionRequest | None = None
        self._run_busy: bool = False
        self._assistant_message_history: list[MessageWidget] = []
        self._pending_attachments: list[ImageAttachment] = []
        self._server_dock_visible: bool = True
        self._saved_servers: list[SavedArtelServer] = []
        self._dismissed_server_urls: set[str] = set()
        self._pending_remote_oauth: dict[str, str] | None = None
        self._pending_rule_editor_existing: Any = None
        self._sidebar_visible: bool = False
        self._suspend_board_editor_events: bool = False
        self._tasks_save_task: asyncio.Task[None] | None = None
        self._notes_save_task: asyncio.Task[None] | None = None
        self._board_save_delay: float = 0.35
        self._last_loaded_tasks_text: str = ""
        self._last_loaded_notes_text: str = ""
        self._board_poll_inflight: bool = False
        self._delegation_events_task: asyncio.Task[None] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="app-body"):
            yield ServerDockSidebar(id="server-dock-sidebar")
            with Vertical(id="main-content"):
                yield PermissionPanel(
                    self._resolve_permission_panel_decision, id="permission-panel"
                )
                with VerticalScroll(id="chat-scroll"):
                    yield Vertical(id="chat-container")
                yield OptionList(id="command-suggestions", compact=True)
                yield InlineInputPanel(id="inline-input-panel")
                yield InlineRuleEditorPanel(id="inline-rule-editor-panel")
                yield PendingAttachmentsBar(id="pending-attachments")
                yield ComposerTextArea(
                    "",
                    placeholder="Type a message… (Enter to send, Shift+Enter for newline)",
                    id="input-bar",
                )
                yield StatusFooter(id="status-footer")
            yield BoardSidebar(id="board-sidebar")
        yield Footer()

    async def on_mount(self) -> None:
        self.set_interval(self._board_poll_interval_seconds(), self._poll_board_state)
        config = load_config(os.getcwd())
        if self.remote_url:
            self.sub_title = f"remote: {self.remote_url}"
        else:
            await self._init_local_session()

        # Apply theme from config
        self._active_theme = config.ui.theme
        self._apply_theme(self._active_theme)

        # Load prompts and skills
        project_dir = os.getcwd()
        self._prompts = load_prompts(project_dir)
        self._skills = load_skills(project_dir)
        await self._load_tui_extensions(config)
        await self._mount_builtin_delegation_widget()

        # Apply custom keybindings from config
        for key, action in config.keybindings.bindings.items():
            self.bind(key, action, description=action)
            for alias in _layout_safe_binding_variants(key):
                self.bind(alias, action, description=action, show=False)

        if self.remote_url:
            await self._restore_initial_remote_session()
            if self._forward_credentials_spec:
                await self._forward_remote_credentials(config)

        self._saved_servers = load_saved_servers()
        self._auto_collapse_server_dock_for_size()
        self._sync_pending_attachments_bar()
        await self._load_board_state()
        await self._refresh_server_dock()
        self._start_delegation_events()
        self.call_after_refresh(self._focus_input)

    def _focus_input(self) -> None:
        """Keep the main input focused for immediate typing."""
        self.query_one("#input-bar", TextArea).focus()

    def _start_delegation_events(self) -> None:
        if self.remote_url:
            return
        if self._delegation_events_task is not None and not self._delegation_events_task.done():
            return
        from artel_core.delegation.registry import get_registry

        queue = get_registry().subscribe()
        self._delegation_events_task = asyncio.create_task(self._consume_delegation_events(queue))

    async def _consume_delegation_events(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        from artel_core.delegation.registry import get_registry

        try:
            while True:
                payload = await queue.get()
                event_type = str(payload.get("type", ""))
                run = payload.get("run", {})
                if not isinstance(run, dict):
                    continue
                if event_type == "completed":
                    task = str(run.get("task", "")).strip()
                    preview = str(run.get("result_preview", "")).strip()
                    message = (
                        f"✅ Delegation completed: {task}" if task else "✅ Delegation completed"
                    )
                    if preview:
                        message += f"\n{preview}"
                    self._add_message(message, role="tool")
                elif event_type == "failed":
                    task = str(run.get("task", "")).strip()
                    error = str(run.get("error", "")).strip()
                    message = f"✗ Delegation failed: {task}" if task else "✗ Delegation failed"
                    if error:
                        message += f"\n{error}"
                    self._add_message(message, role="error")
        except asyncio.CancelledError:
            get_registry().unsubscribe(queue)
            raise

    def _server_dock(self) -> ServerDockSidebar:
        return self.query_one("#server-dock-sidebar", ServerDockSidebar)

    def _board_sidebar(self) -> BoardSidebar:
        return self.query_one("#board-sidebar", BoardSidebar)

    def _inline_input_panel(self) -> InlineInputPanel:
        return self.query_one("#inline-input-panel", InlineInputPanel)

    def _inline_rule_editor_panel(self) -> InlineRuleEditorPanel:
        return self.query_one("#inline-rule-editor-panel", InlineRuleEditorPanel)

    def _set_server_dock_status(self, text: str) -> None:
        with suppress(Exception):
            self._server_dock().set_status(text)

    def _server_dock_input_panel(self) -> DockInputPanel:
        return self._server_dock().input_panel()

    def _server_dock_action_panel(self) -> DockActionPanel:
        return self._server_dock().action_panel()

    def _open_server_dock_input(
        self,
        *,
        mode: str,
        title: str,
        detail: str,
        placeholder: str = "",
    ) -> None:
        self._close_server_dock_actions()
        self._server_dock_input_panel().open(
            mode=mode,
            title=title,
            detail=detail,
            placeholder=placeholder,
        )
        self._set_server_dock_status("Enter a value and press Enter or Submit")

    def _close_server_dock_input(self) -> None:
        with suppress(Exception):
            self._server_dock_input_panel().close()

    def _close_server_dock_actions(self) -> None:
        with suppress(Exception):
            self._server_dock_action_panel().close()

    def _server_dock_selected_data(self) -> ServerDockNodeData | None:
        with suppress(Exception):
            node = self._server_dock().tree().cursor_node
            if node is not None and isinstance(node.data, ServerDockNodeData):
                return node.data
        return None

    def _server_dock_actions_for(self, data: ServerDockNodeData) -> list[tuple[str, str]]:
        if data.kind == "server":
            return [
                ("connect", "Connect"),
                ("refresh", "Refresh sessions"),
                ("remove", "Remove server"),
            ]
        if data.kind == "project":
            return [
                ("open_project", "Switch to project"),
                ("delete_project_sessions", "Delete all project sessions"),
                ("refresh", "Refresh server"),
            ]
        if data.kind == "session":
            return [
                ("resume", "Resume session"),
                ("open_project", "Switch to session project"),
                ("delete_session", "Delete session"),
                ("refresh", "Refresh server"),
            ]
        return []

    async def _run_server_dock_action(self, data: ServerDockNodeData, action: str) -> None:
        if action == "connect" and data.remote_url:
            await self._connect_to_server(data.remote_url, auth_token=data.auth_token, save=False)
            return
        if action == "open_project" and data.remote_url and data.project_dir:
            await self._connect_to_server(
                data.remote_url,
                auth_token=data.auth_token,
                save=False,
                project_dir=data.project_dir,
            )
            return
        if action == "resume" and data.remote_url and data.session_id:
            await self._connect_to_server(
                data.remote_url,
                auth_token=data.auth_token,
                save=False,
                resume_session_id=data.session_id,
            )
            return
        if action == "refresh" and data.remote_url:
            await self._refresh_server_dock()
            self._set_server_dock_status(f"Refreshed {data.name or data.remote_url}")
            return
        if action == "remove" and data.remote_url:
            removed_url = data.remote_url.strip()
            self._dismissed_server_urls.add(removed_url)
            self._saved_servers = remove_saved_server(removed_url)
            if self.remote_url.strip() == removed_url:
                self._saved_servers = [
                    server
                    for server in self._saved_servers
                    if server.remote_url.strip() != removed_url
                ]
            await self._refresh_server_dock()
            self._add_message(f"Removed server: {data.name or data.remote_url}", role="tool")
            return
        if action == "delete_session" and data.remote_url and data.session_id:
            try:
                await RemoteControlClient(data.remote_url, auth_token=data.auth_token).request(
                    "DELETE",
                    f"/api/sessions/{data.session_id}",
                )
            except Exception as exc:
                self._add_message(f"Failed to delete session: {exc}", role="error")
                return
            await self._refresh_server_dock()
            self._add_message(f"Deleted session: {data.name or data.session_id}", role="tool")
            return
        if action == "delete_project_sessions" and data.remote_url and data.project_dir:
            try:
                payload = await RemoteControlClient(
                    data.remote_url,
                    auth_token=data.auth_token,
                ).list_sessions()
                raw_sessions = payload.get("sessions", [])
                sessions = [item for item in raw_sessions if isinstance(item, dict)]
                project_sessions = [
                    item
                    for item in sessions
                    if str(item.get("project_dir", "") or "").strip() == data.project_dir
                ]
                deleted_ids: list[str] = []
                for item in project_sessions:
                    session_id = str(item.get("id", "")).strip()
                    if not session_id:
                        continue
                    await RemoteControlClient(data.remote_url, auth_token=data.auth_token).request(
                        "DELETE",
                        f"/api/sessions/{session_id}",
                    )
                    deleted_ids.append(session_id)
                    if self.remote_url == data.remote_url and self._remote_session_id == session_id:
                        self._remote_session_id = str(uuid.uuid4())
                        self._remote_project_dir = ""
                if not deleted_ids:
                    self._add_message(
                        f"No sessions found for project: {data.name or data.project_dir}",
                        role="tool",
                    )
                    return
            except Exception as exc:
                self._add_message(f"Failed to delete project sessions: {exc}", role="error")
                return
            await self._refresh_server_dock()
            if self.remote_url == data.remote_url:
                with suppress(Exception):
                    await self._sync_remote_session_state()
                with suppress(Exception):
                    await self._load_board_state()
            project_label = data.name or data.project_dir
            self._add_message(
                f"Deleted {len(deleted_ids)} session(s) for project: {project_label}",
                role="tool",
            )
            return

    async def _open_server_dock_actions(self, data: ServerDockNodeData) -> None:
        actions = self._server_dock_actions_for(data)
        if not actions:
            self._close_server_dock_actions()
            self._set_server_dock_status("No actions for this item")
            return
        title = f"Actions: {data.name or data.kind}"
        self._server_dock_action_panel().open(title, actions)
        self._set_server_dock_status("Choose an action and press Enter or Run")

    def _server_dock_confirmation_prompt(self, data: ServerDockNodeData, action: str) -> str:
        if action == "delete_project_sessions":
            return (
                "Delete all sessions for project "
                f"{data.name or data.project_dir}? This cannot be undone."
            )
        if action == "delete_session":
            return f"Delete session {data.name or data.session_id}? This cannot be undone."
        if action == "remove":
            return f"Remove saved server {data.name or data.remote_url} from the dock?"
        return "Are you sure?"

    def _server_dock_action_requires_confirmation(self, action: str) -> bool:
        return action in {"delete_session", "delete_project_sessions", "remove"}

    def _set_board_status(self, text: str) -> None:
        with suppress(Exception):
            self._board_sidebar().set_status(text)

    async def _refresh_server_dock(self) -> None:
        dock = self._server_dock()
        dock.set_visible(self._server_dock_visible)
        tree = dock.tree()
        tree.clear()
        root = tree.root
        root.expand()

        active_remote_url = self.remote_url.strip()
        rendered_servers = [
            server
            for server in self._saved_servers
            if server.remote_url.strip() not in self._dismissed_server_urls
        ]
        if active_remote_url and not any(
            server.remote_url == active_remote_url for server in rendered_servers
        ):
            rendered_servers.append(
                SavedArtelServer(
                    name=default_server_name(active_remote_url),
                    remote_url=active_remote_url,
                    auth_token=self.auth_token,
                )
            )

        if not rendered_servers:
            root.add_leaf(
                "No saved servers. Use /server-add or the Add button.",
                data=ServerDockNodeData(kind="info", name="empty"),
            )
            dock.set_status("No saved Artel servers")
            return

        active_session_id = self._remote_session_id.strip() if active_remote_url else ""

        for server in sorted(rendered_servers, key=lambda item: item.name.lower()):
            server_label = server.name
            if server.remote_url == active_remote_url:
                server_label = f"● {server_label}"
            server_node = root.add(
                server_label,
                data=ServerDockNodeData(
                    kind="server",
                    remote_url=server.remote_url,
                    auth_token=server.auth_token,
                    name=server.name,
                ),
                expand=server.remote_url == active_remote_url,
            )
            sessions: list[dict[str, Any]] = []
            error_text = ""
            try:
                payload = await RemoteControlClient(
                    server.remote_url,
                    auth_token=server.auth_token,
                ).list_sessions()
                raw_sessions = payload.get("sessions", [])
                if isinstance(raw_sessions, list):
                    sessions = [item for item in raw_sessions if isinstance(item, dict)]
            except Exception as exc:
                error_text = str(exc)

            if error_text:
                server_node.add_leaf(
                    f"Connection failed: {error_text}",
                    data=ServerDockNodeData(
                        kind="server_error",
                        remote_url=server.remote_url,
                        auth_token=server.auth_token,
                        name=server.name,
                    ),
                )
                continue

            normalized = sorted(
                sessions,
                key=lambda item: (
                    str(item.get("project_dir", "") or "").lower(),
                    str(item.get("updated_at", "") or ""),
                ),
                reverse=True,
            )
            if not normalized:
                server_node.add_leaf(
                    "No sessions",
                    data=ServerDockNodeData(
                        kind="info",
                        remote_url=server.remote_url,
                        auth_token=server.auth_token,
                        name="no-sessions",
                    ),
                )
                continue

            normalized.sort(key=lambda item: str(item.get("project_dir", "") or ""))
            for project_dir, project_items_iter in groupby(
                normalized, key=lambda item: str(item.get("project_dir", "") or "")
            ):
                project_items = list(project_items_iter)
                project_name = Path(project_dir).name if project_dir else "(default project)"
                project_label = f"📁 {project_name}"
                if (
                    server.remote_url == active_remote_url
                    and project_dir
                    and project_dir == self._remote_project_dir
                ):
                    project_label = f"● {project_label}"
                project_node = server_node.add(
                    project_label,
                    data=ServerDockNodeData(
                        kind="project",
                        remote_url=server.remote_url,
                        auth_token=server.auth_token,
                        project_dir=project_dir,
                        name=project_name,
                    ),
                    expand=server.remote_url == active_remote_url
                    and project_dir == self._remote_project_dir,
                )
                for session in sorted(
                    project_items,
                    key=lambda item: str(item.get("updated_at", "") or ""),
                    reverse=True,
                ):
                    session_id = str(session.get("id", "")).strip()
                    title = str(session.get("title", "")).strip() or "(untitled)"
                    session_label = title
                    if (
                        session_id
                        and server.remote_url == active_remote_url
                        and session_id == active_session_id
                    ):
                        session_label = f"● {session_label}"
                    project_node.add_leaf(
                        session_label,
                        data=ServerDockNodeData(
                            kind="session",
                            remote_url=server.remote_url,
                            auth_token=server.auth_token,
                            session_id=session_id,
                            project_dir=project_dir,
                            name=title,
                        ),
                    )
        dock.set_status("Enter on project/session to switch")

    def _board_poll_interval_seconds(self) -> float:
        return 1.0 if self._sidebar_visible else 3.0

    def _poll_board_state(self) -> None:
        self._schedule_background_task(self._poll_board_state_once, exclusive=False, thread=False)

    async def _poll_board_state_once(self) -> None:
        if self._board_poll_inflight:
            return
        self._board_poll_inflight = True
        try:
            project_dir = self._board_project_dir()
            if self.remote_url:
                try:
                    tasks_payload = await self._remote_control().get_session_tasks(
                        self._remote_session_id
                    )
                    notes_payload = await self._remote_control().get_session_notes(
                        self._remote_session_id
                    )
                except Exception:
                    return
                tasks = str(tasks_payload.get("content", ""))
                notes = str(notes_payload.get("content", ""))
            else:
                tasks = await read_project_board_file(tasks_path(project_dir))
                notes = await read_project_board_file(operator_notes_path(project_dir))
            if tasks == self._last_loaded_tasks_text and notes == self._last_loaded_notes_text:
                return
            await self._load_board_state()
        finally:
            self._board_poll_inflight = False

    async def _load_board_state(self) -> None:
        project_dir = self._board_project_dir()
        if self.remote_url:
            try:
                tasks_payload = await self._remote_control().get_session_tasks(
                    self._remote_session_id
                )
                notes_payload = await self._remote_control().get_session_notes(
                    self._remote_session_id
                )
            except Exception:
                tasks_payload = {}
                notes_payload = {}
            tasks = str(tasks_payload.get("content", ""))
            notes = str(notes_payload.get("content", ""))
        else:
            tasks = await read_project_board_file(tasks_path(project_dir))
            notes = await read_project_board_file(operator_notes_path(project_dir))
        self._suspend_board_editor_events = True
        try:
            try:
                sidebar = self._board_sidebar()
            except Exception:
                return
            if not hasattr(sidebar, "set_tasks") or not hasattr(sidebar, "set_notes"):
                return
            sidebar.set_tasks(tasks)
            sidebar.set_notes(notes)
            self._last_loaded_tasks_text = tasks
            self._last_loaded_notes_text = notes
            sidebar.set_visible(self._sidebar_visible)
            sidebar.set_status(
                "Board files: "
                f"{tasks_path(project_dir).name}, {operator_notes_path(project_dir).name}"
            )
        finally:
            self._suspend_board_editor_events = False

    def _board_project_dir(self) -> str:
        if self.remote_url and self._remote_project_dir:
            return self._remote_project_dir
        return os.getcwd()

    def _cancel_board_save_task(self, task: asyncio.Task[None] | None) -> None:
        if task is not None and not task.done():
            task.cancel()

    def _schedule_tasks_save(self, content: str) -> None:
        self._cancel_board_save_task(self._tasks_save_task)
        self._tasks_save_task = asyncio.create_task(self._debounced_save_tasks_text(content))
        self._set_board_status("Tasks modified…")

    def _schedule_notes_save(self, content: str) -> None:
        self._cancel_board_save_task(self._notes_save_task)
        self._notes_save_task = asyncio.create_task(self._debounced_save_notes_text(content))
        self._set_board_status("Operator notes modified…")

    async def _debounced_save_tasks_text(self, content: str) -> None:
        try:
            await asyncio.sleep(self._board_save_delay)
            await self._save_tasks_text(content)
        except asyncio.CancelledError:
            return

    async def _debounced_save_notes_text(self, content: str) -> None:
        try:
            await asyncio.sleep(self._board_save_delay)
            await self._save_notes_text(content)
        except asyncio.CancelledError:
            return

    async def _save_tasks_text(self, content: str) -> None:
        self._set_board_status("Saving tasks…")
        try:
            if self.remote_url:
                await self._remote_control().put_session_tasks(self._remote_session_id, content)
            else:
                await write_project_board_file(tasks_path(self._board_project_dir()), content)
        except Exception as exc:
            self._set_board_status(f"Failed to save tasks: {exc}")
            return
        self._set_board_status("Tasks saved")

    async def _save_notes_text(self, content: str) -> None:
        self._set_board_status("Saving operator notes…")
        try:
            if self.remote_url:
                await self._remote_control().put_session_notes(self._remote_session_id, content)
            else:
                await write_project_board_file(
                    operator_notes_path(self._board_project_dir()), content
                )
        except Exception as exc:
            self._set_board_status(f"Failed to save operator notes: {exc}")
            return
        self._set_board_status("Operator notes saved")

    def _handle_board_tool_event(self, kind: str, payload: dict[str, Any]) -> None:
        self._schedule_background_task(
            self._refresh_board_after_tool_event, kind, payload, exclusive=False, thread=False
        )

    async def _refresh_board_after_tool_event(self, kind: str, payload: dict[str, Any]) -> None:
        await self._load_board_state()
        if kind == "task_added":
            title = str(payload.get("title", "")).strip() or "task"
            task_id = payload.get("task_id")
            self._add_message(f"🗂 Added task #{task_id}: {title}", role="tool")
        elif kind == "task_updated":
            task_id = payload.get("task_id")
            status = str(payload.get("status", "")).strip()
            suffix = f" ({status})" if status else ""
            self._add_message(f"☑ Updated task #{task_id}{suffix}", role="tool")
        elif kind == "operator_notes_appended":
            self._add_message("📝 Appended to operator notes", role="tool")

    def _set_run_activity(self, label: str, *, busy: bool) -> None:
        self._run_busy = busy
        footer = self.query_one("#status-footer", StatusFooter)
        set_activity = getattr(footer, "set_activity", None)
        if callable(set_activity):
            set_activity(label, busy=busy)

    def _set_footer_thinking_level(self, level: str) -> None:
        try:
            footer = self.query_one("#status-footer", StatusFooter)
        except Exception:
            return
        set_level = getattr(footer, "set_thinking_level", None)
        if callable(set_level):
            set_level(level)

    async def _sync_cmux_session_metadata(self) -> None:
        project_dir = self._remote_project_dir.strip() if self.remote_url else os.getcwd()
        model = self._provider_model.strip()
        branch = ""
        if project_dir:
            try:
                result = subprocess.run(
                    ["git", "-C", project_dir, "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode == 0:
                    branch = result.stdout.strip()
            except Exception:
                branch = ""
        if project_dir:
            await cmux.set_status("project", project_dir, icon="folder", color="#94e2d5")
        if branch and branch != "HEAD":
            await cmux.set_status("branch", branch, icon="git-branch", color="#cba6f7")
        if model:
            await cmux.set_status("model", model, icon="cpu", color="#89b4fa")

    def _composer(self) -> TextArea:
        return self.query_one("#input-bar", TextArea)

    def _composer_text(self) -> str:
        return self._composer().text

    def _set_composer_text(self, text: str) -> None:
        composer = self._composer()
        composer.load_text(text)
        lines = text.split("\n") if text else [""]
        composer.move_cursor((len(lines) - 1, len(lines[-1])))

    def _clear_composer(self) -> None:
        self._composer().load_text("")

    def _sync_pending_attachments_bar(self) -> None:
        with suppress(Exception):
            self.query_one("#pending-attachments", PendingAttachmentsBar).set_attachments(
                self._pending_attachments
            )

    def _consume_pending_attachments(self) -> list[ImageAttachment]:
        attachments = list(self._pending_attachments)
        self._pending_attachments.clear()
        self._sync_pending_attachments_bar()
        return attachments

    def _format_attachment_summary(
        self, attachment: ImageAttachment, *, index: int | None = None
    ) -> str:
        name = attachment.name or Path(attachment.path).name
        prefix = f"[{index}] " if index is not None else ""
        size = ""
        try:
            bytes_size = Path(attachment.path).stat().st_size
            if bytes_size >= 1024 * 1024:
                size = f" — {bytes_size / (1024 * 1024):.1f} MB"
            elif bytes_size >= 1024:
                size = f" — {bytes_size / 1024:.1f} KB"
            else:
                size = f" — {bytes_size} B"
        except Exception:
            pass
        mime = f" ({attachment.mime_type})" if attachment.mime_type else ""
        return f"📎 {prefix}{name}{mime}{size}"

    def _pending_attachment_label(self) -> str:
        if not self._pending_attachments:
            return ""
        return "\n".join(
            self._format_attachment_summary(attachment) for attachment in self._pending_attachments
        )

    def _render_user_submission(
        self, text: str, attachments: list[ImageAttachment] | None = None
    ) -> str:
        prefix = (
            self._pending_attachment_label()
            if attachments is None
            else "\n".join(
                self._format_attachment_summary(attachment) for attachment in attachments
            )
        )
        if prefix and text:
            return f"{prefix}\n{text}"
        if prefix:
            return prefix
        return text

    def _queue_attachment(self, attachment: ImageAttachment) -> None:
        self._pending_attachments.append(attachment)
        self._sync_pending_attachments_bar()

    def _remove_pending_attachment(self, index: int) -> ImageAttachment | None:
        if index < 1 or index > len(self._pending_attachments):
            return None
        attachment = self._pending_attachments.pop(index - 1)
        self._sync_pending_attachments_bar()
        return attachment

    def _clear_pending_attachments(self) -> int:
        count = len(self._pending_attachments)
        self._pending_attachments.clear()
        self._sync_pending_attachments_bar()
        return count

    def _clipboard_image_output_path(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="artel-image-")) / "clipboard.png"

    def _extract_image_paths_from_paste(self, text: str) -> list[str]:
        import shlex

        stripped = text.strip()
        if not stripped:
            return []
        try:
            tokens = shlex.split(stripped)
        except Exception:
            tokens = [line.strip() for line in stripped.splitlines() if line.strip()]
        paths: list[str] = []
        for token in tokens:
            candidate = token.strip()
            if candidate.startswith("file://"):
                candidate = urllib.parse.unquote(urllib.parse.urlparse(candidate).path)
            if not candidate:
                continue
            if len(candidate) > _MAX_PASTED_IMAGE_REFERENCE_CHARS:
                continue
            if not is_supported_image_path(candidate):
                continue
            try:
                resolved = str(Path(candidate).expanduser().resolve())
            except Exception:
                continue
            path = Path(resolved)
            try:
                if path.exists() and path.is_file() and is_supported_image_path(resolved):
                    paths.append(resolved)
            except OSError:
                continue
        return paths

    async def _maybe_handle_pasted_image_reference(self, text: str) -> bool:
        image_paths = self._extract_image_paths_from_paste(text)
        if not image_paths:
            return False
        if not await self._model_supports_vision():
            self._add_message("Current model does not support image input.", role="error")
            return True
        for image_path in image_paths:
            self._queue_attachment(normalize_image_attachment(image_path))
        names = ", ".join(Path(path).name for path in image_paths)
        self._add_message(f"Attached pasted image reference(s): {names}", role="tool")
        return True

    def _paste_image_from_clipboard(self) -> ImageAttachment:
        output_path = self._clipboard_image_output_path()
        commands = [
            ["pngpaste", str(output_path)],
            ["wl-paste", "--type", "image", "--no-newline"],
            ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "$img=[Windows.Forms.Clipboard]::GetImage(); "
                    "if ($img -eq $null) { exit 1 }; "
                    "$img.Save($args[0], [System.Drawing.Imaging.ImageFormat]::Png)"
                ),
                str(output_path),
            ],
        ]
        for command in commands:
            try:
                if command[0] in {"wl-paste", "xclip"}:
                    import subprocess

                    result = subprocess.run(command, check=True, capture_output=True)
                    if result.stdout:
                        output_path.write_bytes(result.stdout)
                else:
                    import subprocess

                    subprocess.run(command, check=True, capture_output=True)
                if output_path.exists() and output_path.stat().st_size > 0:
                    return normalize_image_attachment(str(output_path))
            except Exception:
                continue
        raise RuntimeError(
            "Clipboard image paste is unavailable on this system. Supported "
            "helpers: pngpaste, wl-paste, xclip, powershell."
        )

    def _remote_control(self) -> RemoteControlClient:
        if self._remote_control_client is None:
            self._remote_control_client = RemoteControlClient(
                self.remote_url,
                auth_token=self.auth_token,
            )
        return self._remote_control_client

    def _apply_remote_session_state(self, session: dict[str, Any]) -> None:
        footer = self.query_one("#status-footer", StatusFooter)
        model = str(session.get("model", "")).strip()
        if model:
            self._provider_model = model
            footer.set_model(model)
        thinking_level = str(session.get("thinking_level", "")).strip()
        if thinking_level:
            self._set_footer_thinking_level(thinking_level)
        project_dir = str(session.get("project_dir", "")).strip()
        if project_dir:
            self._remote_project_dir = project_dir
            footer.set_cwd(project_dir)
        self._schedule_background_task(
            self._sync_cmux_session_metadata,
            exclusive=False,
            thread=False,
        )
        overrides = session.get("rule_overrides")
        if isinstance(overrides, dict):
            self._remote_rule_overrides = overrides

    async def _sync_remote_session_state(self) -> None:
        if not self.remote_url:
            return
        footer = self.query_one("#status-footer", StatusFooter)
        try:
            payload = await self._remote_control().get_session(self._remote_session_id)
        except Exception:
            try:
                payload = await self._remote_control().get_server_info()
            except Exception:
                return
            model = str(payload.get("default_model", "")).strip()
            if model:
                self._provider_model = model
                footer.set_model(model)
            thinking_level = str(payload.get("thinking_level", "")).strip()
            if thinking_level:
                self._set_footer_thinking_level(thinking_level)
            project_dir = str(payload.get("project_dir", "")).strip()
            if project_dir:
                self._remote_project_dir = project_dir
                footer.set_cwd(project_dir)
            return
        session = payload.get("session", {})
        self._apply_remote_session_state(session)

    async def _restore_initial_remote_session(self) -> None:
        if self._resume_id:
            await self._resume_remote_session(self._resume_id)
            return
        if self._continue_session:
            try:
                payload = await self._remote_control().list_sessions()
            except Exception as exc:
                self._add_message(f"Failed to load remote sessions: {exc}", role="error")
            else:
                sessions = payload.get("sessions", [])
                if sessions:
                    session_id = str(sessions[0].get("id", "")).strip()
                    if session_id:
                        await self._resume_remote_session(session_id)
                        return
        await self._sync_remote_session_state()
        await self._sync_remote_extension_commands()

    def _set_remote_extension_commands(self, commands: list[Any]) -> None:
        normalized: set[str] = set()
        for command in commands:
            name = str(command).strip()
            if name:
                normalized.add(name)
        self._remote_extension_commands = normalized

    async def _sync_remote_extension_commands(self) -> None:
        if not self.remote_url:
            return
        try:
            payload = await self._remote_control().list_session_commands(self._remote_session_id)
        except Exception:
            self._remote_extension_commands = set()
            return
        self._set_remote_extension_commands(payload.get("commands", []))

    async def _maybe_handle_remote_extension_command(self, cmd_name: str, arg: str) -> bool:
        if not self.remote_url:
            return False
        if cmd_name not in self._remote_extension_commands:
            await self._sync_remote_extension_commands()
        if cmd_name not in self._remote_extension_commands:
            return False
        try:
            payload = await self._remote_control().run_session_command(
                self._remote_session_id,
                cmd_name,
                arg,
            )
        except Exception as exc:
            self._add_message(f"Command error: {exc}", role="error")
            return True
        session = payload.get("session")
        if isinstance(session, dict):
            self._apply_remote_session_state(session)
        output = payload.get("output")
        if output:
            self._add_message(str(output), role="tool")
        return True

    async def _forward_remote_credentials(self, config: Any) -> None:
        exports, skipped = await collect_forward_credentials(
            self._forward_credentials_spec,
            config,
        )
        if not exports and not skipped:
            return

        if exports:
            try:
                result = await self._remote_control().import_credentials(exports)
            except Exception as exc:
                self._add_message(f"Credential forwarding failed: {exc}", role="error")
            else:
                imported = result.get("imported", [])
                if imported:
                    providers = ", ".join(
                        item.get("provider", "") for item in imported if item.get("provider")
                    )
                    if providers:
                        self._add_message(
                            f"Forwarded remote credentials: {providers}",
                            role="tool",
                        )
        for item in skipped:
            self._add_message(
                f"Skipped forwarding {item.provider}: {item.reason}",
                role="tool",
            )

    async def _load_tui_extensions(self, config: Any) -> None:
        """Load TUI extensions and wire their widgets/keybindings into the app."""
        context = ExtensionContext(project_dir=os.getcwd(), runtime="tui", config=config)
        self._tui_extensions = await load_tui_extensions_async(context=context)
        for ext in self._tui_extensions:
            with suppress(Exception):
                await ext.mount(self)
            self._register_tui_extension_keybindings(ext)

    async def _mount_builtin_delegation_widget(self) -> None:
        from artel_tui.delegation_widget import DelegationStatusWidget

        main = self.query_one("#main-content")
        input_bar = self.query_one("#input-bar")
        with suppress(Exception):
            await main.mount(DelegationStatusWidget(self), before=input_bar)

    def _register_tui_extension_keybindings(self, ext: Any) -> None:
        """Bind dynamic keybindings exported by TUI extensions."""
        for index, (key, handler) in enumerate(ext.get_keybindings().items()):
            ext_name = getattr(ext, "name", "") or ext.__class__.__name__.lower()
            action_name = f"ext_{ext_name}_{index}"

            async def _action(self: ArtelApp, _handler: Callable[..., Any] = handler) -> None:
                try:
                    result = _handler(self)
                except TypeError:
                    result = _handler()
                if inspect.isawaitable(result):
                    await result

            setattr(self, f"action_{action_name}", MethodType(_action, self))
            self.bind(key, action_name, description=action_name)

    def _command_menu(self) -> OptionList:
        return self.query_one("#command-suggestions", OptionList)

    def _command_menu_visible(self) -> bool:
        menu = self._command_menu()
        return menu.has_class("visible") and menu.option_count > 0

    def _show_command_menu(self) -> None:
        self._command_menu().add_class("visible")

    def _hide_command_menu(self) -> None:
        menu = self._command_menu()
        menu.remove_class("visible")
        if menu.option_count:
            menu.clear_options()

    def _truncate_command_description(self, description: str, limit: int = 44) -> str:
        clean = " ".join(description.split())
        if len(clean) <= limit:
            return clean
        return clean[: limit - 1] + "…"

    def _current_thinking_level(self) -> str:
        if self._session is not None:
            return str(self._session.thinking_level).strip().lower()
        try:
            footer = self.query_one("#status-footer", StatusFooter)
        except Exception:
            return ""
        return str(getattr(footer, "_thinking_level", "")).strip().lower()

    def _current_project_value(self) -> str:
        if self.remote_url:
            return self._remote_project_dir.strip()
        return os.getcwd()

    def _current_theme_value(self) -> str:
        return self._active_theme.strip().lower()

    def _current_connect_provider(self) -> str:
        model = self._provider_model.strip().lower()
        if "/" in model:
            return model.split("/", 1)[0]
        return ""

    def _is_current_model(self, model_ref: str) -> bool:
        return model_ref.strip().lower() == self._provider_model.strip().lower()

    def _thinking_levels(self) -> tuple[str, ...]:
        return ("off", "minimal", "low", "medium", "high", "xhigh")

    def _available_theme_names(self) -> list[str]:
        from artel_tui.themes import list_themes

        return list_themes(os.getcwd())

    def _provider_ids_for_autocomplete(self) -> list[str]:
        config = load_config(os.getcwd())
        return _provider_ids_for_listing(config)

    def _unquote_path_prefix(self, prefix: str) -> tuple[str, bool]:
        stripped = prefix.lstrip()
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
            return stripped[1:-1], True
        if stripped[:1] in {"'", '"'}:
            return stripped[1:], True
        return prefix, False

    def _quote_completion_path(self, path: str, *, force: bool = False) -> str:
        if force or any(char.isspace() for char in path):
            return shlex.quote(path)
        return path

    def _project_path_suggestions(self, prefix: str) -> list[tuple[str, str]]:
        raw_prefix, was_quoted = self._unquote_path_prefix(prefix)
        expanded = os.path.expanduser(raw_prefix) if raw_prefix else ""
        if expanded:
            base_dir = expanded if os.path.isdir(expanded) else os.path.dirname(expanded)
            partial = os.path.basename(expanded)
            if not base_dir:
                base_dir = "."
        else:
            base_dir = os.getcwd()
            partial = ""

        try:
            entries = sorted(
                (entry for entry in os.scandir(base_dir) if entry.is_dir()),
                key=lambda entry: entry.name.lower(),
            )
        except OSError:
            return []

        suggestions: list[tuple[str, str]] = []
        for entry in entries:
            if partial and partial.lower() not in entry.name.lower():
                continue
            full_path = os.path.abspath(os.path.join(base_dir, entry.name))
            display_path = os.path.expanduser(full_path)
            if raw_prefix.startswith("~"):
                home = str(Path.home())
                if display_path.startswith(home):
                    display_path = "~" + display_path[len(home) :]
            completion_path = self._quote_completion_path(display_path, force=was_quoted)
            suggestions.append((display_path, completion_path))
            if len(suggestions) >= 25:
                break
        return suggestions

    async def _ensure_model_autocomplete_data(self) -> None:
        if self._model_autocomplete_loaded or self._model_autocomplete_loading:
            return
        self._model_autocomplete_loading = True
        try:
            refs: list[str] = []
            descriptions: dict[str, str] = {}
            if self.remote_url:
                try:
                    payload = await self._remote_control().list_models()
                except Exception:
                    providers = []
                else:
                    providers = payload.get("providers", [])
                for provider in providers:
                    provider_id = str(provider.get("id", "")).strip()
                    provider_name = str(provider.get("name", provider_id)).strip() or provider_id
                    if not provider_id:
                        continue
                    for model in provider.get("models", []):
                        model_id = str(model.get("id", "")).strip()
                        if not model_id:
                            continue
                        ref = f"{provider_id}/{model_id}"
                        refs.append(ref)
                        model_name = str(model.get("name", model_id)).strip() or model_id
                        context_window = model.get("context_window") or 0
                        ctx = f", {context_window // 1000}k ctx" if context_window else ""
                        descriptions[ref] = f"{provider_name} — {model_name}{ctx}"
            else:
                from artel_core.cli import _resolve_api_key

                config = load_config(os.getcwd())
                catalog = await get_effective_provider_catalog(config)
                for provider_id, provider in catalog.items():
                    requires_key = provider_requires_api_key(config, provider_id)
                    api_key, _ = await _resolve_api_key(config, provider_id)
                    if not api_key and requires_key:
                        continue
                    for model in provider.models:
                        ref = f"{provider_id}/{model.id}"
                        refs.append(ref)
                        ctx = (
                            f", {model.context_window // 1000}k ctx" if model.context_window else ""
                        )
                        descriptions[ref] = f"{provider.name} — {model.name}{ctx}"
            self._model_autocomplete_refs = sorted(set(refs))
            self._model_autocomplete_descriptions = descriptions
            self._model_autocomplete_loaded = True
        finally:
            self._model_autocomplete_loading = False

    async def _ensure_resume_autocomplete_data(self) -> None:
        if self._resume_autocomplete_loaded or self._resume_autocomplete_loading:
            return
        self._resume_autocomplete_loading = True
        try:
            suggestions: list[SlashCommandSuggestion] = []
            if self.remote_url:
                try:
                    payload = await self._remote_control().list_sessions()
                except Exception:
                    sessions = []
                else:
                    sessions = payload.get("sessions", [])
                for index, session in enumerate(sessions, start=1):
                    session_id = str(session.get("id", "")).strip()
                    if not session_id:
                        continue
                    title = str(session.get("title", "")).strip() or "(untitled)"
                    model = str(session.get("model", "")).strip() or "remote"
                    project_dir = str(session.get("project_dir", "")).strip()
                    project_hint = f" @ {project_dir}" if project_dir else ""
                    description = f"{title} ({model}){project_hint}"
                    suggestions.append(
                        SlashCommandSuggestion(
                            str(index),
                            description,
                            completion=f"/resume {index}",
                            search_text=(
                                f"{index} {session_id} {title} {model} {project_dir}".lower()
                            ),
                        )
                    )
                    suggestions.append(
                        SlashCommandSuggestion(
                            session_id,
                            description,
                            completion=f"/resume {session_id}",
                            search_text=(
                                f"{session_id} {index} {title} {model} {project_dir}".lower()
                            ),
                        )
                    )
            elif self._store is not None:
                try:
                    sessions = await self._store.list_sessions(limit=20)
                except Exception:
                    sessions = []
                home = str(Path.home())
                for index, session in enumerate(sessions, start=1):
                    title = session.title or "(untitled)"
                    project_dir = session.project_dir
                    if project_dir.startswith(home):
                        project_dir = "~" + project_dir[len(home) :]
                    project_hint = f" @ {project_dir}" if project_dir else ""
                    description = f"{title} ({session.model}){project_hint}"
                    suggestions.append(
                        SlashCommandSuggestion(
                            str(index),
                            description,
                            completion=f"/resume {index}",
                            search_text=(
                                f"{index} {session.id} {title} "
                                f"{session.model} {project_dir}".lower()
                            ),
                        )
                    )
                    suggestions.append(
                        SlashCommandSuggestion(
                            session.id,
                            description,
                            completion=f"/resume {session.id}",
                            search_text=(
                                f"{session.id} {index} {title} "
                                f"{session.model} {project_dir}".lower()
                            ),
                        )
                    )
            deduped: list[SlashCommandSuggestion] = []
            seen: set[str] = set()
            for suggestion in suggestions:
                key = suggestion.completion or suggestion.value
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(suggestion)
            self._resume_autocomplete_suggestions = deduped
            self._resume_autocomplete_loaded = True
        finally:
            self._resume_autocomplete_loading = False

    async def _ensure_fork_autocomplete_data(self) -> None:
        if self._fork_autocomplete_loaded or self._fork_autocomplete_loading:
            return
        self._fork_autocomplete_loading = True
        try:
            nodes: list[dict[str, Any]] = []
            if self.remote_url:
                try:
                    payload = await self._remote_control().get_session_tree(self._remote_session_id)
                except Exception:
                    nodes = []
                else:
                    raw_nodes = payload.get("nodes", [])
                    nodes = raw_nodes if isinstance(raw_nodes, list) else []
            elif self._store is not None and self._session is not None:
                try:
                    nodes = await self._store.get_message_nodes(self._session.session_id)
                except Exception:
                    nodes = []
            suggestions: list[SlashCommandSuggestion] = []
            for index, node in enumerate(nodes):
                role = str(node.get("role", "")).strip() or "message"
                content = str(node.get("content", "") or "").replace("\n", " ").strip()
                preview = content[:60] + ("…" if len(content) > 60 else "")
                description = f"[{role}] {preview}" if preview else f"[{role}]"
                suggestions.append(
                    SlashCommandSuggestion(
                        str(index),
                        description,
                        completion=f"/fork {index}",
                        search_text=f"{index} {role} {content}".lower(),
                    )
                )
            self._fork_autocomplete_suggestions = suggestions
            self._fork_autocomplete_loaded = True
        finally:
            self._fork_autocomplete_loading = False

    def _image_path_suggestions(self, prefix: str) -> list[tuple[str, str]]:
        raw_prefix, was_quoted = self._unquote_path_prefix(prefix)
        expanded = os.path.expanduser(raw_prefix) if raw_prefix else ""
        if expanded:
            base_dir = expanded if os.path.isdir(expanded) else os.path.dirname(expanded)
            partial = os.path.basename(expanded)
            if not base_dir:
                base_dir = "."
        else:
            base_dir = os.getcwd()
            partial = ""

        try:
            entries = sorted(os.scandir(base_dir), key=lambda entry: entry.name.lower())
        except OSError:
            return []

        suggestions: list[tuple[str, str]] = []
        for entry in entries:
            if partial and partial.lower() not in entry.name.lower():
                continue
            full_path = os.path.abspath(os.path.join(base_dir, entry.name))
            if entry.is_dir():
                continue
            if not is_supported_image_path(full_path):
                continue
            display_path = os.path.expanduser(full_path)
            if raw_prefix.startswith("~"):
                home = str(Path.home())
                if display_path.startswith(home):
                    display_path = "~" + display_path[len(home) :]
            completion_path = self._quote_completion_path(display_path, force=was_quoted)
            suggestions.append((display_path, completion_path))
            if len(suggestions) >= 25:
                break
        return suggestions

    def _pending_attachment_index_suggestions(self) -> list[SlashCommandSuggestion]:
        suggestions: list[SlashCommandSuggestion] = []
        for index, attachment in enumerate(self._pending_attachments, start=1):
            suggestions.append(
                SlashCommandSuggestion(
                    str(index),
                    attachment.name or Path(attachment.path).name,
                    completion=f"/image-remove {index}",
                )
            )
        return suggestions

    def _command_argument_suggestions(self, text: str) -> list[SlashCommandSuggestion]:
        stripped = text.lstrip()
        if not stripped.startswith("/"):
            return []
        body = stripped[1:]
        if not body:
            return []
        if " " not in body:
            return []
        cmd_name, raw_arg = body.split(" ", 1)
        cmd = f"/{cmd_name.lower()}"
        arg = raw_arg.lstrip()

        if cmd == "/thinking":
            current_thinking = self._current_thinking_level()
            return [
                SlashCommandSuggestion(
                    level,
                    f"set thinking to {level}",
                    completion=f"/thinking {level}",
                    current=(level == current_thinking),
                )
                for level in self._thinking_levels()
                if level.startswith(arg.lower())
            ]

        if cmd == "/theme":
            current_theme = self._current_theme_value()
            return [
                SlashCommandSuggestion(
                    theme,
                    "switch theme",
                    completion=f"/theme {theme}",
                    current=(theme.lower() == current_theme),
                )
                for theme in self._available_theme_names()
                if theme.lower().startswith(arg.lower())
            ]

        if cmd == "/connect":
            current_provider = self._current_connect_provider()
            return [
                SlashCommandSuggestion(
                    provider_id,
                    "connect provider",
                    completion=f"/connect {provider_id}",
                    current=(provider_id.lower() == current_provider),
                )
                for provider_id in self._provider_ids_for_autocomplete()
                if provider_id.lower().startswith(arg.lower())
            ]

        if cmd == "/resume":
            lowered = arg.lower()
            ranked = [
                suggestion
                for suggestion in self._resume_autocomplete_suggestions
                if (suggestion.search_text or suggestion.value.lower()).find(lowered) >= 0
            ]
            ranked.sort(
                key=lambda suggestion: (
                    0 if suggestion.value.isdigit() else 1,
                    0
                    if (
                        suggestion.value.lower().startswith(lowered)
                        or (suggestion.search_text or "").startswith(lowered)
                    )
                    else 1,
                    suggestion.value.lower(),
                )
            )
            return ranked

        if cmd in {"/project", "/cd"}:
            current_project = self._current_project_value()
            return [
                SlashCommandSuggestion(
                    display_path,
                    "change project directory",
                    completion=f"{cmd} {completion_path}",
                    search_text=display_path.lower(),
                    current=(
                        os.path.abspath(os.path.expanduser(display_path))
                        == os.path.abspath(os.path.expanduser(current_project))
                    ),
                )
                for display_path, completion_path in self._project_path_suggestions(arg)
            ]

        if cmd == "/image":
            return [
                SlashCommandSuggestion(
                    display_path,
                    "attach image",
                    completion=f"/image {completion_path}",
                    search_text=display_path.lower(),
                )
                for display_path, completion_path in self._image_path_suggestions(arg)
            ]

        if cmd == "/image-remove":
            return [
                suggestion
                for suggestion in self._pending_attachment_index_suggestions()
                if suggestion.value.startswith(arg)
            ]

        if cmd == "/fork":
            lowered = arg.lower()
            ranked = [
                suggestion
                for suggestion in self._fork_autocomplete_suggestions
                if (suggestion.search_text or suggestion.value.lower()).find(lowered) >= 0
            ]
            ranked.sort(
                key=lambda suggestion: (
                    0
                    if (
                        suggestion.value.startswith(arg)
                        or (suggestion.search_text or "").startswith(lowered)
                    )
                    else 1,
                    int(suggestion.value) if suggestion.value.isdigit() else 10**9,
                )
            )
            return ranked

        if cmd == "/browser":
            browser_hints = ["https://", "http://", "about:blank"]
            return [
                SlashCommandSuggestion(
                    hint,
                    "open browser pane",
                    completion=f"/browser {hint}",
                )
                for hint in browser_hints
                if hint.startswith(arg.lower())
            ]

        if cmd == "/model":
            current_model = self._provider_model.strip().lower()
            current_provider = current_model.split("/", 1)[0] if "/" in current_model else ""
            lowered = arg.lower()
            if "/" not in arg:
                provider_ids = self._provider_ids_for_autocomplete()
                provider_matches: list[str] = []
                for provider_id in provider_ids:
                    provider_lower = provider_id.lower()
                    if lowered in provider_lower:
                        provider_matches.append(provider_id)
                        continue
                    if any(
                        ref.lower().startswith(provider_lower + "/")
                        and (
                            lowered in ref.lower()
                            or lowered in self._model_autocomplete_descriptions.get(ref, "").lower()
                        )
                        for ref in self._model_autocomplete_refs
                    ):
                        provider_matches.append(provider_id)
                provider_matches = list(dict.fromkeys(provider_matches))
                provider_matches.sort(
                    key=lambda provider_id: (
                        0 if provider_id.lower() == current_provider else 1,
                        0 if provider_id.lower().startswith(lowered) else 1,
                        provider_id.lower(),
                    )
                )
                return [
                    SlashCommandSuggestion(
                        f"{provider_id}/",
                        "select provider",
                        completion=f"/model {provider_id}/",
                        search_text=provider_id.lower(),
                        current=(provider_id.lower() == current_provider),
                    )
                    for provider_id in provider_matches
                ]
            model_matches = [
                ref
                for ref in self._model_autocomplete_refs
                if lowered in ref.lower()
                or lowered in self._model_autocomplete_descriptions.get(ref, "").lower()
            ]
            model_matches.sort(
                key=lambda ref: (
                    0 if ref.lower() == current_model else 1,
                    0 if ref.lower().startswith(lowered) else 1,
                    0 if ref.lower().startswith(current_provider + "/") else 1,
                    ref.lower(),
                )
            )
            return [
                SlashCommandSuggestion(
                    ref,
                    self._model_autocomplete_descriptions.get(ref, "switch model"),
                    completion=f"/model {ref}",
                    search_text=(
                        ref + " " + self._model_autocomplete_descriptions.get(ref, "")
                    ).lower(),
                    current=self._is_current_model(ref),
                )
                for ref in model_matches
            ]

        return []

    def _command_suggestions(self) -> list[SlashCommandSuggestion]:
        suggestions = list(BUILTIN_COMMAND_SUGGESTIONS)

        for name, template in sorted(self._prompts.items()):
            suggestions.append(
                SlashCommandSuggestion(
                    f"/{name}",
                    self._truncate_command_description(template),
                )
            )

        if self._skills:
            for skill in sorted(self._skills.values(), key=lambda item: item.name):
                description = getattr(skill, "description", "") or "load skill"
                suggestions.append(
                    SlashCommandSuggestion(
                        f"/skill:{skill.name}",
                        self._truncate_command_description(description),
                    )
                )

        if self._session:
            for name in sorted(self._session.hooks.commands):
                suggestions.append(SlashCommandSuggestion(f"/{name}", "extension command"))
        elif self.remote_url:
            for name in sorted(self._remote_extension_commands):
                suggestions.append(SlashCommandSuggestion(f"/{name}", "remote extension command"))

        deduped: list[SlashCommandSuggestion] = []
        seen: set[str] = set()
        for suggestion in suggestions:
            if suggestion.value in seen:
                continue
            seen.add(suggestion.value)
            deduped.append(suggestion)
        return deduped

    def _matching_command_suggestions(self, text: str) -> list[SlashCommandSuggestion]:
        query = text.strip().lower()
        if not query.startswith("/"):
            return []
        if " " in query:
            return self._command_argument_suggestions(text)
        return [
            suggestion
            for suggestion in self._command_suggestions()
            if suggestion.value.lower().startswith(query)
        ]

    def _update_command_menu(self, text: str) -> None:
        menu = self._command_menu()
        matches = self._matching_command_suggestions(text)
        if not matches:
            self._hide_command_menu()
            return

        options = [
            Option(
                (
                    f"✓ {suggestion.value} — "
                    f"{self._truncate_command_description(suggestion.description)}"
                )
                if getattr(suggestion, "current", False)
                else (
                    f"{suggestion.value} — "
                    f"{self._truncate_command_description(suggestion.description)}"
                ),
                id=suggestion.completion or suggestion.value,
            )
            for suggestion in matches
        ]
        menu.clear_options()
        menu.add_options(options)
        menu.highlighted = 0
        menu.scroll_home(animate=False)
        self._show_command_menu()

    def _selected_command_suggestion(self) -> str | None:
        menu = self._command_menu()
        highlighted = menu.highlighted
        if highlighted is None or menu.option_count == 0:
            return None
        option = menu.get_option_at_index(highlighted)
        return option.id

    def _move_command_suggestion(self, delta: int) -> None:
        menu = self._command_menu()
        if not self._command_menu_visible():
            return
        highlighted = menu.highlighted if menu.highlighted is not None else 0
        highlighted = max(0, min(menu.option_count - 1, highlighted + delta))
        menu.highlighted = highlighted
        menu.scroll_to_highlight()

    def _apply_command_suggestion(
        self,
        command: str | None = None,
        *,
        only_if_completion_needed: bool = False,
    ) -> bool:
        input_bar = self._composer()
        command = command or self._selected_command_suggestion()
        if not command:
            return False
        if only_if_completion_needed and input_bar.text.strip() == command:
            return False
        self._suppress_next_command_menu_update = True

        self._set_composer_text(command)
        self._hide_command_menu()
        self.call_after_refresh(self._focus_input)
        return True

    def on_key(self, event: events.Key) -> None:
        input_bar = self._composer()
        if not input_bar.has_focus or not self._command_menu_visible():
            return

        if event.key == "down":
            self._move_command_suggestion(1)
        elif event.key == "up":
            self._move_command_suggestion(-1)
        elif event.key == "tab":
            if not self._apply_command_suggestion():
                return
        elif event.key == "escape":
            self._hide_command_menu()
        else:
            return

        event.stop()
        event.prevent_default()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "input-bar":
            if self._suppress_next_command_menu_update:
                self._suppress_next_command_menu_update = False
                self._hide_command_menu()
                return
            self._update_command_menu(event.text_area.text)
            normalized = event.text_area.text.strip().lower()
            if normalized.startswith("/model "):
                self._schedule_background_task(
                    self._ensure_model_autocomplete_data,
                    exclusive=True,
                    thread=False,
                )
            if normalized.startswith("/resume "):
                self._schedule_background_task(
                    self._ensure_resume_autocomplete_data,
                    exclusive=True,
                    thread=False,
                )
            if normalized.startswith("/fork "):
                self._schedule_background_task(
                    self._ensure_fork_autocomplete_data,
                    exclusive=True,
                    thread=False,
                )
            return

        if self._suspend_board_editor_events:
            return
        if event.text_area.id == "tasks-editor":
            self._schedule_tasks_save(event.text_area.text)
        elif event.text_area.id == "notes-editor":
            self._schedule_notes_save(event.text_area.text)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "command-suggestions":
            return
        self._apply_command_suggestion(event.option.id)
        event.stop()

    async def _init_local_session(self) -> None:
        from artel_core.cli import _resolve_api_key

        config = load_config(os.getcwd())
        provider_name, model_id = resolve_model(config)
        project_dir = os.getcwd()

        # Session store
        self._store = SessionStore(config.sessions.db_path)
        await self._store.open()

        # Resolve session (resume or new)
        session_id = ""
        prior_messages = None
        resumed_info: Any = None

        if self._resume_id:
            info = await self._store.get_session(self._resume_id)
            if info:
                session_id = info.id
                prior_messages = await self._store.get_messages(session_id)
                resumed_info = info
        elif self._continue_session:
            last = await self._store.get_last_session()
            if last:
                session_id = last.id
                prior_messages = await self._store.get_messages(session_id)
                resumed_info = last

        # Use session's model if available (provider/model format)
        if resumed_info and resumed_info.model and "/" in resumed_info.model:
            provider_name, model_id = resumed_info.model.split("/", 1)

        runtime = await bootstrap_runtime(
            config,
            provider_name,
            model_id,
            project_dir=project_dir,
            resolve_api_key=_resolve_api_key,
            include_extensions=True,
            runtime="tui",
        )
        self._extensions = runtime.extensions
        self._input_price = runtime.input_price_per_m
        self._output_price = runtime.output_price_per_m

        if not session_id:
            session_id = str(uuid.uuid4())
            await self._store.create_session(
                session_id,
                f"{provider_name}/{model_id}",
                project_dir=project_dir,
                thinking_level=config.agent.thinking,
            )
        self._session = create_agent_session_from_bootstrap(
            config,
            runtime,
            project_dir=project_dir,
            store=self._store,
            session_id=session_id,
            permission_callback=self._ask_permission,
        )
        self._session.rule_overrides = self._local_rule_overrides
        self._session.refresh_system_prompt()
        self._session.board_event_callback = self._handle_board_tool_event  # type: ignore[attr-defined]
        if resumed_info and resumed_info.thinking_level:
            self._session.thinking_level = resumed_info.thinking_level  # type: ignore[assignment]

        # Restore prior messages and display them
        self._tool_call_names.clear()
        if prior_messages:
            self._session.messages.extend(prior_messages)
            for msg in prior_messages:
                self._render_restored_message(
                    role=msg.role.value,
                    content=msg.content,
                    reasoning=msg.reasoning or "",
                    attachments=msg.attachments,
                    tool_calls=[tool_call.model_dump() for tool_call in msg.tool_calls]
                    if msg.tool_calls
                    else None,
                    tool_result=msg.tool_result.model_dump()
                    if msg.tool_result is not None
                    else None,
                )

        self._provider_model = f"{provider_name}/{model_id}"
        self.sub_title = self._provider_model
        self.query_one("#status-footer", StatusFooter).set_model(self._provider_model)
        await self._sync_cmux_session_metadata()
        current_thinking = (
            self._session.thinking_level if self._session is not None else config.agent.thinking
        )
        self._set_footer_thinking_level(str(current_thinking))

    async def action_submit_composer(self) -> None:
        composer = self._composer()
        await self._submit_text(composer.text, clear_widget=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        await self._submit_text(event.value, clear_widget=False)

    async def on_tree_node_selected(self, event: Tree.NodeSelected[ServerDockNodeData]) -> None:
        if event.node.tree.id != "server-dock-tree":
            return
        data = event.node.data
        if not isinstance(data, ServerDockNodeData):
            return
        await self._handle_server_dock_selection(data)

    async def on_server_dock_action_requested(self, message: ServerDockActionRequested) -> None:
        node = message.node
        data = getattr(node, "data", None)
        if not isinstance(data, ServerDockNodeData):
            return
        tree = self._server_dock().tree()
        tree.move_cursor(node)
        await self._open_server_dock_actions(data)

    async def on_dock_action_invoked(self, message: DockActionInvoked) -> None:
        data = self._server_dock_selected_data()
        if data is None:
            self._close_server_dock_actions()
            self._set_server_dock_status("Select a server, project, or session first")
            return
        if self._server_dock_action_requires_confirmation(message.action):
            self._server_dock_action_panel().request_confirmation(
                message.action,
                self._server_dock_confirmation_prompt(data, message.action),
            )
            self._set_server_dock_status("Confirm destructive action or cancel")
            return
        self._close_server_dock_actions()
        await self._run_server_dock_action(data, message.action)

    async def on_dock_action_confirmed(self, message: DockActionConfirmed) -> None:
        data = self._server_dock_selected_data()
        if data is None:
            self._close_server_dock_actions()
            self._set_server_dock_status("Select a server, project, or session first")
            return
        self._close_server_dock_actions()
        await self._run_server_dock_action(data, message.action)

    def on_dock_action_closed(self, _: DockActionClosed) -> None:
        self._close_server_dock_actions()
        self._focus_input()

    async def on_dock_input_submitted(self, message: DockInputSubmitted) -> None:
        self._close_server_dock_input()
        value = message.value.strip()
        if message.mode == "add_server":
            if not value:
                self._add_message("Server add cancelled.", role="tool")
                return
            await self._connect_to_server(
                value,
                auth_token=self.auth_token if self.remote_url == value else "",
                save=True,
            )
            return

    def on_dock_input_closed(self, _: DockInputClosed) -> None:
        self._close_server_dock_input()
        self._focus_input()

    async def on_inline_input_submitted(self, message: InlineInputSubmitted) -> None:
        self._close_inline_input()
        value = message.value.strip()
        if message.mode == "remote_oauth_code":
            pending = self._pending_remote_oauth or {}
            self._pending_remote_oauth = None
            if not value:
                self._add_message("Remote login cancelled.", role="tool")
                return
            try:
                await self._remote_control().complete_oauth(
                    str(pending.get("login_id", "")),
                    {"code": value},
                )
            except Exception as exc:
                self._add_message(f"Remote login failed: {exc}", role="error")
                return
            provider_name = str(pending.get("provider_name", "")).strip() or "Provider"
            self._add_message(
                f"{provider_name.capitalize()} authorized on the remote server!",
                role="tool",
            )

    def on_inline_input_closed(self, message: InlineInputClosed) -> None:
        self._close_inline_input()
        if message.mode == "remote_oauth_code":
            self._pending_remote_oauth = None
            self._add_message("Remote login cancelled.", role="tool")
            return
        self._focus_input()

    async def on_rule_editor_submitted(self, message: RuleEditorSubmitted) -> None:
        payload = message.payload
        existing = self._pending_rule_editor_existing
        self._pending_rule_editor_existing = None
        self._close_inline_rule_editor()
        if not payload:
            self._add_message("Rule edit cancelled.", role="tool")
            return
        try:
            if self.remote_url:
                if existing is None:
                    response = await self._remote_control().add_rule(
                        scope=str(payload.get("scope", "project")),
                        text=str(payload.get("text", "")),
                        project_dir=self._current_rules_project_dir(),
                        enabled=bool(payload.get("enabled", True)),
                    )
                    rule_id_value = str(response.get("rule", {}).get("id", ""))
                    self._add_message(f"Added rule {rule_id_value}.", role="tool")
                else:
                    existing_id = (
                        str(existing.get("id", "")) if isinstance(existing, dict) else existing.id
                    )
                    response = await self._remote_control().edit_rule(
                        existing_id,
                        project_dir=self._current_rules_project_dir(),
                        text=str(payload.get("text", "")),
                        scope=str(payload.get("scope", "project")),
                        enabled=bool(payload.get("enabled", True)),
                    )
                    rule_id_value = str(response.get("rule", {}).get("id", existing_id))
                    self._add_message(f"Updated rule {rule_id_value}.", role="tool")
                return
            if existing is None:
                rule = add_rule(
                    scope=str(payload.get("scope", "project")),
                    text=str(payload.get("text", "")),
                    project_dir=self._current_rules_project_dir(),
                    enabled=bool(payload.get("enabled", True)),
                )
                self._add_message(f"Added rule {rule.id}.", role="tool")
            else:
                existing_id = existing.id
                rule = update_rule(
                    existing_id,
                    project_dir=self._current_rules_project_dir(),
                    text=str(payload.get("text", "")),
                    scope=str(payload.get("scope", existing.scope)),
                    enabled=bool(payload.get("enabled", existing.enabled)),
                )
                self._add_message(f"Updated rule {rule.id}.", role="tool")
            if self._session is not None:
                self._session.refresh_system_prompt()
        except Exception as exc:
            self._add_message(f"Failed to save rule: {exc}", role="error")

    def on_rule_editor_closed(self, _: RuleEditorClosed) -> None:
        self._pending_rule_editor_existing = None
        self._close_inline_rule_editor()
        self._add_message("Rule edit cancelled.", role="tool")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "server-dock-add":
            event.stop()
            detail = (
                "Enter ws:// or wss:// URL. "
                "The active auth token will be reused if available."
            )
            self._open_server_dock_input(
                mode="add_server",
                title="Add Artel server",
                detail=detail,
                placeholder="ws://host:7432",
            )
            return
        if button_id == "server-dock-refresh":
            event.stop()
            await self._refresh_server_dock()
            return
        if button_id == "server-dock-hide":
            event.stop()
            self.action_toggle_server_dock()
            return

    def _open_inline_input(
        self,
        *,
        mode: str,
        title: str,
        detail: str,
        placeholder: str = "",
    ) -> None:
        self._inline_rule_editor_panel().close()
        self._inline_input_panel().open(
            mode=mode,
            title=title,
            detail=detail,
            placeholder=placeholder,
        )

    def _close_inline_input(self) -> None:
        with suppress(Exception):
            self._inline_input_panel().close()

    def _open_inline_rule_editor(
        self,
        *,
        title: str,
        text: str = "",
        scope: str = "project",
        enabled: bool = True,
    ) -> None:
        self._close_inline_input()
        self._inline_rule_editor_panel().open(
            title=title,
            text=text,
            scope=scope,
            enabled=enabled,
        )

    def _close_inline_rule_editor(self) -> None:
        with suppress(Exception):
            self._inline_rule_editor_panel().close()

    async def _submit_text(self, raw_text: str, *, clear_widget: bool) -> None:
        text = raw_text.strip()
        attachments = self._consume_pending_attachments()
        if not text and not attachments:
            return
        if self._command_menu_visible() and self._apply_command_suggestion(
            only_if_completion_needed=True
        ):
            return

        if attachments and not await self._model_supports_vision():
            self._pending_attachments = attachments + self._pending_attachments
            self._add_message("Current model does not support image input.", role="error")
            return

        if clear_widget:
            self._clear_composer()
        self._hide_command_menu()
        self.call_after_refresh(self._focus_input)

        # Handle bash commands: !! = local only, ! = send output to LLM
        if text.startswith("!!"):
            if attachments:
                self._pending_attachments = attachments + self._pending_attachments
                self._add_message(
                    "Image attachments are only supported for normal chat messages.", role="error"
                )
                return
            cmd = text[2:].strip()
            if cmd:
                self._add_message(f"$ {cmd}", role="user")
                if self.remote_url:
                    self._run_remote_bash(cmd, send_to_llm=False)
                else:
                    self._run_bash(cmd, send_to_llm=False)
            return
        if text.startswith("!"):
            if attachments:
                self._pending_attachments = attachments + self._pending_attachments
                self._add_message(
                    "Image attachments are only supported for normal chat messages.", role="error"
                )
                return
            cmd = text[1:].strip()
            if cmd:
                self._add_message(f"$ {cmd}", role="user")
                if self.remote_url:
                    self._run_remote_bash(cmd, send_to_llm=True)
                else:
                    self._run_bash(cmd, send_to_llm=True)
            return

        # Handle slash commands
        if text.startswith("/"):
            self._pending_attachments = attachments + self._pending_attachments
            await self._handle_command(text)
            return

        if self._run_busy:
            if attachments:
                self._pending_attachments = attachments + self._pending_attachments
                self._add_message(
                    "Image attachments are not supported for steering messages.", role="error"
                )
                return
            self._add_message(text, role="user")
            if self.remote_url:
                try:
                    await self._send_remote_event(
                        {
                            "type": "steer",
                            "content": text,
                            "session_id": self._remote_session_id,
                        }
                    )
                    self._add_message("Steering queued.", role="tool")
                except Exception as exc:
                    self._add_message(f"Failed to steer remote run: {exc}", role="error")
            elif self._session is not None:
                self._session.steer(text)
                self._add_message("Steering queued.", role="tool")
            return

        self._add_message(self._render_user_submission(text, attachments), role="user")

        # Auto-title session from first user message (async, non-blocking)
        if self._store and self._session and not text.startswith("/"):
            info = await self._store.get_session(self._session.session_id)
            if info and not info.title:
                self._generate_title(text)

        if self.remote_url:
            if attachments:
                self._run_remote(text, attachments=attachments)
            else:
                self._run_remote(text)
        else:
            if attachments:
                self._run_local(text, attachments=attachments)
            else:
                self._run_local(text)

    async def _handle_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in {"/clear", "/new"}:
            await self.action_clear()
        elif cmd == "/cancel":
            await self._cmd_cancel()
        elif cmd == "/quit":
            await self._cleanup()
            self.exit()
        elif cmd == "/help":
            self._add_message(
                "Commands:\n"
                "  /model              — show current model\n"
                "  /model <p/model>    — switch model\n"
                "  /models             — list all available models\n"
                "  /project            — show current project on the active host\n"
                "  /project <path>     — switch project/cwd on the active host\n"
                "  /cd <path>          — alias for /project <path>\n"
                "  /providers          — list supported providers and setup hints\n"
                "  /connect <provider> — login to a provider\n"
                "  /rules              — list configured rules\n"
                "  /rule add           — add a rule via inline editor\n"
                "  /rule edit <id>     — edit a rule via inline editor\n"
                "  /rule delete <id>   — delete a rule\n"
                "  /rule enable <id>   — enable a rule for this session\n"
                "  /rule disable <id>  — disable a rule for this session\n"
                "  /rule persist enable <id>  — enable a rule in storage\n"
                "  /rule persist disable <id> — disable a rule in storage\n"
                "  /rule move <id> up|down   — move a rule in precedence order\n"
                "  /rule move <id> to <n>    — move a rule to a 1-based position\n"
                "  /rule reset <id>    — reset a session rule override\n"
                "  /rule reset all     — reset all session rule overrides\n"
                "  /resume             — list and resume a session\n"
                "  /sessions           — list recent sessions\n"
                "  /compact [prompt]   — compact conversation history\n"
                "  /name <title>       — rename current session\n"
                "  /tree               — show session message tree\n"
                "  /fork [index]       — fork session from message index\n"
                "  /prompts            — list prompt templates\n"
                "  /skill:<name>       — load a skill into session\n"
                "  /skills             — list available skills\n"
                "  /thinking [level]   — set thinking (off/minimal/low/medium/high/xhigh)\n"
                "  /theme [name]       — switch theme (dark/light/monokai/dracula)\n"
                "  /export [file]      — export session to HTML\n"
                "  /reload             — hot-reload extensions, prompts, skills\n"
                "  /image <path>       — attach an image to the next message\n"
                "  /image-paste        — paste an image from the clipboard\n"
                "  /image-clear        — clear pending image attachments\n"
                "  /image-remove <n>   — remove one pending image attachment\n"
                "  /copy               — copy the last assistant message\n"
                "  /server-add         — add a server via inline dialog\n"
                "  /server-remove [x]  — remove a saved server by name/url\n"
                "  /server-select <x>  — connect to a saved server\n"
                "  /server-dock        — toggle the left server/project/session dock\n"
                "  /delegates [subcmd] — inspect delegated orchestration runs\n"
                "  /agents [subcmd]    — alias for /delegates\n"
                "  /mcp [reload]       — show MCP status or reload MCP connections\n"
                "  /schedules [subcmd] — list/run/reload scheduled tasks on the active server\n"
                "  /git [subcmd]       — first-class git status/diff/rollback helpers\n"
                "  /status             — alias for /git status\n"
                "  /diff [path]        — alias for /git diff [path]\n"
                "  /rollback <path>    — restore one file (or --all)\n"
                "  /undo               — restore files changed by the latest AI edit cycle\n"
                "  /rewind <index>     — fork+switch session to an earlier message index\n"
                "  /wt [branch]        — manage git worktrees for the current repository\n"
                "  /tasks              — show the shared task board\n"
                "  /task-add <title>   — add a task to the shared task board\n"
                "  /task-done <id>     — mark a task as done\n"
                "  /notes              — show operator notes\n"
                "  /notes-open         — focus the operator notes editor\n"
                "  /cancel             — cancel the active run\n"
                "  /server-restart     — restart managed local Artel server\n"
                "  /split [dir]        — open cmux split pane (cmux only)\n"
                "  /browser [url]      — open browser pane (cmux only)\n"
                "  /new                — start a new session in this window\n"
                "  /clear              — clear chat & start new session\n"
                "  /quit               — exit\n"
                "  ! <command>         — run cmd on the active host & send output to LLM\n"
                "  !! <command>        — run cmd on the active host\n"
                "  Ctrl+O              — toggle tool output\n"
                "  Ctrl+X              — open actions for selected server-dock item\n"
                "  Click the left ⋮    — open actions for a tree item\n"
                "  Ctrl+G              — toggle the left server dock\n"
                "  Ctrl+B              — toggle task board / notes sidebar\n"
                "  Ctrl+T              — focus tasks editor\n"
                "  Ctrl+N              — focus notes editor\n"
                "  Enter               — send composer contents\n"
                "  Shift+Enter         — insert new line\n"
                "  Ctrl+Shift+C        — copy last assistant reply",
                role="tool",
            )
        elif cmd == "/model":
            if arg:
                await self._switch_model(arg)
            else:
                if self.remote_url:
                    try:
                        payload = await self._remote_control().get_session(self._remote_session_id)
                        session = payload.get("session", {})
                        model = str(session.get("model", "")).strip() or "remote"
                    except Exception as exc:
                        self._add_message(f"Failed to load remote model: {exc}", role="error")
                        return
                else:
                    model = self._session.model if self._session else "remote"
                self._add_message(f"Current model: {model}", role="tool")
        elif cmd == "/models":
            self._list_models()
        elif cmd in ("/project", "/cd"):
            await self._cmd_project(arg)
        elif cmd == "/providers":
            await self._list_providers()
        elif cmd == "/rules":
            await self._cmd_rules()
        elif cmd == "/rule":
            await self._cmd_rule(arg)
        elif cmd == "/connect":
            if not arg:
                self._add_message(
                    "Usage: /connect <provider>  (see /providers for setup options)",
                    role="tool",
                )
            else:
                self._run_connect(arg)
        elif cmd in ("/resume", "/sessions"):
            await self._cmd_resume(arg)
        elif cmd == "/compact":
            await self._cmd_compact(arg)
        elif cmd == "/name":
            if not arg:
                self._add_message("Usage: /name <title>", role="error")
            else:
                await self._cmd_name(arg)
        elif cmd == "/tree":
            await self._cmd_tree()
        elif cmd == "/fork":
            await self._cmd_fork(arg)
        elif cmd == "/prompts":
            self._cmd_prompts()
        elif cmd.startswith("/skill:"):
            await self._cmd_skill(cmd[7:])  # strip "/skill:"
        elif cmd == "/skills":
            self._cmd_skills_list()
        elif cmd == "/theme":
            self._cmd_theme(arg)
        elif cmd == "/thinking":
            await self._cmd_thinking(arg)
        elif cmd == "/export":
            await self._cmd_export(arg)
        elif cmd == "/split":
            await self._cmd_split(arg)
        elif cmd == "/browser":
            await self._cmd_browser(arg)
        elif cmd == "/reload":
            await self._cmd_reload()
        elif cmd == "/image":
            await self._cmd_image(arg)
        elif cmd == "/image-paste":
            await self._cmd_image_paste()
        elif cmd == "/image-clear":
            self._cmd_image_clear()
        elif cmd == "/image-remove":
            self._cmd_image_remove(arg)
        elif cmd == "/copy":
            self.action_copy_last_assistant_message()
        elif cmd == "/server-add":
            await self._cmd_server_add(arg)
        elif cmd == "/server-remove":
            await self._cmd_server_remove(arg)
        elif cmd == "/server-select":
            await self._cmd_server_select(arg)
        elif cmd == "/server-dock":
            self.action_toggle_server_dock()
        elif cmd in {"/agents", "/delegates"}:
            await self._cmd_agents(arg)
        elif cmd == "/mcp":
            await self._cmd_mcp(arg)
        elif cmd == "/schedules":
            await self._cmd_schedules(arg)
        elif cmd in {"/git", "/status", "/diff", "/rollback"}:
            await self._cmd_git(cmd, arg)
        elif cmd == "/undo":
            await self._cmd_undo()
        elif cmd == "/rewind":
            await self._cmd_rewind(arg)
        elif cmd == "/wt":
            await self._cmd_wt(arg)
        elif cmd == "/tasks":
            await self._cmd_tasks()
        elif cmd == "/task-add":
            await self._cmd_task_add(arg)
        elif cmd == "/task-done":
            await self._cmd_task_done(arg)
        elif cmd == "/notes":
            await self._cmd_notes()
        elif cmd == "/notes-open":
            self.action_focus_notes()
        elif cmd == "/server-restart":
            await self._cmd_server_restart()
        else:
            # Check prompt templates as /name commands
            cmd_name = cmd.lstrip("/")
            if cmd_name in self._prompts:
                self._cmd_use_prompt(cmd_name, arg)
            elif await self._maybe_handle_remote_extension_command(cmd_name, arg):
                return
            # Check extension commands
            elif self._session and cmd_name in self._session.hooks.commands:
                handler = self._session.hooks.commands[cmd_name]
                try:
                    result = await handler(arg)
                    if result:
                        self._add_message(result, role="tool")
                except Exception as e:
                    self._add_message(f"Command error: {e}", role="error")
            else:
                self._add_message(f"Unknown command: {cmd}. Type /help for list.", role="error")

    async def _model_supports_vision(self) -> bool:
        try:
            config = load_config(os.getcwd())
            provider_name, model_id = resolve_model(config)
            model_ref = self._provider_model.strip()
            if "/" in model_ref:
                provider_name, model_id = model_ref.split("/", 1)
            model = await get_effective_model_info(config, provider_name, model_id)
            return bool(model and model.supports_vision)
        except Exception:
            return False

    async def _cmd_image(self, arg: str) -> None:
        if not arg:
            self._add_message("Usage: /image <path-to-image>", role="error")
            return
        try:
            attachment = normalize_image_attachment(arg)
        except Exception as exc:
            self._add_message(f"Failed to attach image: {exc}", role="error")
            return
        if not Path(attachment.path).exists():
            self._add_message(f"Image not found: {attachment.path}", role="error")
            return
        if not Path(attachment.path).is_file():
            self._add_message(f"Not a file: {attachment.path}", role="error")
            return
        if not is_supported_image_path(attachment.path):
            self._add_message("Only image files are supported for /image.", role="error")
            return
        if not await self._model_supports_vision():
            self._add_message("Current model does not support image input.", role="error")
            return
        self._queue_attachment(attachment)
        self._add_message(
            f"Attached image: {attachment.name or Path(attachment.path).name}", role="tool"
        )

    async def _cmd_image_paste(self) -> None:
        if not await self._model_supports_vision():
            self._add_message("Current model does not support image input.", role="error")
            return
        try:
            attachment = self._paste_image_from_clipboard()
        except Exception as exc:
            self._add_message(str(exc), role="error")
            return
        self._queue_attachment(attachment)
        self._add_message(
            f"Attached image from clipboard: {attachment.name or Path(attachment.path).name}",
            role="tool",
        )

    def _cmd_image_clear(self) -> None:
        count = self._clear_pending_attachments()
        if count:
            self._add_message(f"Cleared {count} pending image attachment(s).", role="tool")
        else:
            self._add_message("No pending image attachments.", role="tool")

    def _cmd_image_remove(self, arg: str) -> None:
        if not arg:
            self._add_message("Usage: /image-remove <index>", role="error")
            return
        try:
            index = int(arg)
        except ValueError:
            self._add_message("Usage: /image-remove <index>", role="error")
            return
        removed = self._remove_pending_attachment(index)
        if removed is None:
            self._add_message(f"No pending image attachment at index {index}.", role="error")
            return
        self._add_message(
            f"Removed pending image: {removed.name or Path(removed.path).name}",
            role="tool",
        )

    async def _cmd_cancel(self) -> None:
        if self.remote_url:
            if not self._run_busy:
                self._add_message("No active remote run.", role="tool")
                return
            try:
                await self._send_remote_event(
                    {
                        "type": "cancel",
                        "session_id": self._remote_session_id,
                    }
                )
            except Exception as exc:
                self._add_message(f"Failed to cancel remote run: {exc}", role="error")
                return
            self._add_message("Cancellation requested.", role="tool")
            return

        if not self._session or not self._run_busy:
            self._add_message("No active local run.", role="tool")
            return
        self._session.abort()
        self._add_message("Cancellation requested.", role="tool")

    @work(exclusive=True, thread=False)
    async def _run_local(self, text: str, attachments: list[ImageAttachment] | None = None) -> None:
        """Run a query through the local agent session."""
        if not self._session:
            self._add_message("Session not initialized.", role="error")
            return

        widget: MessageWidget | None = None
        reasoning_widget: MessageWidget | None = None
        had_tool_calls = False
        need_new_reasoning_block = False
        footer = self.query_one("#status-footer", StatusFooter)

        self._set_run_activity("thinking", busy=True)
        await cmux.set_status("state", "thinking", icon="brain", color="#89b4fa")

        try:
            run_iter = (
                self._session.run(text, attachments=attachments)
                if attachments
                else self._session.run(text)
            )
            async for event in run_iter:
                if event.type == AgentEventType.REASONING_DELTA:
                    self._set_run_activity("thinking", busy=True)
                    if reasoning_widget is None or need_new_reasoning_block:
                        reasoning_widget = self._add_reasoning_block()
                        need_new_reasoning_block = False
                    reasoning_widget.append_content(event.content)

                elif event.type == AgentEventType.TEXT_DELTA:
                    self._set_run_activity("responding", busy=True)
                    if widget is None or had_tool_calls:
                        widget = self._add_message("", role="assistant")
                        had_tool_calls = False
                    widget.append_content(event.content)

                elif event.type == AgentEventType.TOOL_CALL:
                    had_tool_calls = True
                    need_new_reasoning_block = True
                    tool_display = format_tool_call_display(event.tool_name, event.tool_args)
                    self._start_tool_card(
                        event.tool_call_id or str(uuid.uuid4()),
                        title=tool_display.title,
                        body=tool_display.body,
                    )
                    self._set_run_activity(f"tool: {event.tool_name}", busy=True)
                    await cmux.set_status(
                        "state",
                        f"tool: {event.tool_name}",
                        icon="gear",
                        color="#f9e2af",
                    )
                    await cmux.log(f"tool: {event.tool_name}", source="artel")

                elif event.type == AgentEventType.TOOL_RESULT:
                    result_display = format_tool_result_display(
                        tool_name=event.tool_name,
                        content=event.content,
                        is_error=event.is_error,
                        display=event.display,
                    )
                    self._finish_tool_card(
                        event.tool_call_id or str(uuid.uuid4()),
                        title=result_display.title,
                        body=result_display.body,
                        markdown=result_display.markdown,
                        display=event.display,
                        kind=result_display.kind,
                        status_badge=result_display.status_badge,
                        status_variant=result_display.status_variant,
                    )
                    self._set_run_activity("thinking", busy=True)

                elif event.type == AgentEventType.ERROR:
                    self._add_message(event.error, role="error")
                    await cmux.log(event.error, level="error", source="artel")

                elif event.type == AgentEventType.COMPACT:
                    self._add_message("\U0001f4cb Session auto-compacted.", role="tool")

                elif event.type == AgentEventType.DONE:
                    if event.usage:
                        footer.update_usage(
                            event.usage.input_tokens,
                            event.usage.output_tokens,
                            self._input_price,
                            self._output_price,
                        )
                    if self._session:
                        est = self._session._estimate_tokens()
                        footer.update_context_pct(est, self._session.context_window)
                        await cmux.set_status("context", str(est), icon="database", color="#89dceb")
                        if self._session.context_window > 0:
                            pct = est / self._session.context_window
                            await cmux.set_progress(min(pct, 1.0), label=f"ctx {pct:.0%}")
        finally:
            self._set_run_activity("idle", busy=False)
            await cmux.set_status("state", "idle", icon="check", color="#a6e3a1")
            await cmux.notify("Artel", subtitle="Task complete")
            self._scroll_to_bottom()

    @work(exclusive=True, thread=False)
    async def _run_remote(
        self, text: str, attachments: list[ImageAttachment] | None = None
    ) -> None:
        """Send a query to the remote server via WebSocket."""
        import websockets

        if not self._ws:
            try:
                self._ws = await websockets.connect(
                    self.remote_url,
                    additional_headers=self._remote_connect_headers(),
                )
            except Exception as e:
                self._add_message(f"Connection failed: {e}", role="error")
                return

        try:
            await self._ws.send(
                json.dumps(self._remote_message_payload(text, attachments=attachments))
            )
        except Exception as e:
            self._add_message(f"Connection error: {e}", role="error")
            self._ws = None
            return

        widget: MessageWidget | None = None
        reasoning_widget: MessageWidget | None = None
        had_tool_calls = False
        need_new_reasoning_block = False
        footer = self.query_one("#status-footer", StatusFooter)
        self._set_run_activity("thinking", busy=True)

        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")
                if msg_type == "reasoning_delta":
                    self._set_run_activity("thinking", busy=True)
                    if reasoning_widget is None or need_new_reasoning_block:
                        reasoning_widget = self._add_reasoning_block()
                        need_new_reasoning_block = False
                    reasoning_widget.append_content(msg.get("content", ""))
                elif msg_type == "text_delta":
                    self._set_run_activity("responding", busy=True)
                    if widget is None or had_tool_calls:
                        widget = self._add_message("", role="assistant")
                        had_tool_calls = False
                    widget.append_content(msg.get("content", ""))
                elif msg_type == "tool_call":
                    had_tool_calls = True
                    need_new_reasoning_block = True
                    tool_name = str(msg.get("tool", "")).strip()
                    tool_args = msg.get("args", {})
                    tool_display = format_tool_call_display(
                        tool_name,
                        tool_args if isinstance(tool_args, dict) else {},
                    )
                    self._start_tool_card(
                        str(msg.get("call_id", "") or uuid.uuid4()),
                        title=tool_display.title,
                        body=tool_display.body,
                    )
                    self._set_run_activity(f"tool: {tool_name}", busy=True)
                elif msg_type == "tool_result":
                    output = str(msg.get("output", "") or "")
                    result_display = format_tool_result_display(
                        tool_name=str(msg.get("tool", "") or "tool"),
                        content=output,
                        is_error=bool(msg.get("is_error", False)),
                        display=msg.get("display")
                        if isinstance(msg.get("display"), dict)
                        else None,
                    )
                    self._finish_tool_card(
                        str(msg.get("call_id", "") or uuid.uuid4()),
                        title=result_display.title,
                        body=result_display.body,
                        markdown=result_display.markdown,
                        display=msg.get("display")
                        if isinstance(msg.get("display"), dict)
                        else None,
                        kind=result_display.kind,
                        status_badge=result_display.status_badge,
                        status_variant=result_display.status_variant,
                    )
                    self._set_run_activity("thinking", busy=True)
                elif msg_type == "permission_request":
                    await self._handle_remote_permission_request(msg)
                elif msg_type == "session_updated":
                    session = msg.get("session")
                    if isinstance(session, dict):
                        self._apply_remote_session_state(session)
                elif msg_type == "board_event":
                    event_name = str(msg.get("event", "")).strip()
                    payload = msg.get("payload", {})
                    if isinstance(payload, dict) and event_name:
                        await self._refresh_board_after_tool_event(event_name, payload)
                elif msg_type == "status":
                    state_label = (
                        str(msg.get("state", "")).strip() or str(msg.get("label", "")).strip()
                    )
                    self._set_run_activity(
                        state_label or "working", busy=bool(msg.get("busy", True))
                    )
                elif msg_type == "error":
                    error_text = str(msg.get("error", "Unknown error"))
                    self._add_message(error_text, role="error")
                    if "Unknown type: steer" in error_text:
                        self._add_message(
                            "Remote server does not support steering yet. Run "
                            "/server-restart to reload the local managed server.",
                            role="tool",
                        )
                    break
                elif msg_type == "done":
                    usage = msg.get("usage")
                    if usage:
                        footer.update_usage(
                            int(usage.get("input", 0)),
                            int(usage.get("output", 0)),
                            self._input_price,
                            self._output_price,
                        )
                    break
        except Exception as e:
            self._add_message(f"Connection error: {e}", role="error")
            self._ws = None
        finally:
            self._set_run_activity("idle", busy=False)
            self._scroll_to_bottom()

    def _auto_collapse_server_dock_for_size(self) -> None:
        with suppress(Exception):
            width = int(self.size.width)
        if width and width < 100 and self._server_dock_visible:
            self._server_dock_visible = False
            self._server_dock().set_visible(False)
            self._set_server_dock_status("Hidden automatically on narrow screens")

    async def _handle_remote_permission_request(self, payload: dict[str, Any]) -> None:
        request_id = str(payload.get("request_id", "")).strip()
        if not request_id or self._ws is None:
            return
        tool_name = str(payload.get("tool", "")).strip() or "tool"
        raw_args = payload.get("args", {})
        args = raw_args if isinstance(raw_args, dict) else {}
        decision = await self._request_permission_decision(tool_name, args)
        resolved = decision if decision in {"once", "all", "deny"} else "deny"
        if resolved == "all":
            self._auto_approve_all = True
        await self._ws.send(
            json.dumps(
                {
                    "type": "approve_tool",
                    "request_id": request_id,
                    "decision": resolved,
                }
            )
        )

    # ── Model management ────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def _list_models(self) -> None:
        """Show available models for connected providers only."""
        if self.remote_url:
            try:
                payload = await self._remote_control().list_models()
            except Exception as exc:
                self._add_message(f"Failed to load remote models: {exc}", role="error")
                return
            providers = payload.get("providers", [])
            lines: list[str] = []
            for provider in providers:
                name = provider.get("name", provider.get("id", ""))
                provider_id = provider.get("id", "")
                lines.append(f"\n  {name}:")
                for model in provider.get("models", []):
                    context_window = model.get("context_window") or 0
                    ctx = f"{context_window // 1000}k" if context_window else "?"
                    lines.append(
                        f"      {provider_id}/{model.get('id', '')}  "
                        f"({model.get('name', '')}, {ctx} ctx)"
                    )
            if lines:
                self._add_message(
                    "Connected providers:\n" + "\n".join(lines),
                    role="tool",
                )
            else:
                self._add_message(
                    "No connected providers. Use /providers to see supported providers "
                    "and setup hints.",
                    role="error",
                )
            return
        from artel_core.cli import _resolve_api_key

        config = load_config(os.getcwd())
        catalog = await get_effective_provider_catalog(config)
        lines: list[str] = []
        for pid, prov in catalog.items():
            requires_key = provider_requires_api_key(config, pid)
            api_key, _ = await _resolve_api_key(config, pid)
            connected = bool(api_key or not requires_key)
            if not connected:
                continue
            lines.append(f"\n  {prov.name}:")
            for m in prov.models:
                ctx = f"{m.context_window // 1000}k" if m.context_window else "?"
                lines.append(f"      {pid}/{m.id}  ({m.name}, {ctx} ctx)")

        if lines:
            self._add_message(
                "Connected providers:\n" + "\n".join(lines),
                role="tool",
            )
        else:
            self._add_message(
                "No connected providers. Use /providers to see supported providers "
                "and setup hints.",
                role="error",
            )

    async def _list_providers(self) -> None:
        """Show all supported providers with setup guidance."""
        if self.remote_url:
            try:
                payload = await self._remote_control().list_providers()
            except Exception as exc:
                self._add_message(f"Failed to load remote providers: {exc}", role="error")
                return
            entries = [
                ProviderSetupEntry(
                    id=str(item.get("id", "")),
                    name=str(item.get("name", "")),
                    status=str(item.get("status", "")),
                    hint=str(item.get("hint", "")),
                )
                for item in payload.get("providers", [])
            ]
            self._add_message(format_provider_setup_entries(entries), role="tool")
            return
        from artel_core.cli import _resolve_api_key

        config = load_config(os.getcwd())
        entries = await collect_provider_setup_entries(config, _resolve_api_key)
        self._add_message(format_provider_setup_entries(entries), role="tool")

    async def _switch_model(self, model_str: str) -> None:
        """Switch to a different model (provider/model-id format)."""
        if self.remote_url:
            try:
                payload = await self._remote_control().set_session_model(
                    self._remote_session_id,
                    model_str,
                )
            except Exception as exc:
                self._add_message(f"Failed to switch remote model: {exc}", role="error")
                return
            session = payload.get("session", {})
            self._apply_remote_session_state(session)
            self._provider_model = str(session.get("model", "")).strip() or model_str
            self.sub_title = self._provider_model
            await self._sync_cmux_session_metadata()
            self._add_message(f"Switched to {self._provider_model}", role="tool")
            return
        if "/" not in model_str:
            self._add_message(
                "Format: provider/model-id (e.g. anthropic/claude-sonnet-4-20250514)",
                role="error",
            )
            return

        from artel_core.cli import _resolve_api_key

        provider_name, model_id = model_str.split("/", 1)
        config = load_config(os.getcwd())

        # Validate model exists in catalog
        catalog_model = await get_effective_model_info(config, provider_name, model_id)
        if not catalog_model:
            self._add_message(
                f"Model '{model_id}' not found for {provider_name}. Use /models to see available.",
                role="error",
            )
            return

        api_key, _ = await _resolve_api_key(config, provider_name)
        if provider_requires_api_key(config, provider_name) and not api_key:
            hint = _provider_setup_hint_for_config(config, provider_name)
            self._add_message(
                f"No credentials for {provider_name}. {hint}",
                role="error",
            )
            return
        try:
            runtime = await bootstrap_runtime(
                config,
                provider_name,
                model_id,
                project_dir=os.getcwd(),
                resolve_api_key=_resolve_api_key,
                include_extensions=True,
                runtime="tui",
            )
        except Exception as e:
            self._add_message(f"Failed to create provider: {e}", role="error")
            return
        self._extensions = runtime.extensions
        self._input_price = runtime.input_price_per_m
        self._output_price = runtime.output_price_per_m

        # Carry over conversation history
        prior_messages = self._session.messages[1:] if self._session else []
        current_thinking = (
            self._session.thinking_level if self._session is not None else config.agent.thinking
        )

        # Close old provider
        if self._session:
            await self._session.provider.close()
            mcp_runtime = getattr(self._session, "mcp_runtime", None)
            if mcp_runtime is not None:
                await mcp_runtime.close()
            lsp_runtime = getattr(self._session, "lsp_runtime", None)
            if lsp_runtime is not None:
                await lsp_runtime.close()
        session_id = str(uuid.uuid4())
        if self._store:
            await self._store.create_session(
                session_id,
                f"{provider_name}/{model_id}",
                project_dir=os.getcwd(),
                thinking_level=current_thinking,
            )
        self._session = create_agent_session_from_bootstrap(
            config,
            runtime,
            project_dir=os.getcwd(),
            store=self._store,
            session_id=session_id,
            permission_callback=self._ask_permission,
        )
        self._session.rule_overrides = self._local_rule_overrides
        self._session.refresh_system_prompt()
        self._session.board_event_callback = self._handle_board_tool_event  # type: ignore[attr-defined]
        self._session.thinking_level = current_thinking  # type: ignore[assignment]
        await self._sync_cmux_session_metadata()

        # Restore prior messages into new session
        if prior_messages:
            self._session.messages.extend(prior_messages)

        self._provider_model = f"{provider_name}/{model_id}"
        self.sub_title = self._provider_model
        self.query_one("#status-footer", StatusFooter).set_model(self._provider_model)
        await self._sync_cmux_session_metadata()
        self._set_footer_thinking_level(str(current_thinking))
        await self._load_board_state()
        self._add_message(f"Switched to {self._provider_model}", role="tool")

    # ── Provider login ────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def _run_connect(self, provider_name: str) -> None:
        """Run OAuth login for a provider."""
        if self.remote_url:
            await self._run_remote_connect(provider_name)
            return
        from artel_ai.oauth import get_oauth_provider, list_oauth_provider_names

        config = load_config(os.getcwd())

        oauth = get_oauth_provider(provider_name, config=config)
        if oauth is None:
            supported = ", ".join(list_oauth_provider_names())
            self._add_message(
                f"OAuth not supported for '{provider_name}'. "
                f"{_provider_setup_hint_for_config(config, provider_name)}. "
                f"Supported: {supported}",
                role="error",
            )
            return

        self._add_message(
            f"Starting {provider_name} login... Check your browser/terminal.",
            role="tool",
        )
        try:
            await oauth.login()
            self._add_message(
                f"{provider_name.capitalize()} authorized! Use /model to switch.",
                role="tool",
            )
        except Exception as e:
            self._add_message(f"Login failed: {e}", role="error")

    async def _run_remote_connect(self, provider_name: str) -> None:
        from artel_ai.oauth import get_oauth_provider, list_oauth_provider_names
        from artel_ai.provider_specs import get_provider_spec

        canonical_id = (
            get_provider_spec(provider_name).id
            if get_provider_spec(provider_name) is not None
            else provider_name
        )
        self._add_message(
            f"Starting remote login for {canonical_id}...",
            role="tool",
        )

        if canonical_id == "openai":
            await self._run_remote_callback_oauth(canonical_id)
            return
        if canonical_id == "anthropic":
            await self._run_remote_code_paste_oauth(canonical_id)
            return

        config = load_config(os.getcwd())
        oauth = get_oauth_provider(canonical_id, config=config)
        if oauth is None:
            supported = ", ".join(list_oauth_provider_names())
            self._add_message(
                f"OAuth not supported for '{provider_name}'. "
                f"{_provider_setup_hint_for_config(config, provider_name)}. "
                f"Supported: {supported}",
                role="error",
            )
            return
        await self._run_remote_forwarded_oauth_login(canonical_id, config)

    async def _run_remote_callback_oauth(self, provider_name: str) -> None:
        redirect_uri, callback_future, server = await self._start_local_callback_listener()
        try:
            payload = await self._remote_control().start_oauth(
                provider_name,
                redirect_uri=redirect_uri,
            )
            authorize_url = str(payload.get("authorize_url", "")).strip()
            if authorize_url:
                self._add_message(
                    f"Opening browser for remote {provider_name} login...",
                    role="tool",
                )
                with suppress(Exception):
                    webbrowser.open(authorize_url)
            callback_payload = await asyncio.wait_for(callback_future, timeout=300)
            await self._remote_control().complete_oauth(
                str(payload.get("login_id", "")),
                callback_payload,
            )
            self._add_message(
                f"{provider_name.capitalize()} authorized on the remote server!",
                role="tool",
            )
        except Exception as exc:
            self._add_message(f"Remote login failed: {exc}", role="error")
        finally:
            server.close()
            await server.wait_closed()

    async def _run_remote_code_paste_oauth(self, provider_name: str) -> None:
        try:
            payload = await self._remote_control().start_oauth(provider_name)
            authorize_url = str(payload.get("authorize_url", "")).strip()
            if authorize_url:
                self._add_message(
                    f"Opening browser for remote {provider_name} login...",
                    role="tool",
                )
                with suppress(Exception):
                    webbrowser.open(authorize_url)
            self._pending_remote_oauth = {
                "provider_name": provider_name,
                "login_id": str(payload.get("login_id", "")),
            }
            self._open_inline_input(
                mode="remote_oauth_code",
                title=f"{provider_name.capitalize()} authorization",
                detail="Paste the authorization code from the browser.",
                placeholder="authorization code",
            )
        except Exception as exc:
            self._add_message(f"Remote login failed: {exc}", role="error")

    async def _run_remote_forwarded_oauth_login(self, provider_name: str, config: Any) -> None:
        from artel_ai.oauth import TokenStore, get_oauth_provider

        with tempfile.TemporaryDirectory(prefix="artel-remote-oauth-") as temp_dir:
            temp_store = TokenStore(path=Path(temp_dir) / "auth.json")
            oauth = get_oauth_provider(
                provider_name,
                config=config,
                token_store=temp_store,
            )
            if oauth is None:
                self._add_message(
                    f"Remote login is not supported for '{provider_name}'.",
                    role="error",
                )
                return
            try:
                token = await oauth.login()
                settings = self._provider_forwarding_settings(config, provider_name)
                await self._remote_control().import_credentials(
                    [
                        {
                            "provider": provider_name,
                            "settings": settings,
                            "auth": {
                                "kind": "oauth_token",
                                "token": asdict(token),
                            },
                        }
                    ]
                )
            except Exception as exc:
                self._add_message(f"Remote login failed: {exc}", role="error")
                return
        self._add_message(
            f"{provider_name.capitalize()} authorized on the remote server!",
            role="tool",
        )

    def _provider_forwarding_settings(self, config: Any, provider_name: str) -> dict[str, Any]:
        provider_config = get_provider_config(config, provider_name)
        if provider_config is None:
            return {}
        data = provider_config.model_dump(exclude_defaults=True, exclude_none=True)
        data.pop("api_key", None)
        data.pop("env", None)
        return data

    async def _start_local_callback_listener(
        self,
    ) -> tuple[str, asyncio.Future[dict[str, str]], asyncio.AbstractServer]:
        loop = asyncio.get_running_loop()
        callback_future: asyncio.Future[dict[str, str]] = loop.create_future()

        async def _handle_callback(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            try:
                data = await reader.read(8192)
                request_line = data.decode(errors="replace").splitlines()[0]
                parts = request_line.split(" ", 2)
                target = parts[1] if len(parts) >= 2 else "/"
                query = urllib.parse.parse_qs(urllib.parse.urlsplit(target).query)
                error = query.get("error", [""])[0]
                if error:
                    detail = query.get("error_description", [error])[0]
                    if not callback_future.done():
                        callback_future.set_exception(RuntimeError(detail))
                else:
                    code = query.get("code", [""])[0]
                    state = query.get("state", [""])[0]
                    if code and not callback_future.done():
                        callback_future.set_result({"code": code, "state": state})
                    elif not callback_future.done():
                        callback_future.set_exception(RuntimeError("Missing authorization code."))
                body = (
                    "<html><body><h1>Authorized!</h1>"
                    "<p>You can close this tab and return to Artel.</p>"
                    "</body></html>"
                )
                response = (
                    "HTTP/1.1 200 OK\r\n"
                    "Content-Type: text/html; charset=utf-8\r\n"
                    f"Content-Length: {len(body.encode('utf-8'))}\r\n"
                    "Connection: close\r\n\r\n"
                    f"{body}"
                )
                writer.write(response.encode("utf-8"))
                await writer.drain()
            finally:
                writer.close()
                with suppress(Exception):
                    await writer.wait_closed()

        server = await asyncio.start_server(_handle_callback, "127.0.0.1", 0)
        sock = next(iter(server.sockets or ()), None)
        if sock is None:
            server.close()
            await server.wait_closed()
            raise RuntimeError("Failed to start local callback listener.")
        port = sock.getsockname()[1]
        return f"http://127.0.0.1:{port}/auth/callback", callback_future, server

    # ── Session commands ───────────────────────────────────────

    async def _cmd_project(self, arg: str) -> None:
        """Show or change the active project/cwd."""
        if not self.remote_url:
            cwd = os.getcwd()
            if not arg:
                self._add_message(f"Current project: {cwd}", role="tool")
                return
            self._add_message(
                "Changing project is currently supported only in remote mode.",
                role="error",
            )
            return

        if not arg:
            if not self._remote_project_dir:
                await self._sync_remote_session_state()
            project_dir = self._remote_project_dir or "(unknown)"
            self._add_message(f"Current remote project: {project_dir}", role="tool")
            return

        try:
            payload = await self._remote_control().set_session_project(
                self._remote_session_id,
                arg,
            )
        except Exception as exc:
            self._add_message(f"Failed to switch remote project: {exc}", role="error")
            return

        session = payload.get("session", {})
        self._apply_remote_session_state(session)
        await self._load_board_state()
        await self._refresh_server_dock()
        project_dir = str(session.get("project_dir", "")).strip() or arg
        self._add_message(f"Switched remote project to: {project_dir}", role="tool")

    async def _cmd_rules(self) -> None:
        if self.remote_url:
            try:
                payload = await self._remote_control().list_rules(
                    project_dir=self._current_rules_project_dir()
                )
                overrides_payload = await self._remote_control().get_session_rule_overrides(
                    self._remote_session_id
                )
            except Exception as exc:
                self._add_message(f"Failed to load remote rules: {exc}", role="error")
                return
            rules = payload.get("rules", [])
            overrides = overrides_payload.get("rule_overrides", {})
            self._remote_rule_overrides = overrides if isinstance(overrides, dict) else {}
            if not rules:
                self._add_message("No rules configured.", role="tool")
                return
            disabled_ids = set(self._remote_rule_overrides.get("disabled_rule_ids", []))
            enabled_ids = set(self._remote_rule_overrides.get("enabled_rule_ids", []))
            lines = ["Configured rules:"]
            for rule in rules:
                rule_id = str(rule.get("id", ""))
                base_enabled = bool(rule.get("enabled", True))
                persisted = "enabled" if base_enabled else "disabled"
                if rule_id in disabled_ids:
                    override = "disabled"
                    effective = "disabled"
                elif rule_id in enabled_ids and not base_enabled:
                    override = "enabled"
                    effective = "enabled"
                else:
                    override = "-"
                    effective = persisted
                order = int(rule.get("order", 0) or 0)
                lines.append(
                    f"  {order}. {rule_id} [{rule.get('scope', '')}] "
                    f"persisted={persisted} session={override} "
                    f"effective={effective} {rule.get('text', '')}"
                )
            self._add_message("\n".join(lines), role="tool")
            return
        rules = list_rules(self._current_rules_project_dir())
        if not rules:
            self._add_message("No rules configured.", role="tool")
            return
        lines = ["Configured rules:"]
        for rule in rules:
            persisted = "enabled" if rule.enabled else "disabled"
            effective = effective_rule_state(rule, self._local_rule_overrides)
            if effective == "session-disabled":
                override = "disabled"
                effective_label = "disabled"
            elif effective == "session-enabled":
                override = "enabled"
                effective_label = "enabled"
            else:
                override = "-"
                effective_label = effective
            lines.append(
                f"  {rule.order}. {rule.id} [{rule.scope}] "
                f"persisted={persisted} session={override} "
                f"effective={effective_label} {rule.text}"
            )
        self._add_message("\n".join(lines), role="tool")

    async def _cmd_rule(self, arg: str) -> None:
        parts = arg.split(maxsplit=1)
        action = parts[0].strip().lower() if parts else ""
        rest = parts[1].strip() if len(parts) > 1 else ""
        if action == "add":
            self._run_rule_editor_dialog()
            return
        if action == "edit":
            if not rest:
                self._add_message("Usage: /rule edit <rule-id>", role="error")
                return
            self._run_rule_editor_dialog(rule_id=rest)
            return
        if action in {"delete", "remove", "rm"}:
            if not rest:
                self._add_message("Usage: /rule delete <rule-id>", role="error")
                return
            if self.remote_url:
                try:
                    payload = await self._remote_control().delete_rule(
                        rest, project_dir=self._current_rules_project_dir()
                    )
                except Exception as exc:
                    self._add_message(f"Failed to delete rule: {exc}", role="error")
                    return
                deleted = payload.get("rule", {})
                self._add_message(f"Deleted rule {deleted.get('id', rest)}.", role="tool")
                return
            deleted = delete_rule(rest, self._current_rules_project_dir())
            if deleted is None:
                self._add_message(f"Rule '{rest}' not found.", role="error")
                return
            self._add_message(f"Deleted rule {deleted.id}.", role="tool")
            return
        if action == "enable":
            if not rest:
                self._add_message("Usage: /rule enable <rule-id>", role="error")
                return
            try:
                if self.remote_url:
                    payload = await self._remote_control().set_session_rule_enabled(
                        self._remote_session_id,
                        rest,
                        enabled=True,
                    )
                    self._remote_rule_overrides = payload.get("rule_overrides", {})
                    rule_id = str(payload.get("rule_id", rest))
                else:
                    set_rule_enabled_for_session(self._local_rule_overrides, rest, True)
                    if self._session is not None:
                        self._session.rule_overrides = self._local_rule_overrides
                        self._session.refresh_system_prompt()
                    rule_id = rest
            except Exception as exc:
                self._add_message(f"Failed to enable rule: {exc}", role="error")
                return
            self._add_message(f"Enabled rule {rule_id} for this session.", role="tool")
            return
        if action == "disable":
            if not rest:
                self._add_message("Usage: /rule disable <rule-id>", role="error")
                return
            try:
                if self.remote_url:
                    payload = await self._remote_control().set_session_rule_enabled(
                        self._remote_session_id,
                        rest,
                        enabled=False,
                    )
                    self._remote_rule_overrides = payload.get("rule_overrides", {})
                    rule_id = str(payload.get("rule_id", rest))
                else:
                    set_rule_enabled_for_session(self._local_rule_overrides, rest, False)
                    if self._session is not None:
                        self._session.rule_overrides = self._local_rule_overrides
                        self._session.refresh_system_prompt()
                    rule_id = rest
            except Exception as exc:
                self._add_message(f"Failed to disable rule: {exc}", role="error")
                return
            self._add_message(f"Disabled rule {rule_id} for this session.", role="tool")
            return
        if action == "persist":
            persist_parts = rest.split(maxsplit=1)
            persist_action = persist_parts[0].strip().lower() if persist_parts else ""
            persist_rule_id = persist_parts[1].strip() if len(persist_parts) > 1 else ""
            if persist_action not in {"enable", "disable"} or not persist_rule_id:
                self._add_message(
                    "Usage: /rule persist enable <rule-id> | /rule persist disable <rule-id>",
                    role="error",
                )
                return
            try:
                if self.remote_url:
                    payload = await self._remote_control().edit_rule(
                        persist_rule_id,
                        project_dir=self._current_rules_project_dir(),
                        enabled=(persist_action == "enable"),
                    )
                    rule_id = str(payload.get("rule", {}).get("id", persist_rule_id))
                else:
                    rule = update_rule(
                        persist_rule_id,
                        project_dir=self._current_rules_project_dir(),
                        enabled=(persist_action == "enable"),
                    )
                    rule_id = rule.id
                    if self._session is not None:
                        self._session.refresh_system_prompt()
            except Exception as exc:
                self._add_message(f"Failed to update persisted rule state: {exc}", role="error")
                return
            self._add_message(
                f"Persistently {'enabled' if persist_action == 'enable' else 'disabled'} "
                f"rule {rule_id}.",
                role="tool",
            )
            return
        if action == "move":
            move_parts = rest.split()
            if len(move_parts) < 2:
                self._add_message("Usage: /rule move <rule-id> up|down|to <n>", role="error")
                return
            move_rule_id = move_parts[0].strip()
            move_action = move_parts[1].strip().lower()
            position = None
            offset = None
            if move_action == "up":
                offset = -1
            elif move_action == "down":
                offset = 1
            elif move_action == "to" and len(move_parts) >= 3:
                try:
                    position = int(move_parts[2])
                except ValueError:
                    self._add_message("Usage: /rule move <rule-id> to <position>", role="error")
                    return
            else:
                self._add_message("Usage: /rule move <rule-id> up|down|to <n>", role="error")
                return
            try:
                if self.remote_url:
                    payload = await self._remote_control().move_rule(
                        move_rule_id,
                        project_dir=self._current_rules_project_dir(),
                        position=position,
                        offset=offset,
                    )
                    moved = payload.get("rule", {})
                    moved_id = str(moved.get("id", move_rule_id))
                    moved_order = int(moved.get("order", 0) or 0)
                else:
                    moved_rule = move_rule(
                        move_rule_id,
                        project_dir=self._current_rules_project_dir(),
                        position=position,
                        offset=offset,
                    )
                    moved_id = moved_rule.id
                    moved_order = moved_rule.order
                    if self._session is not None:
                        self._session.refresh_system_prompt()
            except Exception as exc:
                self._add_message(f"Failed to move rule: {exc}", role="error")
                return
            self._add_message(f"Moved rule {moved_id} to position {moved_order}.", role="tool")
            return
        if action == "reset":
            if not rest:
                self._add_message("Usage: /rule reset <rule-id|all>", role="error")
                return
            if self.remote_url:
                try:
                    if rest == "all":
                        payload = await self._remote_control().set_session_rule_enabled(
                            self._remote_session_id,
                            "*",
                            enabled=None,
                        )
                    else:
                        payload = await self._remote_control().set_session_rule_enabled(
                            self._remote_session_id,
                            rest,
                            enabled=None,
                        )
                    self._remote_rule_overrides = payload.get("rule_overrides", {})
                except Exception as exc:
                    self._add_message(f"Failed to reset session rule override: {exc}", role="error")
                    return
            else:
                if rest == "all":
                    clear_session_rule_overrides(self._local_rule_overrides)
                else:
                    reset_rule_for_session(self._local_rule_overrides, rest)
                if self._session is not None:
                    self._session.rule_overrides = self._local_rule_overrides
                    self._session.refresh_system_prompt()
            self._add_message(
                "Reset all session rule overrides."
                if rest == "all"
                else f"Reset session override for rule {rest}.",
                role="tool",
            )
            return
        self._add_message(
            "Usage: /rule add | /rule edit <id> | /rule delete <id> | "
            "/rule enable <id> | /rule disable <id> | "
            "/rule persist enable <id> | /rule persist disable <id> | "
            "/rule move <id> up|down|to <n> | /rule reset <id|all>",
            role="tool",
        )

    def _current_rules_project_dir(self) -> str:
        if self.remote_url and self._remote_project_dir:
            return self._remote_project_dir
        return os.getcwd()

    @work(exclusive=True, thread=False)
    async def _run_rule_editor_dialog(self, rule_id: str = "") -> None:
        existing = None
        if rule_id:
            if self.remote_url:
                try:
                    payload = await self._remote_control().list_rules(
                        project_dir=self._current_rules_project_dir()
                    )
                except Exception as exc:
                    self._add_message(f"Failed to load remote rules: {exc}", role="error")
                    return
                for item in payload.get("rules", []):
                    if str(item.get("id", "")).strip() == rule_id:
                        existing = item
                        break
            else:
                existing = get_rule(rule_id, self._current_rules_project_dir())
            if existing is None:
                self._add_message(f"Rule '{rule_id}' not found.", role="error")
                return
        self._pending_rule_editor_existing = existing
        self._open_inline_rule_editor(
            title="Edit rule" if existing is not None else "Add rule",
            text=(existing.get("text", "") if isinstance(existing, dict) else existing.text)
            if existing is not None
            else "",
            scope=(
                existing.get("scope", "project") if isinstance(existing, dict) else existing.scope
            )
            if existing is not None
            else "project",
            enabled=(
                bool(existing.get("enabled", True))
                if isinstance(existing, dict)
                else existing.enabled
            )
            if existing is not None
            else True,
        )

    async def _cmd_tasks(self) -> None:
        await self._load_board_state()
        content = self._board_sidebar().tasks_text()
        if not content.strip():
            self._add_message("No tasks yet.", role="tool")
            return
        self._add_message(render_numbered_text(content), role="tool")

    async def _cmd_task_add(self, arg: str) -> None:
        if not arg:
            self._add_message("Usage: /task-add <title>", role="error")
            return
        content = self._board_sidebar().tasks_text()
        try:
            updated, task_id = add_task_to_markdown(content, arg)
        except Exception as exc:
            self._add_message(f"Failed to add task: {exc}", role="error")
            return
        self._suspend_board_editor_events = True
        try:
            self._board_sidebar().set_tasks(updated)
        finally:
            self._suspend_board_editor_events = False
        await self._save_tasks_text(updated)
        self._add_message(f"Added task #{task_id}: {arg}", role="tool")

    async def _cmd_task_done(self, arg: str) -> None:
        if not arg:
            self._add_message("Usage: /task-done <task-id>", role="error")
            return
        try:
            task_id = int(arg)
        except ValueError:
            self._add_message("Usage: /task-done <task-id>", role="error")
            return
        content = self._board_sidebar().tasks_text()
        try:
            updated = update_task_in_markdown(content, task_id, status="done")
        except Exception as exc:
            self._add_message(f"Failed to complete task: {exc}", role="error")
            return
        self._suspend_board_editor_events = True
        try:
            self._board_sidebar().set_tasks(updated)
        finally:
            self._suspend_board_editor_events = False
        await self._save_tasks_text(updated)
        self._add_message(f"Completed task #{task_id}", role="tool")

    async def _cmd_notes(self) -> None:
        await self._load_board_state()
        content = self._board_sidebar().notes_text()
        if not content.strip():
            self._add_message("Operator notes are empty.", role="tool")
            return
        self._add_message(render_numbered_text(content), role="tool")

    async def _handle_server_dock_selection(self, data: ServerDockNodeData) -> None:
        if data.kind == "session" and data.session_id:
            await self._connect_to_server(
                data.remote_url,
                auth_token=data.auth_token,
                save=False,
                resume_session_id=data.session_id,
            )
            return
        if data.kind == "project" and data.project_dir:
            await self._connect_to_server(
                data.remote_url,
                auth_token=data.auth_token,
                save=False,
                project_dir=data.project_dir,
            )
            return
        if data.kind == "server" and data.remote_url:
            await self._connect_to_server(data.remote_url, auth_token=data.auth_token, save=False)

    async def _cmd_resume_remote(self, arg: str) -> None:
        """List or resume server-backed remote sessions."""
        if arg and not arg.isdigit():
            await self._resume_remote_session(arg)
            return
        try:
            payload = await self._remote_control().list_sessions()
        except Exception as exc:
            self._add_message(f"Failed to load remote sessions: {exc}", role="error")
            return

        sessions = payload.get("sessions", [])
        if arg.isdigit():
            idx = int(arg) - 1
            if 0 <= idx < len(sessions):
                session_id = str(sessions[idx].get("id", "")).strip()
                if session_id:
                    await self._resume_remote_session(session_id)
                    return
            self._add_message(f"Invalid index: {arg}", role="error")
            return

        if not sessions:
            self._add_message("No saved remote sessions.", role="tool")
            return

        lines = ["Recent sessions:"]
        for i, session in enumerate(sessions, 1):
            title = str(session.get("title", "")).strip() or "(untitled)"
            model = str(session.get("model", "")).strip() or "remote"
            updated_at = str(session.get("updated_at", "")).strip()
            project_dir = str(session.get("project_dir", "")).strip()
            prefix = f"  {i}. [{updated_at}] " if updated_at else f"  {i}. "
            proj_label = f" @ {project_dir}" if project_dir else ""
            lines.append(f"{prefix}{title} ({model}){proj_label}")
        lines.append("\nType /resume <number> to load a session.")
        self._add_message("\n".join(lines), role="tool")

    async def _resume_remote_session(self, session_id: str) -> None:
        """Load a remote session and replace the current chat."""
        try:
            session_payload = await self._remote_control().get_session(session_id)
            messages_payload = await self._remote_control().get_session_messages(session_id)
        except Exception as exc:
            self._add_message(f"Failed to resume remote session: {exc}", role="error")
            return

        session = session_payload.get("session", {})
        self._remote_session_id = session_id
        self._apply_remote_session_state(session)
        await self._load_board_state()
        await self._sync_remote_extension_commands()

        container = self.query_one("#chat-container", Vertical)
        container.remove_children()
        self._tool_collapsibles.clear()
        self._active_tool_cards.clear()
        self._tool_call_names.clear()

        for message in messages_payload.get("messages", []):
            role = str(message.get("role", ""))
            content = str(message.get("content", ""))
            reasoning = str(message.get("reasoning", ""))
            attachments_payload = message.get("attachments", [])
            attachments = [
                ImageAttachment(
                    path=str(item.get("path", "")),
                    mime_type=str(item.get("mime_type", "image/png") or "image/png"),
                    name=str(item.get("name", "") or ""),
                )
                for item in attachments_payload
                if isinstance(item, dict) and str(item.get("path", "")).strip()
            ]
            self._render_restored_message(
                role=role,
                content=content,
                reasoning=reasoning,
                attachments=attachments or None,
                tool_calls=message.get("tool_calls")
                if isinstance(message.get("tool_calls"), list)
                else None,
                tool_result=message.get("tool_result")
                if isinstance(message.get("tool_result"), dict)
                else None,
            )

        await self._refresh_server_dock()
        title = str(session.get("title", "")).strip() or session_id[:8]
        self._add_message(f"Resumed remote session: {title}", role="tool")

    async def _connect_to_server(
        self,
        remote_url: str,
        *,
        auth_token: str = "",
        save: bool = True,
        project_dir: str = "",
        resume_session_id: str = "",
    ) -> None:
        normalized_url = remote_url.strip()
        if not normalized_url:
            self._add_message("Missing remote URL.", role="error")
            return
        self._dismissed_server_urls.discard(normalized_url)
        self.remote_url = normalized_url
        self.auth_token = auth_token
        self._remote_control_client = None
        self._remote_extension_commands = set()
        self._remote_project_dir = ""
        self._remote_rule_overrides = {}
        self._remote_session_id = str(uuid.uuid4())
        if self._ws:
            with suppress(Exception):
                await self._ws.close()
            self._ws = None
        self.sub_title = f"remote: {self.remote_url}"
        if save:
            self._saved_servers = upsert_saved_server(
                SavedArtelServer(
                    name=default_server_name(self.remote_url),
                    remote_url=self.remote_url,
                    auth_token=self.auth_token,
                )
            )
        if resume_session_id:
            await self._resume_remote_session(resume_session_id)
        else:
            await self._sync_remote_session_state()
            if project_dir:
                try:
                    payload = await self._remote_control().set_session_project(
                        self._remote_session_id,
                        project_dir,
                    )
                except Exception as exc:
                    self._add_message(f"Failed to switch remote project: {exc}", role="error")
                else:
                    session = payload.get("session", {})
                    if isinstance(session, dict):
                        self._apply_remote_session_state(session)
                        await self._load_board_state()
            await self._sync_remote_extension_commands()
            await self._refresh_server_dock()
            label = default_server_name(self.remote_url)
            if project_dir:
                self._add_message(f"Connected to {label}; project: {project_dir}", role="tool")
            else:
                self._add_message(f"Connected to {label}", role="tool")

    async def _cmd_resume(self, arg: str) -> None:
        """List sessions or resume by number/ID."""
        if self.remote_url:
            await self._cmd_resume_remote(arg)
            return
        if not self._store:
            self._add_message("Session store not available.", role="error")
            return

        # If arg is a number, resume that session from the list
        if arg.isdigit():
            idx = int(arg) - 1
            sessions = await self._store.list_sessions(limit=20)
            if 0 <= idx < len(sessions):
                await self._resume_session(sessions[idx].id)
                return
            else:
                self._add_message(f"Invalid index: {arg}", role="error")
                return

        # If arg is a session ID, resume directly
        if arg:
            info = await self._store.get_session(arg)
            if info:
                await self._resume_session(arg)
            else:
                self._add_message(f"Session '{arg}' not found.", role="error")
            return

        # No arg — list sessions
        sessions = await self._store.list_sessions(limit=20)
        if not sessions:
            self._add_message("No saved sessions.", role="tool")
            return

        home = os.path.expanduser("~")
        lines = ["Recent sessions:"]
        for i, s in enumerate(sessions, 1):
            title = s.title or "(untitled)"
            proj = s.project_dir
            if proj and proj.startswith(home):
                proj = "~" + proj[len(home) :]
            proj_label = f" @ {proj}" if proj else ""
            lines.append(f"  {i}. [{s.updated_at}] {title} ({s.model}){proj_label}")
        lines.append("\nType /resume <number> to load a session.")
        self._add_message("\n".join(lines), role="tool")

    async def _resume_session(self, session_id: str) -> None:
        """Load a session and replace current chat."""
        if not self._store or not self._session:
            return

        messages = await self._store.get_messages(session_id)
        info = await self._store.get_session(session_id)

        # Switch to the session's original model if different
        if info and info.model and "/" in info.model and info.model != self._provider_model:
            await self._switch_model(info.model)

        if not self._session:
            return

        # Clear current chat
        container = self.query_one("#chat-container", Vertical)
        container.remove_children()
        self._tool_collapsibles.clear()
        self._active_tool_cards.clear()
        self._tool_call_names.clear()

        # Update session
        self._session.session_id = session_id
        self._session.messages = [self._session.messages[0]]  # Keep system prompt
        if info and info.thinking_level:
            self._session.thinking_level = info.thinking_level  # type: ignore[assignment]
        self._set_footer_thinking_level(str(self._session.thinking_level))
        self._session.messages.extend(messages)

        # Display restored messages
        for msg in messages:
            self._render_restored_message(
                role=msg.role.value,
                content=msg.content,
                reasoning=msg.reasoning or "",
                attachments=msg.attachments,
                tool_calls=[tool_call.model_dump() for tool_call in msg.tool_calls]
                if msg.tool_calls
                else None,
                tool_result=msg.tool_result.model_dump() if msg.tool_result is not None else None,
            )

        title = info.title if info else session_id[:8]
        self._add_message(f"Resumed session: {title}", role="tool")

    @work(exclusive=True, thread=False)
    async def _cmd_compact(self, custom_prompt: str = "") -> None:
        """Compact conversation history."""

        self._add_message("Compacting conversation history...", role="tool")
        if self.remote_url:
            try:
                payload = await self._remote_control().compact_session(
                    self._remote_session_id,
                    custom_prompt,
                )
            except Exception as exc:
                self._add_message(f"Compact failed: {exc}", role="error")
                return
            session = payload.get("session")
            if isinstance(session, dict):
                self._apply_remote_session_state(session)
            summary = str(payload.get("summary", ""))
            if summary:
                self._add_message(
                    f"Session compacted. Summary ({len(summary)} chars) saved.",
                    role="tool",
                )
            else:
                self._add_message("Session compacted.", role="tool")
            return
        if not self._session:
            self._add_message("No active session.", role="error")
            return
        try:
            summary = await self._session.compact(custom_prompt)
            self._add_message(
                f"Session compacted. Summary ({len(summary)} chars) saved.",
                role="tool",
            )
        except Exception as e:
            self._add_message(f"Compact failed: {e}", role="error")

    async def _cmd_name(self, title: str) -> None:
        """Rename the current session."""
        if self.remote_url:
            try:
                payload = await self._remote_control().set_session_title(
                    self._remote_session_id,
                    title,
                )
            except Exception as exc:
                self._add_message(f"Rename failed: {exc}", role="error")
                return
            session = payload.get("session")
            if isinstance(session, dict):
                self._apply_remote_session_state(session)
            self._add_message(f"Session renamed to: {title}", role="tool")
            return
        if not self._store or not self._session:
            self._add_message("No active session.", role="error")
            return
        await self._store.rename_session(self._session.session_id, title)
        self._add_message(f"Session renamed to: {title}", role="tool")

    async def _cmd_tree(self) -> None:
        """Show message tree for current session."""
        if self.remote_url:
            try:
                payload = await self._remote_control().get_session_tree(self._remote_session_id)
            except Exception as exc:
                self._add_message(f"Tree failed: {exc}", role="error")
                return
            nodes = payload.get("nodes", [])
        else:
            if not self._store or not self._session:
                self._add_message("No active session.", role="error")
                return
            nodes = await self._store.get_message_nodes(self._session.session_id)
        if not nodes:
            self._add_message("No messages in session.", role="tool")
            return

        lines = ["Session messages:"]
        for i, n in enumerate(nodes):
            role = n["role"]
            content = (n["content"] or "")[:80].replace("\n", " ")
            if len(n["content"] or "") > 80:
                content += "\u2026"
            lines.append(f"  {i}. [{role}] {content}")
        lines.append(f"\nTotal: {len(nodes)} messages. Use /fork <index> to branch.")
        self._add_message("\n".join(lines), role="tool")

    async def _cmd_fork(self, arg: str) -> None:
        """Fork session from a message index."""
        idx: int | None = None
        if arg:
            if not arg.isdigit():
                self._add_message(
                    "Usage: /fork [message_index]  (see /tree for indices)",
                    role="error",
                )
                return
            idx = int(arg)

        if self.remote_url:
            try:
                payload = await self._remote_control().fork_session(
                    self._remote_session_id,
                    message_index=idx,
                )
            except Exception as exc:
                self._add_message(f"Fork failed: {exc}", role="error")
                return
            new_session = str(payload.get("session_id", "")).strip()
            if idx is None:
                label = "Forked current session."
            else:
                label = f"Forked session at message {idx}."
            suffix = (
                f" New session ID: {new_session[:8]}… Use /resume to switch." if new_session else ""
            )
            self._add_message(f"{label}{suffix}", role="tool")
            return

        if not self._store or not self._session:
            self._add_message("No active session.", role="error")
            return

        new_id = str(uuid.uuid4())
        try:
            await self._store.fork_session(
                self._session.session_id,
                new_id,
                up_to_message_idx=idx,
            )
            if idx is None:
                label = "Forked current session."
            else:
                label = f"Forked session at message {idx}."
            self._add_message(
                f"{label} New session ID: {new_id[:8]}… Use /resume to switch.",
                role="tool",
            )
        except Exception as e:
            self._add_message(f"Fork failed: {e}", role="error")

    # ── Prompt template commands ──────────────────────────────

    def _cmd_prompts(self) -> None:
        """List available prompt templates."""
        if not self._prompts:
            self._add_message(
                "No prompt templates found.\n"
                "Place .md files in ~/.config/artel/prompts/ or .artel/prompts/.\n"
                "Legacy Artel prompt paths are still supported.",
                role="tool",
            )
            return
        lines = ["Prompt templates (use /<name> [args]):"]
        for name in sorted(self._prompts):
            preview = self._prompts[name][:80].replace("\n", " ")
            lines.append(f"  /{name} — {preview}…")
        self._add_message("\n".join(lines), role="tool")

    def _cmd_use_prompt(self, name: str, arg: str) -> None:
        """Execute a prompt template by name."""
        template = self._prompts.get(name)
        if not template:
            self._add_message(f"Prompt template '{name}' not found.", role="error")
            return

        # Build variables from arg (key=value pairs or just {{input}})
        variables: dict[str, str] = {"input": arg} if arg else {}
        if arg and "=" in arg:
            variables = {}
            for pair in arg.split():
                if "=" in pair:
                    k, _, v = pair.partition("=")
                    variables[k] = v
                else:
                    variables.setdefault("input", "")
                    variables["input"] += (" " if variables.get("input") else "") + pair

        rendered = render_prompt(template, variables)
        self._add_message(rendered, role="user")

        if self.remote_url:
            self._run_remote(rendered)
        else:
            self._run_local(rendered)

    # ── Skills commands ─────────────────────────────────────

    def _cmd_skills_list(self) -> None:
        """List available skills."""
        if not self._skills:
            self._add_message(
                "No skills found.\n"
                "Place .md files in ~/.config/artel/skills/ or .artel/skills/.\n"
                "Legacy Artel skill paths are still supported.",
                role="tool",
            )
            return
        lines = ["Available skills (use /skill:<name> to load):"]
        for sk in sorted(self._skills.values(), key=lambda s: s.name):
            desc = f" — {sk.description}" if sk.description else ""
            lines.append(f"  {sk.name}{desc}")
        self._add_message("\n".join(lines), role="tool")

    async def _cmd_skill(self, name: str) -> None:
        """Load a skill into the current session's system prompt."""
        if self.remote_url:
            try:
                payload = await self._remote_control().inject_skill(
                    self._remote_session_id,
                    name,
                )
            except Exception as exc:
                self._add_message(f"Skill load failed: {exc}", role="error")
                return
            session = payload.get("session")
            if isinstance(session, dict):
                self._apply_remote_session_state(session)
            self._add_message(f"Skill '{name}' loaded into session.", role="tool")
            return
        if not self._session:
            self._add_message("No active session.", role="error")
            return

        skill = self._skills.get(name)
        if not skill:
            available = ", ".join(sorted(self._skills)) or "none"
            self._add_message(
                f"Skill '{name}' not found. Available: {available}",
                role="error",
            )
            return

        # Inject into system prompt
        self._session.system_prompt = inject_skill(
            self._session.system_prompt,
            skill,
        )
        self._session.messages[0].content = self._session.system_prompt
        self._add_message(
            f"Skill '{name}' loaded into session.",
            role="tool",
        )

    # ── Thinking command ──────────────────────────────────────────

    async def _cmd_thinking(self, arg: str) -> None:
        """Set or show thinking level."""
        valid = ("off", "minimal", "low", "medium", "high", "xhigh")
        if self.remote_url:
            if not arg:
                try:
                    payload = await self._remote_control().get_session(self._remote_session_id)
                except Exception as exc:
                    self._add_message(f"Failed to load remote thinking level: {exc}", role="error")
                    return
                session = payload.get("session", {})
                level = str(session.get("thinking_level", "")).strip() or "off"
                self._add_message(
                    f"Current thinking level: {level}\n"
                    f"Available: {', '.join(valid)}\n"
                    f"Usage: /thinking <level>",
                    role="tool",
                )
                return
            level = arg.strip().lower()
            if level not in valid:
                self._add_message(
                    f"Invalid level '{level}'. Available: {', '.join(valid)}",
                    role="error",
                )
                return
            try:
                payload = await self._remote_control().set_session_thinking(
                    self._remote_session_id,
                    level,
                )
            except Exception as exc:
                self._add_message(f"Failed to set remote thinking level: {exc}", role="error")
                return
            session = payload.get("session", {})
            applied_level = str(session.get("thinking_level", "")).strip() or level
            self._set_footer_thinking_level(applied_level)
            self._add_message(f"Thinking level set to: {applied_level}", role="tool")
            return
        if not self._session:
            self._add_message("No active session.", role="error")
            return
        if not arg:
            self._add_message(
                f"Current thinking level: {self._session.thinking_level}\n"
                f"Available: {', '.join(valid)}\n"
                f"Usage: /thinking <level>",
                role="tool",
            )
            return
        level = arg.strip().lower()
        if level not in valid:
            self._add_message(
                f"Invalid level '{level}'. Available: {', '.join(valid)}",
                role="error",
            )
            return
        self._session.thinking_level = level  # type: ignore[assignment]
        self._set_footer_thinking_level(level)
        if self._store:
            await self._store.update_session_thinking(self._session.session_id, level)
        self._add_message(f"Thinking level set to: {level}", role="tool")

    # ── Theme commands ────────────────────────────────────────────

    def _cmd_theme(self, name: str) -> None:
        """Switch or list themes."""
        from artel_tui.themes import load_themes

        themes = load_themes(os.getcwd())

        if not name:
            available = ", ".join(sorted(themes))
            self._add_message(
                f"Current theme: {self._active_theme}\n"
                f"Available: {available}\n"
                f"Usage: /theme <name>",
                role="tool",
            )
            return

        if name not in themes:
            available = ", ".join(sorted(themes))
            self._add_message(
                f"Unknown theme '{name}'. Available: {available}",
                role="error",
            )
            return

        self._apply_theme(name)
        self._add_message(f"Theme switched to: {name}", role="tool")

    def _apply_theme(self, name: str) -> None:
        """Apply a theme's CSS to the app."""
        from artel_tui.themes import load_themes

        themes = load_themes(os.getcwd())
        css = themes.get(name)
        if css:
            self.stylesheet.add_source(css, read_from=("theme", name))
            self.stylesheet.reparse()
            self._active_theme = name
            self.refresh()

    # ── Export command ───────────────────────────────────────

    async def _cmd_export(self, arg: str) -> None:
        """Export session to HTML."""
        from datetime import datetime

        from artel_ai.models import Message, Role
        from artel_core.export import export_html

        if self.remote_url:
            try:
                session_payload = await self._remote_control().get_session(self._remote_session_id)
                messages_payload = await self._remote_control().get_session_messages(
                    self._remote_session_id,
                )
            except Exception as exc:
                self._add_message(f"Export failed: {exc}", role="error")
                return
            session = session_payload.get("session", {})
            model = str(session.get("model", "")).strip() or self._provider_model or "remote"
            messages = [
                Message(role=Role(item["role"]), content=str(item.get("content", "")))
                for item in messages_payload.get("messages", [])
                if item.get("role") in {Role.USER.value, Role.ASSISTANT.value}
            ]
        else:
            if not self._session:
                self._add_message("No active session.", role="error")
                return
            model = self._session.model
            messages = [m for m in self._session.messages if m.role in (Role.USER, Role.ASSISTANT)]
        if not messages:
            self._add_message("Nothing to export.", role="error")
            return

        html = export_html(
            messages,
            title=f"Artel — {model}",
            model=model,
            session_id=self._remote_session_id if self.remote_url else self._session.session_id,
        )
        filename = arg or f"artel-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(html)
            self._add_message(f"Exported to {filename}", role="tool")
        except OSError as e:
            self._add_message(f"Export failed: {e}", role="error")

    # ── Reload & extensions ─────────────────────────────────

    async def _cmd_reload(self) -> None:
        """Hot-reload extensions, prompts, and skills."""
        from artel_core.extensions import reload_extensions_async

        config = load_config(os.getcwd())
        context = ExtensionContext(project_dir=os.getcwd(), runtime="tui", config=config)
        if self.remote_url:
            self._tui_extensions = await reload_tui_extensions_async(
                self._tui_extensions,
                context=context,
            )
            for ext in self._tui_extensions:
                with suppress(Exception):
                    await ext.mount(self)
                self._register_tui_extension_keybindings(ext)
            project_dir = os.getcwd()
            self._prompts = load_prompts(project_dir)
            self._skills = load_skills(project_dir)
            try:
                payload = await self._remote_control().reload_session(self._remote_session_id)
            except Exception as exc:
                self._add_message(f"Reload failed: {exc}", role="error")
                return
            session = payload.get("session")
            if isinstance(session, dict):
                self._apply_remote_session_state(session)
            self._add_message(
                f"Reloaded remote session, {len(self._tui_extensions)} tui extension(s), "
                f"{len(self._prompts)} prompt(s), {len(self._skills)} skill(s)",
                role="tool",
            )
            return
        if not self._session:
            self._add_message("No active session.", role="error")
            return
        self._extensions, new_hooks = await reload_extensions_async(
            self._extensions, context=context
        )
        self._tui_extensions = await reload_tui_extensions_async(
            self._tui_extensions, context=context
        )
        self._session.hooks = new_hooks

        # Re-collect tools
        tools = create_builtin_tools(os.getcwd())
        for ext in self._extensions:
            tools.extend(ext.get_tools())
        self._session.tools = {t.name: t for t in tools}
        for ext in self._tui_extensions:
            with suppress(Exception):
                await ext.mount(self)
            self._register_tui_extension_keybindings(ext)

        # Reload prompts and skills
        project_dir = os.getcwd()
        self._prompts = load_prompts(project_dir)
        self._skills = load_skills(project_dir)
        self._add_message(
            f"Reloaded: {len(self._extensions)} core extension(s), "
            f"{len(self._tui_extensions)} tui extension(s), "
            f"{len(self._prompts)} prompt(s), "
            f"{len(self._skills)} skill(s)",
            role="tool",
        )

    async def _close_store(self) -> None:
        """Close the session store."""
        if self._store:
            await self._store.close()
            self._store = None

    # ── Bash execution ────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def _run_remote_bash(self, cmd: str, send_to_llm: bool = False) -> None:
        """Execute a shell command on the remote server and display the output."""
        try:
            payload = await self._remote_control().run_bash(self._remote_session_id, cmd)
            session = payload.get("session")
            if isinstance(session, dict):
                self._apply_remote_session_state(session)
            output = str(payload.get("output", "")).rstrip()
            exit_code = int(payload.get("exit_code", 0))
            if output:
                self._add_message(output, role="tool")
            if exit_code != 0:
                self._add_message(f"exit code: {exit_code}", role="error")

            if send_to_llm and output:
                llm_text = f"Output of `{cmd}`:\n```\n{output}\n```"
                self._add_message(llm_text, role="user")
                self._run_remote(llm_text)
        except Exception as e:
            self._add_message(f"Remote command failed: {e}", role="error")
        self._scroll_to_bottom()

    @work(exclusive=True, thread=False)
    async def _run_bash(self, cmd: str, send_to_llm: bool = False) -> None:
        """Execute a shell command and display the output.

        If *send_to_llm* is True (``!`` prefix), the output is also
        forwarded to the LLM as a user message.  ``!!`` prefix keeps
        the output local only.
        """
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=os.getcwd(),
            )
            stdout, _ = await proc.communicate()
            output = stdout.decode(errors="replace").rstrip()
            if output:
                # Truncate very long output
                if len(output) > 5000:
                    output = output[:5000] + f"\n... (truncated, {len(stdout)} bytes total)"
                self._add_message(output, role="tool")
            if proc.returncode != 0:
                self._add_message(f"exit code: {proc.returncode}", role="error")

            # Forward output to LLM if requested
            if send_to_llm and output and self._session:
                llm_text = f"Output of `{cmd}`:\n```\n{output}\n```"
                self._add_message(llm_text, role="user")
                if self.remote_url:
                    self._run_remote(llm_text)
                else:
                    self._run_local(llm_text)
        except Exception as e:
            self._add_message(f"Command failed: {e}", role="error")
        self._scroll_to_bottom()

    # ── cmux commands ────────────────────────────────────────

    async def _cmd_split(self, arg: str) -> None:
        """Open a cmux split pane."""
        if not is_cmux():
            self._add_message("Not running in cmux.", role="error")
            return
        direction = arg if arg in ("left", "right", "up", "down") else "right"
        result = await cmux.new_split(direction)  # type: ignore[arg-type]
        self._add_message(f"Split pane opened: {result or direction}", role="tool")

    async def _cmd_browser(self, arg: str) -> None:
        """Open a cmux browser pane."""
        if not is_cmux():
            self._add_message("Not running in cmux.", role="error")
            return
        result = await cmux.browser_open(arg)
        self._add_message(f"Browser opened: {result or arg or '(empty)'}", role="tool")

    async def _cmd_agents(self, arg: str) -> None:
        """Inspect delegated orchestration runs."""
        from artel_core.delegation.formatting import format_run_detail, format_run_list
        from artel_core.delegation.registry import get_registry

        command = arg.strip()
        if self.remote_url:
            sid = self._remote_session_id
            try:
                if not command or command in {"list", "ls"}:
                    payload = await self._remote_control().request(
                        "GET", f"/api/sessions/{sid}/delegates"
                    )
                    delegates = payload.get("delegates", [])
                    if delegates:
                        counts: dict[str, int] = {}
                        for item in delegates:
                            status = str(item.get("status", ""))
                            counts[status] = counts.get(status, 0) + 1
                        summary = ", ".join(f"{name}={counts[name]}" for name in sorted(counts))
                        output = (
                            f"Orchestration runs: {len(delegates)} total ({summary})\n"
                            + "\n".join(
                                f"- {item['id']} [{item['status']}] ({item['mode']}) {item['task']}"
                                + (
                                    f" — {item.get('latest_update', '')}"
                                    if item.get("latest_update")
                                    else ""
                                )
                                for item in delegates
                            )
                        )
                    else:
                        output = "No orchestration runs found."
                else:
                    parts = shlex.split(command)
                    if parts[0] == "show" and len(parts) == 2:
                        payload = await self._remote_control().request(
                            "GET", f"/api/sessions/{sid}/delegates/{parts[1]}"
                        )
                        delegate = payload.get("delegate", {})
                        output = "Orchestration run:\n" + "\n".join(
                            [
                                f"- id: {delegate.get('id', '')}",
                                f"- parent_session_id: {delegate.get('parent_session_id', '')}",
                                f"- status: {delegate.get('status', '')}",
                                f"- model: {delegate.get('model', '')}",
                                f"- mode: {delegate.get('mode', '')}",
                                f"- project_dir: {delegate.get('project_dir', '')}",
                                f"- created_at: {delegate.get('created_at', '')}",
                                f"- task: {delegate.get('task', '')}",
                            ]
                        )
                        if delegate.get("result"):
                            output += f"\n\nResult:\n{delegate['result']}"
                        if delegate.get("error"):
                            output += f"\n\nError:\n{delegate['error']}"
                    elif parts[0] == "tail" and len(parts) == 2:
                        payload = await self._remote_control().request(
                            "GET", f"/api/sessions/{sid}/delegates/{parts[1]}"
                        )
                        delegate = payload.get("delegate", {})
                        events = delegate.get("events", []) or []
                        lines = [f"Tail for orchestration run {parts[1]}:"]
                        lines.extend(f"- {item}" for item in events[-10:])
                        if delegate.get("latest_update"):
                            lines.append("")
                            lines.append(f"Latest: {delegate['latest_update']}")
                        output = "\n".join(lines)
                    elif parts[0] == "cancel" and len(parts) == 2:
                        payload = await self._remote_control().request(
                            "POST", f"/api/sessions/{sid}/delegates/{parts[1]}/cancel", json_data={}
                        )
                        output = (
                            f"Cancelled orchestration run: {parts[1]}"
                            if payload.get("cancelled")
                            else f"Failed to cancel orchestration run: {parts[1]}"
                        )
                    else:
                        output = (
                            "Usage:\n"
                            "  /delegates\n"
                            "  /delegates list\n"
                            "  /delegates show <run_id>\n"
                            "  /delegates tail <run_id>\n"
                            "  /delegates cancel <run_id>\n\n"
                            "Alias: /agents"
                        )
            except Exception as exc:
                self._add_message(f"agents error: {exc}", role="error")
                return
            self._add_message(output, role="tool")
            return

        registry = get_registry()
        session_id = self._session.session_id if self._session is not None else ""
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            self._add_message(f"agents error: {exc}", role="error")
            return
        if not parts or parts[0] in {"list", "ls"}:
            runs = registry.list_runs(session_id)
            rendered = format_run_list(runs)
            if rendered.startswith("Delegates:"):
                rendered = rendered.replace("Delegates:", "Orchestration runs:", 1)
            elif rendered == "No delegates found.":
                rendered = "No orchestration runs found."
            self._add_message(rendered, role="tool")
            return
        if parts[0] == "show" and len(parts) == 2:
            run = registry.get_session_run(session_id, parts[1])
            if run is None:
                self._add_message(
                    f"delegates error: Unknown orchestration run: {parts[1]}", role="error"
                )
                return
            rendered = format_run_detail(run)
            if rendered.startswith("Delegate:"):
                rendered = rendered.replace("Delegate:", "Orchestration run:", 1)
            self._add_message(rendered, role="tool")
            return
        if parts[0] == "tail" and len(parts) == 2:
            run = registry.get_session_run(session_id, parts[1])
            if run is None:
                self._add_message(
                    f"delegates error: Unknown orchestration run: {parts[1]}", role="error"
                )
                return
            lines = [f"Tail for orchestration run {parts[1]}:"]
            lines.extend(f"- {item}" for item in run.events[-10:])
            if run.latest_update:
                lines.append("")
                lines.append(f"Latest: {run.latest_update}")
            self._add_message("\n".join(lines), role="tool")
            return
        if parts[0] == "cancel" and len(parts) == 2:
            run = registry.get_session_run(session_id, parts[1])
            if run is None:
                self._add_message(
                    f"delegates error: Unknown orchestration run: {parts[1]}", role="error"
                )
                return
            registry.cancel(run.id)
            rendered = format_run_detail(run)
            if rendered.startswith("Delegate:"):
                rendered = rendered.replace("Delegate:", "Orchestration run:", 1)
            self._add_message(rendered, role="tool")
            return
        self._add_message(
            "Usage:\n"
            "  /delegates\n"
            "  /delegates list\n"
            "  /delegates show <run_id>\n"
            "  /delegates tail <run_id>\n"
            "  /delegates cancel <run_id>\n\n"
            "Alias: /agents",
            role="tool",
        )

    async def _cmd_schedules(self, arg: str) -> None:
        """Show or control scheduled tasks on the active server."""
        if not self.remote_url:
            self._add_message(
                "Scheduled tasks are managed by the server. Use a managed/remote server session.",
                role="error",
            )
            return
        command = arg.strip()
        try:
            if not command or command in {"list", "ls"}:
                payload = await self._remote_control().list_schedules()
                schedules = payload.get("schedules", [])
                if not schedules:
                    output = "No scheduled tasks configured."
                else:
                    lines = [
                        "Scheduled tasks: "
                        f"{payload.get('count', len(schedules))} total; "
                        f"next={payload.get('next_run_at', '') or '-'}"
                    ]
                    for item in schedules:
                        schedule = item.get("schedule", {})
                        state = item.get("state", {})
                        trigger = (
                            f"every {schedule.get('every_seconds', 0)}s"
                            if schedule.get("kind") == "interval"
                            else str(schedule.get("cron", ""))
                        )
                        lines.append(
                            f"- {schedule.get('id', '')} "
                            f"[{'enabled' if schedule.get('enabled') else 'disabled'}] "
                            f"{schedule.get('kind', '')}={trigger} "
                            f"status={state.get('last_status', 'idle')} "
                            f"next={state.get('next_run_at', '') or '-'}"
                        )
                    output = "\n".join(lines)
            else:
                parts = shlex.split(command)
                if parts[0] == "reload":
                    payload = await self._remote_control().reload_schedules()
                    output = f"Reloaded schedules: {payload.get('count', 0)} configured"
                elif parts[0] == "run" and len(parts) == 2:
                    payload = await self._remote_control().run_schedule(parts[1])
                    output = (
                        f"Triggered schedule: {parts[1]}\n"
                        f"next={payload.get('next_run_at', '') or '-'}"
                    )
                elif parts[0] == "show" and len(parts) == 2:
                    payload = await self._remote_control().list_schedules()
                    found = None
                    for item in payload.get("schedules", []):
                        schedule = item.get("schedule", {})
                        if str(schedule.get("id", "")) == parts[1]:
                            found = item
                            break
                    if found is None:
                        output = f"Unknown schedule: {parts[1]}"
                    else:
                        output = json.dumps(found, indent=2, sort_keys=True)
                else:
                    output = (
                        "Usage:\n"
                        "  /schedules\n"
                        "  /schedules list\n"
                        "  /schedules show <id>\n"
                        "  /schedules run <id>\n"
                        "  /schedules reload"
                    )
        except Exception as exc:
            self._add_message(f"schedules error: {exc}", role="error")
            return
        self._add_message(output, role="tool")

    async def _cmd_mcp(self, arg: str) -> None:
        """Show or reload first-party MCP runtime state."""
        if self.remote_url:
            action = arg.strip().lower()
            try:
                if action == "reload":
                    payload = await self._remote_control().request(
                        "POST", "/api/mcp/reload", json_data={}
                    )
                else:
                    payload = await self._remote_control().request("GET", "/api/mcp")
            except Exception as exc:
                self._add_message(f"mcp error: {exc}", role="error")
                return
            output = str(payload.get("status", "")).strip() or str(payload.get("error", "")).strip()
            self._add_message(output or "(no output)", role="tool")
            return

        try:
            from artel_core.config import load_config
            from artel_core.extensions import ExtensionContext
            from artel_core.mcp_runtime import McpRuntimeManager

            runtime = McpRuntimeManager()
            context = ExtensionContext(
                project_dir=os.getcwd(), runtime="local", config=load_config(os.getcwd())
            )
            await runtime.load(context)
            if arg.strip().lower() == "reload":
                await runtime.reload()
            output = runtime.status_text()
            await runtime.close()
            self._add_message(output or "(no output)", role="tool")
        except Exception as exc:
            self._add_message(f"mcp error: {exc}", role="error")

    async def _cmd_git(self, cmd: str, arg: str) -> None:
        subarg = arg.strip()
        if cmd == "/status":
            subarg = "status"
        elif cmd == "/diff":
            subarg = f"diff {subarg}".strip()
        elif cmd == "/rollback":
            subarg = f"rollback {subarg}".strip()

        parts = subarg.split(maxsplit=1) if subarg else []
        action = parts[0].lower() if parts else "status"
        rest = parts[1].strip() if len(parts) > 1 else ""

        if self.remote_url:
            try:
                if action in {"", "status", "help"}:
                    if action == "help":
                        self._add_message(render_git_help(), role="tool")
                        return
                    payload = await self._remote_control().run_bash(
                        self._remote_session_id, "git status --short --branch"
                    )
                    output = str(payload.get("output", "") or "")
                    rendered = "Git status: clean working tree." if not output.strip() else output
                    self._add_message(
                        rendered
                        if rendered.startswith("Git status")
                        else f"Git status\n\n{rendered}",
                        role="tool",
                    )
                    return
                if action == "diff":
                    command = f"git diff -- {rest}" if rest else "git diff"
                    payload = await self._remote_control().run_bash(
                        self._remote_session_id, command
                    )
                    output = str(payload.get("output", "") or "")
                    target = rest or "working tree"
                    rendered = (
                        f"No unstaged diff for {target}."
                        if not output.strip()
                        else f"Git diff: {target}\n\n```diff\n{output}\n```"
                    )
                    self._add_message(rendered, role="tool")
                    return
                if action == "rollback":
                    if rest == "--all":
                        payload = await self._remote_control().run_bash(
                            self._remote_session_id, "git restore ."
                        )
                        if int(payload.get("exit_code", 0)) == 0:
                            self._add_message("Restored all unstaged changes.", role="tool")
                        else:
                            self._add_message(
                                str(payload.get("output", "") or "git restore failed"), role="error"
                            )
                        return
                    if not rest:
                        self._add_message("Usage: /rollback <path> | /rollback --all", role="error")
                        return
                    payload = await self._remote_control().run_bash(
                        self._remote_session_id, f"git restore -- {rest}"
                    )
                    if int(payload.get("exit_code", 0)) == 0:
                        self._add_message(f"Restored: {rest}", role="tool")
                    else:
                        self._add_message(
                            str(payload.get("output", "") or "git restore failed"), role="error"
                        )
                    return
                self._add_message(render_git_help(), role="tool")
            except Exception as exc:
                self._add_message(f"git error: {exc}", role="error")
            return

        cwd = os.getcwd()
        if action in {"", "status"}:
            self._add_message(render_git_status(cwd=cwd), role="tool")
            return
        if action == "diff":
            self._add_message(render_git_diff(cwd=cwd, pathspec=rest), role="tool")
            return
        if action == "rollback":
            if rest == "--all":
                self._add_message(restore_all(cwd=cwd), role="tool")
                return
            message = restore_path(cwd=cwd, pathspec=rest)
            role = (
                "error"
                if message.startswith("Usage:") or message.startswith("git restore failed:")
                else "tool"
            )
            self._add_message(message, role=role)
            return
        self._add_message(render_git_help(), role="tool")

    async def _cmd_undo(self) -> None:
        if self.remote_url:
            try:
                payload = await self._remote_control().get_session_messages(self._remote_session_id)
            except Exception as exc:
                self._add_message(f"Undo failed: {exc}", role="error")
                return
            messages = (
                payload.get("messages", []) if isinstance(payload.get("messages", []), list) else []
            )
            paths = collect_last_ai_changed_paths(messages)
            if not paths:
                self._add_message("No recent AI file edits found to undo.", role="tool")
                return
            command = "git restore -- " + " ".join(paths)
            try:
                payload = await self._remote_control().run_bash(self._remote_session_id, command)
            except Exception as exc:
                self._add_message(f"Undo failed: {exc}", role="error")
                return
            if int(payload.get("exit_code", 0)) != 0:
                self._add_message(
                    str(payload.get("output", "") or "git restore failed"), role="error"
                )
                return
            self._add_message(
                "Undid latest AI file changes:\n" + "\n".join(f"- {path}" for path in paths),
                role="tool",
            )
            return

        if not self._session:
            self._add_message("No active session.", role="error")
            return
        paths = collect_last_ai_changed_paths(self._session.messages)
        if not paths:
            self._add_message("No recent AI file edits found to undo.", role="tool")
            return
        message = restore_paths(cwd=os.getcwd(), paths=paths)
        role = "error" if message.startswith("git restore failed:") else "tool"
        self._add_message(
            message
            if role == "error"
            else "Undid latest AI file changes:\n" + "\n".join(f"- {path}" for path in paths),
            role=role,
        )

    async def _cmd_rewind(self, arg: str) -> None:
        if not arg.isdigit():
            self._add_message("Usage: /rewind <message_index>", role="error")
            return
        idx = int(arg)
        if self.remote_url:
            try:
                payload = await self._remote_control().fork_session(
                    self._remote_session_id, message_index=idx
                )
            except Exception as exc:
                self._add_message(f"Rewind failed: {exc}", role="error")
                return
            new_session = str(payload.get("session_id", "")).strip()
            if not new_session:
                self._add_message("Rewind failed: missing forked session id.", role="error")
                return
            await self._resume_remote_session(new_session)
            self._add_message(f"Rewound session to message {idx}.", role="tool")
            return

        if not self._store or not self._session:
            self._add_message("No active session.", role="error")
            return
        new_id = str(uuid.uuid4())
        try:
            await self._store.fork_session(self._session.session_id, new_id, up_to_message_idx=idx)
            await self._resume_session(new_id)
        except Exception as exc:
            self._add_message(f"Rewind failed: {exc}", role="error")
            return
        self._add_message(f"Rewound session to message {idx}.", role="tool")

    async def _cmd_wt(self, arg: str) -> None:
        """Manage git worktrees for the current project."""
        if self.remote_url:
            try:
                payload = await self._remote_control().request(
                    "POST",
                    f"/api/sessions/{self._remote_session_id}/wt",
                    json_data={"arg": arg},
                )
            except Exception as exc:
                self._add_message(f"wt error: {exc}", role="error")
                return
            output = str(payload.get("output", "")).strip()
            session = payload.get("session")
            if isinstance(session, dict):
                self._apply_remote_session_state(session)
            self._add_message(output or "(no output)", role="tool")
            return

        project_dir = self._session.project_dir if self._session is not None else os.getcwd()
        from artel_core.worktree import run_worktree_command

        output = await asyncio.to_thread(run_worktree_command, project_dir, arg)
        self._add_message(output or "(no output)", role="tool")

    def _find_saved_server(self, needle: str) -> SavedArtelServer | None:
        normalized = needle.strip().lower()
        if not normalized:
            return None
        for server in self._saved_servers:
            if server.remote_url.lower() == normalized or server.name.lower() == normalized:
                return server
        return None

    async def _cmd_server_add(self, arg: str) -> None:
        if arg.strip():
            message = (
                "/server-add no longer accepts inline arguments. "
                "Use the inline dialog in the left dock."
            )
            self._add_message(message, role="error")
            return
        self._open_server_dock_input(
            mode="add_server",
            title="Add Artel server",
            detail="Enter ws:// or wss:// URL for the server to show in the left dock.",
            placeholder="ws://host:7432",
        )

    async def _cmd_server_remove(self, arg: str) -> None:
        target = arg.strip() or self.remote_url.strip()
        if not target:
            self._add_message("Usage: /server-remove <name-or-url>", role="error")
            return
        server = self._find_saved_server(target)
        if server is None:
            self._add_message(f"Saved server not found: {target}", role="error")
            return
        removed_url = server.remote_url.strip()
        self._dismissed_server_urls.add(removed_url)
        self._saved_servers = remove_saved_server(removed_url)
        if self.remote_url.strip() == removed_url:
            self._saved_servers = [
                item for item in self._saved_servers if item.remote_url.strip() != removed_url
            ]
        await self._refresh_server_dock()
        self._add_message(f"Removed server: {server.name}", role="tool")

    async def _cmd_server_select(self, arg: str) -> None:
        target = arg.strip()
        if not target:
            self._add_message("Usage: /server-select <name-or-url>", role="error")
            return
        server = self._find_saved_server(target)
        if server is None:
            self._add_message(f"Saved server not found: {target}", role="error")
            return
        await self._connect_to_server(
            server.remote_url,
            auth_token=server.auth_token,
            save=False,
        )

    async def _cmd_server_restart(self) -> None:
        """Restart the managed local Artel server for this project."""
        if not self.remote_url:
            self._add_message(
                "Server restart is only available when connected to a managed local server.",
                role="error",
            )
            return
        if not self.remote_url.startswith("ws://127.0.0.1:") and not self.remote_url.startswith(
            "ws://localhost:"
        ):
            self._add_message(
                "Server restart is only supported for local managed Artel servers.",
                role="error",
            )
            return
        try:
            handle = await restart_managed_local_server(os.getcwd())
        except Exception as exc:
            self._add_message(f"Failed to restart managed local server: {exc}", role="error")
            return
        self.remote_url = handle.remote_url
        self.auth_token = handle.auth_token
        self._remote_control_client = None
        self._saved_servers = upsert_saved_server(
            SavedArtelServer(
                name=default_server_name(handle.remote_url),
                remote_url=handle.remote_url,
                auth_token=handle.auth_token,
            )
        )
        self._add_message(f"Managed local Artel server restarted: {handle.remote_url}", role="tool")
        await self._sync_remote_session_state()
        await self._refresh_server_dock()

    # ── Auto-title ───────────────────────────────────────

    @work(thread=False)
    async def _generate_title(self, text: str) -> None:
        """Generate session title in background via small model."""
        if not self._session or not self._store:
            return
        title = await self._session.generate_title(text)
        if title:
            await self._store.rename_session(self._session.session_id, title)

    # ── Permission callback ────────────────────────────────────

    def _permission_panel(self) -> PermissionPanel:
        return self.query_one("#permission-panel", PermissionPanel)

    def _show_next_permission_request(self) -> None:
        if self._active_permission_request is not None:
            return
        if not self._pending_permission_requests:
            self._permission_panel().close_request()
            self.call_after_refresh(self._focus_input)
            return
        self._active_permission_request = self._pending_permission_requests.pop(0)
        self._permission_panel().open_request(
            self._active_permission_request.tool_name,
            self._active_permission_request.tool_args,
        )

    def _resolve_permission_panel_decision(self, decision: str) -> None:
        request = self._active_permission_request
        if request is None:
            return
        self._active_permission_request = None
        if decision == "all":
            self._auto_approve_all = True
        if not request.future.done():
            request.future.set_result(decision)
        if decision == "all":
            for pending in self._pending_permission_requests:
                if not pending.future.done():
                    pending.future.set_result("all")
            self._pending_permission_requests.clear()
        self._permission_panel().close_request()
        self._show_next_permission_request()

    async def _request_permission_decision(self, tool_name: str, args: dict[str, Any]) -> str:
        if self._auto_approve_all:
            return "all"
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending_permission_requests.append(
            PendingPermissionRequest(
                tool_name=tool_name,
                tool_args=dict(args),
                future=future,
            )
        )
        self._show_next_permission_request()
        return await future

    async def _ask_permission(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Show an inline panel and return whether the tool call is allowed."""
        if self._auto_approve_all:
            return True
        # cmux: notify user that permission is needed
        await cmux.set_status(
            "state",
            f"permission: {tool_name}",
            icon="lock",
            color="#fab387",
        )
        await cmux.notify(
            "Artel",
            subtitle=f"Permission required: {tool_name}",
        )
        result = await self._request_permission_decision(tool_name, args)
        if result == "all":
            self._auto_approve_all = True
            return True
        return result == "once"

    # ── Helpers ──────────────────────────────────────────────

    def _add_message(self, content: str, role: str = "assistant") -> MessageWidget:
        container = self.query_one("#chat-container", Vertical)
        widget = MessageWidget(content, role=role)
        widget.set_scroll_callback(self._scroll_to_bottom)
        container.mount(widget)
        if role == "assistant":
            self._assistant_message_history.append(widget)
        self._scroll_to_bottom()
        return widget

    def _add_reasoning_block(self, content: str = "") -> MessageWidget:
        container = self.query_one("#chat-container", Vertical)
        widget = MessageWidget(content, role="reasoning")
        widget.set_scroll_callback(self._scroll_to_bottom)
        collapsible = Collapsible(widget, title="💡 thinking", collapsed=True)
        container.mount(collapsible)
        self._tool_collapsibles.append(collapsible)
        self._scroll_to_bottom()
        return widget

    def _start_tool_card(self, call_id: str, *, title: str, body: str = "") -> ToolCard:
        container = self.query_one("#chat-container", Vertical)
        widget = ToolCard(title, body)
        collapsible = Collapsible(widget, title=title, collapsed=False)
        container.mount(collapsible)
        self._tool_collapsibles.append(collapsible)
        self._active_tool_cards[call_id] = widget
        normalized_title = title[2:].strip() if title.startswith("⚙ ") else title.strip()
        tool_name = normalized_title.split(" ", 1)[0] if normalized_title else "tool"
        self._tool_call_names[call_id] = tool_name or "tool"
        self._scroll_to_bottom()
        return widget

    def _finish_tool_card(
        self,
        call_id: str,
        *,
        title: str,
        body: str,
        markdown: bool = False,
        display: dict[str, Any] | None = None,
        kind: str = "text",
        status_badge: str = "",
        status_variant: str = "neutral",
    ) -> None:
        card = self._active_tool_cards.pop(call_id, None)
        if card is not None:
            card.set_result(
                title=title,
                body=body,
                markdown=markdown,
                display=display,
                kind=kind,
                status_badge=status_badge,
                status_variant=status_variant,
            )
            self._scroll_to_bottom()
            return
        container = self.query_one("#chat-container", Vertical)
        widget = ToolCard(
            "⚙ tool",
            "",
            result_title=title,
            result_body=body,
            result_markdown=markdown,
            result_display=display,
            result_kind=kind,
            result_status_badge=status_badge,
            result_status_variant=status_variant,
        )
        collapsible = Collapsible(widget, title=title, collapsed=False)
        container.mount(collapsible)
        self._tool_collapsibles.append(collapsible)
        self._scroll_to_bottom()

    def _render_restored_message(
        self,
        *,
        role: str,
        content: str,
        reasoning: str = "",
        attachments: list[ImageAttachment] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_result: dict[str, Any] | None = None,
    ) -> None:
        rendered = (
            self._render_user_submission(content, attachments)
            if role == Role.USER.value
            else content
        )
        if role == Role.USER.value:
            self._add_message(rendered, role="user")
        elif role == Role.ASSISTANT.value:
            if reasoning:
                self._add_reasoning_block(reasoning)
            if rendered:
                self._add_message(rendered, role="assistant")
            if tool_calls:
                for tool_call in tool_calls:
                    if not isinstance(tool_call, dict):
                        continue
                    tool_name = str(tool_call.get("name", "") or "tool").strip() or "tool"
                    tool_args = tool_call.get("arguments")
                    if not isinstance(tool_args, dict):
                        tool_args = {}
                    tool_call_id = str(tool_call.get("id", "") or uuid.uuid4())
                    tool_display = format_tool_call_display(tool_name, tool_args)
                    self._tool_call_names[tool_call_id] = tool_name
                    self._start_tool_card(
                        tool_call_id,
                        title=tool_display.title,
                        body=tool_display.body,
                    )
        elif role == Role.SYSTEM.value and rendered:
            self._add_message("📋 [Restored session]", role="tool")
        elif role == Role.TOOL.value:
            if tool_result:
                tool_call_id = str(tool_result.get("tool_call_id", "") or "")
                matched_tool_name = self._tool_call_names.get(tool_call_id, "tool")
                result_display = format_tool_result_display(
                    tool_name=matched_tool_name,
                    content=str(tool_result.get("content", "") or content),
                    is_error=bool(tool_result.get("is_error", False)),
                    display=tool_result.get("display")
                    if isinstance(tool_result.get("display"), dict)
                    else None,
                )
                self._finish_tool_card(
                    str(tool_result.get("tool_call_id", "") or uuid.uuid4()),
                    title=result_display.title,
                    body=result_display.body,
                    markdown=result_display.markdown,
                    display=tool_result.get("display")
                    if isinstance(tool_result.get("display"), dict)
                    else None,
                    kind=result_display.kind,
                    status_badge=result_display.status_badge,
                    status_variant=result_display.status_variant,
                )
            elif rendered:
                self._add_message(rendered, role="tool")
        elif rendered:
            self._add_message(rendered, role=role)

    def _last_assistant_message_text(self) -> str:
        for widget in reversed(self._assistant_message_history):
            content = getattr(widget, "content", "")
            if content:
                return str(content)
        return ""

    def action_copy_last_assistant_message(self) -> None:
        content = self._last_assistant_message_text().strip()
        if not content:
            self._add_message("No assistant message available to copy.", role="tool")
            return
        self.copy_to_clipboard(content)
        self._add_message("Copied last assistant message to clipboard.", role="tool")

    def _scroll_to_bottom(self) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.scroll_end(animate=False)

    def _remote_connect_headers(self) -> dict[str, str]:
        if not self.auth_token:
            return {}
        return {"Authorization": f"Bearer {self.auth_token}"}

    def _remote_message_payload(
        self,
        text: str,
        attachments: list[ImageAttachment] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "type": "message",
            "content": text,
            "session_id": self._remote_session_id,
        }
        if attachments:
            payload["attachments"] = [
                {
                    "path": attachment.path,
                    "mime_type": attachment.mime_type,
                    "name": attachment.name,
                }
                for attachment in attachments
            ]
        return payload

    async def _send_remote_event(self, payload: dict[str, Any]) -> None:
        import websockets

        if not self._ws:
            self._ws = await websockets.connect(
                self.remote_url,
                additional_headers=self._remote_connect_headers(),
            )
        await self._ws.send(json.dumps(payload))

    def action_toggle_tools(self) -> None:
        """Ctrl+O: toggle all tool output collapsibles."""
        for c in self._tool_collapsibles:
            c.collapsed = not c.collapsed

    def action_server_dock_actions(self) -> None:
        data = self._server_dock_selected_data()
        if data is None:
            self._set_server_dock_status("Select a server, project, or session first")
            return
        self._schedule_background_task(
            self._open_server_dock_actions(data),
            exclusive=False,
            thread=False,
        )

    def action_toggle_server_dock(self) -> None:
        self._server_dock_visible = not self._server_dock_visible
        self._server_dock().set_visible(self._server_dock_visible)
        self._set_server_dock_status(
            "Server dock visible" if self._server_dock_visible else "Server dock hidden"
        )

    def action_toggle_sidebar(self) -> None:
        self._sidebar_visible = not self._sidebar_visible
        self._board_sidebar().set_visible(self._sidebar_visible)
        if self._sidebar_visible:
            self._board_sidebar().set_status("Board visible")

    def action_focus_tasks(self) -> None:
        if not self._sidebar_visible:
            self.action_toggle_sidebar()
        self._board_sidebar().focus_tasks()

    def action_focus_notes(self) -> None:
        if not self._sidebar_visible:
            self.action_toggle_sidebar()
        self._board_sidebar().focus_notes()

    async def action_quit(self) -> None:
        """Override default quit to clean up async resources."""
        await self._cleanup()
        self.exit()

    async def _cleanup(self) -> None:
        """Close all open async resources."""
        self._cancel_board_save_task(self._tasks_save_task)
        self._cancel_board_save_task(self._notes_save_task)
        if self._delegation_events_task is not None:
            self._delegation_events_task.cancel()
            with suppress(Exception):
                await self._delegation_events_task
            self._delegation_events_task = None
        await self._close_store()
        if self._session and self._session.provider:
            with suppress(Exception):
                await self._session.provider.close()
            mcp_runtime = getattr(self._session, "mcp_runtime", None)
            if mcp_runtime is not None:
                with suppress(Exception):
                    await mcp_runtime.close()
            lsp_runtime = getattr(self._session, "lsp_runtime", None)
            if lsp_runtime is not None:
                with suppress(Exception):
                    await lsp_runtime.close()
        if self._ws:
            with suppress(Exception):
                await self._ws.close()
            self._ws = None

    async def action_clear(self) -> None:
        container = self.query_one("#chat-container", Vertical)
        container.remove_children()
        self._tool_collapsibles.clear()
        self._active_tool_cards.clear()
        self._hide_command_menu()
        self._clear_pending_attachments()
        if self.remote_url:
            self._remote_session_id = str(uuid.uuid4())
            self._remote_rule_overrides = {}
            await self._sync_remote_session_state()
        if self._session:
            self._local_rule_overrides = SessionRuleOverrides.empty()
            self._session.rule_overrides = self._local_rule_overrides
            self._session.refresh_system_prompt()
            new_id = str(uuid.uuid4())
            if self._store:
                await self._store.create_session(
                    new_id,
                    self._provider_model,
                    project_dir=os.getcwd(),
                    thinking_level=self._session.thinking_level,
                )
            self._session.session_id = new_id
            self._session.messages = self._session.messages[:1]
            self._set_footer_thinking_level(str(self._session.thinking_level))
        await self._load_board_state()
        await self._refresh_server_dock()
        self.call_after_refresh(self._focus_input)




def run_tui(
    remote_url: str = "",
    auth_token: str = "",
    forward_credentials: str = "",
    continue_session: bool = False,
    resume_id: str = "",
) -> None:
    """Entry point for the TUI."""
    app = ArtelApp(
        remote_url=remote_url,
        auth_token=auth_token,
        forward_credentials=forward_credentials,
        continue_session=continue_session,
        resume_id=resume_id,
    )
    app.run()
