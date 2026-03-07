"""Worker TUI — Textual application."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import tempfile
import urllib.parse
import uuid
import webbrowser
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MethodType
from typing import Any

import worker_core.cmux as cmux
from rich.markdown import Markdown
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Header,
    Input,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option
from worker_ai.models import Role
from worker_core.agent import AgentEventType, AgentSession
from worker_core.bootstrap import (
    bootstrap_runtime,
    create_agent_session_from_bootstrap,
    provider_requires_api_key,
)
from worker_core.cmux import is_cmux
from worker_core.config import load_config, resolve_model
from worker_core.extensions import (
    ExtensionContext,
    load_tui_extensions_async,
    reload_tui_extensions_async,
)
from worker_core.prompts import load_prompts, render_prompt
from worker_core.provider_resolver import (
    get_effective_model_info,
    get_effective_provider_catalog,
    get_provider_config,
    get_provider_env_vars,
)
from worker_core.sessions import SessionStore
from worker_core.skills import inject_skill, load_skills
from worker_core.tools.builtins import create_builtin_tools

from worker_tui.credential_forwarding import collect_forward_credentials
from worker_tui.remote_control import RemoteControlClient


@dataclass(frozen=True, slots=True)
class ProviderSetupEntry:
    """A provider entry shown by the /providers command."""

    id: str
    name: str
    status: str
    hint: str


def _provider_ids_for_listing(config: Any) -> list[str]:
    from worker_ai.provider_specs import iter_provider_specs

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
    from worker_ai.oauth import list_oauth_provider_names
    from worker_ai.provider_specs import get_provider_spec

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
    from worker_ai.oauth import list_oauth_provider_names
    from worker_ai.provider_specs import get_provider_spec

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


BUILTIN_COMMAND_SUGGESTIONS: tuple[SlashCommandSuggestion, ...] = (
    SlashCommandSuggestion("/help", "show available commands"),
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
    SlashCommandSuggestion("/split", "open a cmux split pane"),
    SlashCommandSuggestion("/browser", "open a cmux browser pane"),
    SlashCommandSuggestion("/clear", "clear chat and start a new session"),
    SlashCommandSuggestion("/quit", "exit the TUI"),
)


class MessageWidget(Static):
    """A single message bubble in the chat."""

    DEFAULT_CSS = """
    MessageWidget {
        margin: 0 1;
        padding: 0 1;
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
    .tool-message {
        background: $surface;
        color: $text-muted;
        text-style: italic;
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
        self.add_class(f"{role}-message")

    def render(self) -> Markdown | Text:
        if self.role == "user":
            return Text(f"❯ {self._content}")
        if self.role == "tool":
            return Text(self._content)
        if self.role == "error":
            return Text(f"✗ {self._content}")
        # assistant — render markdown
        try:
            return Markdown(self._content)
        except Exception:
            return Text(self._content)

    def append_content(self, delta: str) -> None:
        self._content += delta
        self.refresh(layout=True)


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
        self._total_input: int = 0
        self._total_output: int = 0
        self._total_cost: float = 0.0
        self._context_pct: float = 0.0
        self._cwd: str = ""
        self._in_cmux: bool = is_cmux()

    def render(self) -> Text:
        parts: list[str] = []
        if self._model:
            parts.append(self._model)
        parts.append(f"{self._total_input + self._total_output} tok")
        if self._total_cost > 0:
            parts.append(f"${self._total_cost:.4f}")
        if self._context_pct > 0:
            parts.append(f"ctx {self._context_pct:.0%}")
        # Show current working directory (~ for home)
        cwd = self._cwd or os.getcwd()
        home = os.path.expanduser("~")
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home):]
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
            input_tokens * input_price / 1_000_000
            + output_tokens * output_price / 1_000_000
        )
        self.refresh()

    def update_context_pct(self, estimated_tokens: int, context_window: int) -> None:
        if context_window > 0:
            self._context_pct = estimated_tokens / context_window
        self.refresh()

    def set_model(self, model: str) -> None:
        self._model = model
        self.refresh()

    def set_cwd(self, cwd: str) -> None:
        self._cwd = cwd
        self.refresh()


# ── Permission dialog ─────────────────────────────────────────


class PermissionScreen(ModalScreen[str]):
    """Modal dialog asking to approve / deny a tool call."""

    CSS = """
    PermissionScreen {
        align: center middle;
    }
    #perm-dialog {
        width: 70;
        max-height: 18;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }
    #perm-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #perm-detail {
        max-height: 6;
        margin-bottom: 1;
        color: $text-muted;
    }
    #perm-buttons {
        height: 3;
        align: center middle;
    }
    #perm-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("y", "approve_once", "Allow once", show=False),
        Binding("a", "approve_all", "Allow all", show=False),
        Binding("n", "deny", "Deny", show=False),
        Binding("escape", "deny", "Deny", show=False),
    ]

    def __init__(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.tool_args = args

    def compose(self) -> ComposeResult:
        detail = ""
        if self.tool_name == "bash":
            detail = self.tool_args.get("command", "")
        else:
            detail = ", ".join(f"{k}={v!r}" for k, v in self.tool_args.items())
        with Vertical(id="perm-dialog"):
            yield Static(f"⚠ Permission required: [b]{self.tool_name}[/b]", id="perm-title")
            yield Static(detail[:300], id="perm-detail")
            with Horizontal(id="perm-buttons"):
                yield Button("[y] Allow once", id="btn-once", variant="primary")
                yield Button("[a] Allow all", id="btn-all", variant="success")
                yield Button("[n] Deny", id="btn-deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-once":
            self.dismiss("once")
        elif event.button.id == "btn-all":
            self.dismiss("all")
        else:
            self.dismiss("deny")

    def action_approve_once(self) -> None:
        self.dismiss("once")

    def action_approve_all(self) -> None:
        self.dismiss("all")

    def action_deny(self) -> None:
        self.dismiss("deny")


class TextInputScreen(ModalScreen[str | None]):
    """Modal dialog asking the user to paste or type a short value."""

    CSS = """
    TextInputScreen {
        align: center middle;
    }
    #text-input-dialog {
        width: 80;
        max-height: 18;
        padding: 1 2;
        background: $surface;
        border: thick $primary;
    }
    #text-input-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #text-input-detail {
        margin-bottom: 1;
        color: $text-muted;
    }
    #text-input-field {
        margin-bottom: 1;
    }
    #text-input-buttons {
        height: 3;
        align: center middle;
    }
    #text-input-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    def __init__(
        self,
        title: str,
        detail: str,
        *,
        placeholder: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._detail = detail
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(id="text-input-dialog"):
            yield Static(self._title, id="text-input-title")
            yield Static(self._detail, id="text-input-detail")
            yield Input(placeholder=self._placeholder, id="text-input-field")
            with Horizontal(id="text-input-buttons"):
                yield Button("Submit", id="btn-submit", variant="primary")
                yield Button("Cancel", id="btn-cancel", variant="error")

    def on_mount(self) -> None:
        self.query_one("#text-input-field", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-submit":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "text-input-field":
            self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        value = self.query_one("#text-input-field", Input).value.strip()
        self.dismiss(value or None)


# ── Main App ──────────────────────────────────────────────────


class WorkerApp(App):
    """Textual TUI for the Worker coding agent."""

    TITLE = "Worker"

    CSS = """
    #main-content {
        height: 1fr;
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
        height: auto;
        max-height: 6;
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("ctrl+o", "toggle_tools", "Toggle tools"),
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
        self._prompts: dict[str, str] = {}  # loaded prompt templates
        self._skills: dict[str, Any] = {}  # loaded skills (Skill objects)
        self._active_theme: str = "dark"
        self._tool_collapsibles: list[Collapsible] = []
        self._input_price: float = 0.0  # per 1M tokens
        self._output_price: float = 0.0
        self._auto_approve_all: bool = False
        self._provider_model: str = ""  # "provider/model" for DB storage
        self._suppress_next_command_menu_update: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="main-content"):
            with VerticalScroll(id="chat-scroll"):
                yield Vertical(id="chat-container")
            yield OptionList(id="command-suggestions", compact=True)
            yield Input(placeholder="Type a message... (Enter to send)", id="input-bar")
            yield StatusFooter(id="status-footer")
        yield Footer()

    async def on_mount(self) -> None:
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

        # Apply custom keybindings from config
        for key, action in config.keybindings.bindings.items():
            self.bind(key, action, description=action)

        if self.remote_url:
            await self._sync_remote_session_state()
            if self._forward_credentials_spec:
                await self._forward_remote_credentials(config)

        self.call_after_refresh(self._focus_input)

    def _focus_input(self) -> None:
        """Keep the main input focused for immediate typing."""
        self.query_one("#input-bar", Input).focus()

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
        project_dir = str(session.get("project_dir", "")).strip()
        if project_dir:
            self._remote_project_dir = project_dir
            footer.set_cwd(project_dir)

    async def _sync_remote_session_state(self) -> None:
        if not self.remote_url:
            return
        try:
            payload = await self._remote_control().get_session(self._remote_session_id)
        except Exception:
            return
        session = payload.get("session", {})
        self._apply_remote_session_state(session)

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
                        item.get("provider", "")
                        for item in imported
                        if item.get("provider")
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

    def _register_tui_extension_keybindings(self, ext: Any) -> None:
        """Bind dynamic keybindings exported by TUI extensions."""
        for index, (key, handler) in enumerate(ext.get_keybindings().items()):
            ext_name = getattr(ext, "name", "") or ext.__class__.__name__.lower()
            action_name = f"ext_{ext_name}_{index}"

            async def _action(self: WorkerApp, _handler: Callable[..., Any] = handler) -> None:
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
                suggestions.append(
                    SlashCommandSuggestion(f"/{name}", "extension command")
                )

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
        if not query.startswith("/") or " " in query:
            return []
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
                f"{suggestion.value} — "
                f"{self._truncate_command_description(suggestion.description)}",
                id=suggestion.value,
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
        input_bar = self.query_one("#input-bar", Input)
        command = command or self._selected_command_suggestion()
        if not command:
            return False
        if only_if_completion_needed and input_bar.value.strip() == command:
            return False
        self._suppress_next_command_menu_update = True

        input_bar.value = command
        input_bar.cursor_position = len(command)
        self._hide_command_menu()
        self.call_after_refresh(self._focus_input)
        return True

    def on_key(self, event: events.Key) -> None:
        input_bar = self.query_one("#input-bar", Input)
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

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "input-bar":
            return
        if self._suppress_next_command_menu_update:
            self._suppress_next_command_menu_update = False
            self._hide_command_menu()
            return
        self._update_command_menu(event.value)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id != "command-suggestions":
            return
        self._apply_command_suggestion(event.option.id)
        event.stop()

    async def _init_local_session(self) -> None:
        from worker_core.cli import _resolve_api_key

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
                session_id, f"{provider_name}/{model_id}", project_dir=project_dir,
            )
        self._session = create_agent_session_from_bootstrap(
            config,
            runtime,
            project_dir=project_dir,
            store=self._store,
            session_id=session_id,
            permission_callback=self._ask_permission,
        )

        # Restore prior messages and display them
        if prior_messages:
            self._session.messages.extend(prior_messages)
            for msg in prior_messages:
                if msg.role == Role.USER:
                    self._add_message(msg.content, role="user")
                elif msg.role == Role.ASSISTANT and msg.content:
                    self._add_message(msg.content, role="assistant")
                elif msg.role == Role.SYSTEM and msg.content:
                    self._add_message("\U0001f4cb [Restored session]", role="tool")

        self._provider_model = f"{provider_name}/{model_id}"
        self.sub_title = self._provider_model
        self.query_one("#status-footer", StatusFooter).set_model(self._provider_model)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        if self._command_menu_visible() and self._apply_command_suggestion(
            only_if_completion_needed=True
        ):
            return

        event.input.value = ""
        self._hide_command_menu()
        self.call_after_refresh(self._focus_input)

        # Handle bash commands: !! = local only, ! = send output to LLM
        if text.startswith("!!"):
            cmd = text[2:].strip()
            if cmd:
                self._add_message(f"$ {cmd}", role="user")
                if self.remote_url:
                    self._run_remote_bash(cmd, send_to_llm=False)
                else:
                    self._run_bash(cmd, send_to_llm=False)
            return
        if text.startswith("!"):
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
            await self._handle_command(text)
            return

        self._add_message(text, role="user")

        # Auto-title session from first user message (async, non-blocking)
        if self._store and self._session and not text.startswith("/"):
            info = await self._store.get_session(self._session.session_id)
            if info and not info.title:
                self._generate_title(text)

        if self.remote_url:
            self._run_remote(text)
        else:
            self._run_local(text)

    async def _handle_command(self, text: str) -> None:
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd == "/clear":
            await self.action_clear()
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
                "  /split [dir]        — open cmux split pane (cmux only)\n"
                "  /browser [url]      — open browser pane (cmux only)\n"
                "  /clear              — clear chat & start new session\n"
                "  /quit               — exit\n"
                "  ! <command>         — run cmd on the active host & send output to LLM\n"
                "  !! <command>        — run cmd on the active host\n"
                "  Ctrl+O              — toggle tool output",
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
            self._cmd_compact(arg)
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
            self._cmd_skill(cmd[7:])  # strip "/skill:"
        elif cmd == "/skills":
            self._cmd_skills_list()
        elif cmd == "/theme":
            self._cmd_theme(arg)
        elif cmd == "/thinking":
            await self._cmd_thinking(arg)
        elif cmd == "/export":
            self._cmd_export(arg)
        elif cmd == "/split":
            await self._cmd_split(arg)
        elif cmd == "/browser":
            await self._cmd_browser(arg)
        elif cmd == "/reload":
            await self._cmd_reload()
        else:
            # Check prompt templates as /name commands
            cmd_name = cmd.lstrip("/")
            if cmd_name in self._prompts:
                self._cmd_use_prompt(cmd_name, arg)
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

    @work(exclusive=True, thread=False)
    async def _run_local(self, text: str) -> None:
        """Run a query through the local agent session."""
        if not self._session:
            self._add_message("Session not initialized.", role="error")
            return

        widget: MessageWidget | None = None
        reasoning_widget: MessageWidget | None = None
        had_tool_calls = False

        # cmux: set status to thinking
        await cmux.set_status("state", "thinking", icon="brain", color="#89b4fa")

        async for event in self._session.run(text):
            if event.type == AgentEventType.REASONING_DELTA:
                # Show thinking in a collapsible block
                if reasoning_widget is None:
                    reasoning_widget = MessageWidget("", role="reasoning")
                    container = self.query_one("#chat-container", Vertical)
                    collapsible = Collapsible(
                        reasoning_widget, title="💡 thinking", collapsed=True,
                    )
                    container.mount(collapsible)
                    self._tool_collapsibles.append(collapsible)
                reasoning_widget.append_content(event.content)

            elif event.type == AgentEventType.TEXT_DELTA:
                # After tool calls, create a new widget so text appears AFTER tools
                if widget is None or had_tool_calls:
                    widget = self._add_message("", role="assistant")
                    had_tool_calls = False
                    reasoning_widget = None  # reset for next turn
                widget.append_content(event.content)
                self.call_after_refresh(self._scroll_to_bottom)

            elif event.type == AgentEventType.TOOL_CALL:
                had_tool_calls = True
                tool_args = ", ".join(
                    f"{k}={v!r}" for k, v in event.tool_args.items()
                )
                tool_label = f"⚙ {event.tool_name}({tool_args})"
                self._add_tool_message(tool_label)
                # cmux: update status to tool call
                await cmux.set_status(
                    "state",
                    f"tool: {event.tool_name}",
                    icon="gear",
                    color="#f9e2af",
                )
                await cmux.log(f"tool: {event.tool_name}", source="worker")

            elif event.type == AgentEventType.TOOL_RESULT:
                output = event.content[:200] + "..." if len(event.content) > 200 else event.content
                self._add_tool_message(f"  → {output}")

            elif event.type == AgentEventType.ERROR:
                self._add_message(event.error, role="error")
                await cmux.log(event.error, level="error", source="worker")

            elif event.type == AgentEventType.COMPACT:
                self._add_message("\U0001f4cb Session auto-compacted.", role="tool")

            elif event.type == AgentEventType.DONE:
                footer = self.query_one("#status-footer", StatusFooter)
                if event.usage:
                    footer.update_usage(
                        event.usage.input_tokens,
                        event.usage.output_tokens,
                        self._input_price,
                        self._output_price,
                    )
                # Update context % in footer
                if self._session:
                    est = self._session._estimate_tokens()
                    footer.update_context_pct(est, self._session.context_window)
                    # cmux: update progress bar with context usage
                    if self._session.context_window > 0:
                        pct = est / self._session.context_window
                        await cmux.set_progress(min(pct, 1.0), label=f"ctx {pct:.0%}")

        # cmux: set status to idle, notify completion
        await cmux.set_status("state", "idle", icon="check", color="#a6e3a1")
        await cmux.notify("Worker", subtitle="Task complete")

        self._scroll_to_bottom()

    @work(exclusive=True, thread=False)
    async def _run_remote(self, text: str) -> None:
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
        await self._ws.send(json.dumps(self._remote_message_payload(text)))

        widget = self._add_message("", role="assistant")

        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                msg_type = msg.get("type", "")

                if msg_type == "text_delta":
                    widget.append_content(msg.get("content", ""))
                elif msg_type == "tool_call":
                    self._add_message(f"⚙ {msg.get('tool', '')}", role="tool")
                elif msg_type == "tool_result":
                    output = msg.get("output", "")
                    if len(output) > 200:
                        output = output[:200] + "..."
                    self._add_message(f"  → {output}", role="tool")
                elif msg_type == "error":
                    self._add_message(msg.get("error", "Unknown error"), role="error")
                elif msg_type == "done":
                    usage = msg.get("usage")
                    if usage:
                        self._add_message(
                            f"tokens: {usage.get('input', 0)} in / {usage.get('output', 0)} out",
                            role="tool",
                        )
                    break
        except Exception as e:
            self._add_message(f"Connection error: {e}", role="error")
            self._ws = None

        self._scroll_to_bottom()

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
        from worker_core.cli import _resolve_api_key

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
        from worker_core.cli import _resolve_api_key

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
            self._add_message(f"Switched to {self._provider_model}", role="tool")
            return
        if "/" not in model_str:
            self._add_message(
                "Format: provider/model-id (e.g. anthropic/claude-sonnet-4-20250514)",
                role="error",
            )
            return

        from worker_core.cli import _resolve_api_key

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

        # Close old provider
        if self._session:
            await self._session.provider.close()
        session_id = str(uuid.uuid4())
        if self._store:
            await self._store.create_session(
                session_id, f"{provider_name}/{model_id}", project_dir=os.getcwd(),
            )
        self._session = create_agent_session_from_bootstrap(
            config,
            runtime,
            project_dir=os.getcwd(),
            store=self._store,
            session_id=session_id,
            permission_callback=self._ask_permission,
        )

        # Restore prior messages into new session
        if prior_messages:
            self._session.messages.extend(prior_messages)

        self._provider_model = f"{provider_name}/{model_id}"
        self.sub_title = self._provider_model
        self.query_one("#status-footer", StatusFooter).set_model(self._provider_model)
        self._add_message(f"Switched to {self._provider_model}", role="tool")

    # ── Provider login ────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def _run_connect(self, provider_name: str) -> None:
        """Run OAuth login for a provider."""
        if self.remote_url:
            await self._run_remote_connect(provider_name)
            return
        from worker_ai.oauth import get_oauth_provider, list_oauth_provider_names
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
        from worker_ai.oauth import get_oauth_provider, list_oauth_provider_names
        from worker_ai.provider_specs import get_provider_spec

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
            code = await self.push_screen_wait(
                TextInputScreen(
                    f"{provider_name.capitalize()} authorization",
                    "Paste the authorization code from the browser.",
                    placeholder="authorization code",
                )
            )
            if not code:
                self._add_message("Remote login cancelled.", role="tool")
                return
            await self._remote_control().complete_oauth(
                str(payload.get("login_id", "")),
                {"code": code},
            )
            self._add_message(
                f"{provider_name.capitalize()} authorized on the remote server!",
                role="tool",
            )
        except Exception as exc:
            self._add_message(f"Remote login failed: {exc}", role="error")

    async def _run_remote_forwarded_oauth_login(self, provider_name: str, config: Any) -> None:
        from worker_ai.oauth import TokenStore, get_oauth_provider

        with tempfile.TemporaryDirectory(prefix="worker-remote-oauth-") as temp_dir:
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
                        callback_future.set_exception(
                            RuntimeError("Missing authorization code.")
                        )
                body = (
                    "<html><body><h1>Authorized!</h1>"
                    "<p>You can close this tab and return to Worker.</p>"
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
        project_dir = str(session.get("project_dir", "")).strip() or arg
        self._add_message(f"Switched remote project to: {project_dir}", role="tool")
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

        container = self.query_one("#chat-container", Vertical)
        container.remove_children()
        self._tool_collapsibles.clear()

        for message in messages_payload.get("messages", []):
            role = str(message.get("role", ""))
            content = str(message.get("content", ""))
            if role == Role.USER.value:
                self._add_message(content, role="user")
            elif role == Role.ASSISTANT.value and content:
                self._add_message(content, role="assistant")
            elif role == Role.SYSTEM.value and content:
                self._add_message("\U0001f4cb [Restored session]", role="tool")

        title = str(session.get("title", "")).strip() or session_id[:8]
        self._add_message(f"Resumed remote session: {title}", role="tool")

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
                proj = "~" + proj[len(home):]
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

        # Update session
        self._session.session_id = session_id
        self._session.messages = [self._session.messages[0]]  # Keep system prompt
        self._session.messages.extend(messages)

        # Display restored messages
        for msg in messages:
            if msg.role == Role.USER:
                self._add_message(msg.content, role="user")
            elif msg.role == Role.ASSISTANT and msg.content:
                self._add_message(msg.content, role="assistant")
            elif msg.role == Role.SYSTEM and msg.content:
                self._add_message("\U0001f4cb [Restored session]", role="tool")

        title = info.title if info else session_id[:8]
        self._add_message(f"Resumed session: {title}", role="tool")

    @work(exclusive=True, thread=False)
    async def _cmd_compact(self, custom_prompt: str = "") -> None:
        """Compact conversation history."""
        if not self._session:
            self._add_message("No active session.", role="error")
            return

        self._add_message("Compacting conversation history...", role="tool")
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
        if not self._store or not self._session:
            self._add_message("No active session.", role="error")
            return
        await self._store.rename_session(self._session.session_id, title)
        self._add_message(f"Session renamed to: {title}", role="tool")

    async def _cmd_tree(self) -> None:
        """Show message tree for current session."""
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
        if not self._store or not self._session:
            self._add_message("No active session.", role="error")
            return

        if not arg.isdigit():
            self._add_message(
                "Usage: /fork <message_index>  (see /tree for indices)", role="error",
            )
            return

        idx = int(arg)
        new_id = str(uuid.uuid4())
        try:
            await self._store.fork_session(
                self._session.session_id, new_id, up_to_message_idx=idx,
            )
            self._add_message(
                f"Forked session at message {idx}. "
                f"New session ID: {new_id[:8]}\u2026 Use /resume to switch.",
                role="tool",
            )
        except Exception as e:
            self._add_message(f"Fork failed: {e}", role="error")

    # ── Prompt template commands ──────────────────────────────

    def _cmd_prompts(self) -> None:
        """List available prompt templates."""
        if not self._prompts:
            self._add_message("No prompt templates found.\n"
                              "Place .md files in ~/.config/worker/prompts/ "
                              "or .worker/prompts/", role="tool")
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
            self._add_message("No skills found.\n"
                              "Place .md files in ~/.config/worker/skills/ "
                              "or .worker/skills/", role="tool")
            return
        lines = ["Available skills (use /skill:<name> to load):"]
        for sk in sorted(self._skills.values(), key=lambda s: s.name):
            desc = f" — {sk.description}" if sk.description else ""
            lines.append(f"  {sk.name}{desc}")
        self._add_message("\n".join(lines), role="tool")

    def _cmd_skill(self, name: str) -> None:
        """Load a skill into the current session's system prompt."""
        if not self._session:
            self._add_message("No active session.", role="error")
            return

        skill = self._skills.get(name)
        if not skill:
            available = ", ".join(sorted(self._skills)) or "none"
            self._add_message(
                f"Skill '{name}' not found. Available: {available}", role="error",
            )
            return

        # Inject into system prompt
        self._session.system_prompt = inject_skill(
            self._session.system_prompt, skill,
        )
        self._session.messages[0].content = self._session.system_prompt
        self._add_message(
            f"Skill '{name}' loaded into session.", role="tool",
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
        self._add_message(f"Thinking level set to: {level}", role="tool")

    # ── Theme commands ────────────────────────────────────────────

    def _cmd_theme(self, name: str) -> None:
        """Switch or list themes."""
        from worker_tui.themes import load_themes

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
                f"Unknown theme '{name}'. Available: {available}", role="error",
            )
            return

        self._apply_theme(name)
        self._add_message(f"Theme switched to: {name}", role="tool")

    def _apply_theme(self, name: str) -> None:
        """Apply a theme's CSS to the app."""
        from worker_tui.themes import load_themes

        themes = load_themes(os.getcwd())
        css = themes.get(name)
        if css:
            self.stylesheet.add_source(css, read_from=("theme", name))
            self.stylesheet.reparse()
            self._active_theme = name
            self.refresh()

    # ── Export command ───────────────────────────────────────

    def _cmd_export(self, arg: str) -> None:
        """Export session to HTML."""
        from datetime import datetime

        from worker_ai.models import Role
        from worker_core.export import export_html

        if not self._session:
            self._add_message("No active session.", role="error")
            return

        messages = [
            m for m in self._session.messages
            if m.role in (Role.USER, Role.ASSISTANT)
        ]
        if not messages:
            self._add_message("Nothing to export.", role="error")
            return

        html = export_html(
            messages,
            title=f"Worker — {self._session.model}",
            model=self._session.model,
            session_id=self._session.session_id,
        )
        filename = arg or f"worker-export-{datetime.now().strftime('%Y%m%d-%H%M%S')}.html"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(html)
            self._add_message(f"Exported to {filename}", role="tool")
        except OSError as e:
            self._add_message(f"Export failed: {e}", role="error")

    # ── Reload & extensions ─────────────────────────────────

    async def _cmd_reload(self) -> None:
        """Hot-reload extensions, prompts, and skills."""
        from worker_core.extensions import reload_extensions_async

        if not self._session:
            self._add_message("No active session.", role="error")
            return
        config = load_config(os.getcwd())
        context = ExtensionContext(project_dir=os.getcwd(), runtime="tui", config=config)
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

    async def _ask_permission(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Show a modal dialog and return whether the tool call is allowed."""
        if self._auto_approve_all:
            return True
        # cmux: notify user that permission is needed
        await cmux.set_status(
            "state", f"permission: {tool_name}", icon="lock", color="#fab387",
        )
        await cmux.notify(
            "Worker", subtitle=f"Permission required: {tool_name}",
        )
        result = await self.push_screen_wait(PermissionScreen(tool_name, args))
        if result == "all":
            self._auto_approve_all = True
            return True
        return result == "once"

    # ── Helpers ──────────────────────────────────────────────

    def _add_message(self, content: str, role: str = "assistant") -> MessageWidget:
        container = self.query_one("#chat-container", Vertical)
        widget = MessageWidget(content, role=role)
        container.mount(widget)
        self._scroll_to_bottom()
        return widget

    def _add_tool_message(self, content: str) -> Collapsible:
        """Add a tool message wrapped in a collapsible container."""
        container = self.query_one("#chat-container", Vertical)
        widget = MessageWidget(content, role="tool")
        collapsible = Collapsible(widget, title="⚙ tool", collapsed=False)
        container.mount(collapsible)
        self._tool_collapsibles.append(collapsible)
        self._scroll_to_bottom()
        return collapsible

    def _scroll_to_bottom(self) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.scroll_end(animate=False)

    def _remote_connect_headers(self) -> dict[str, str]:
        if not self.auth_token:
            return {}
        return {"Authorization": f"Bearer {self.auth_token}"}

    def _remote_message_payload(self, text: str) -> dict[str, Any]:
        return {
            "type": "message",
            "content": text,
            "session_id": self._remote_session_id,
        }

    def action_toggle_tools(self) -> None:
        """Ctrl+O: toggle all tool output collapsibles."""
        for c in self._tool_collapsibles:
            c.collapsed = not c.collapsed

    async def action_quit(self) -> None:
        """Override default quit to clean up async resources."""
        await self._cleanup()
        self.exit()

    async def _cleanup(self) -> None:
        """Close all open async resources."""
        await self._close_store()
        if self._session and self._session.provider:
            with suppress(Exception):
                await self._session.provider.close()
        if self._ws:
            with suppress(Exception):
                await self._ws.close()
            self._ws = None

    async def action_clear(self) -> None:
        container = self.query_one("#chat-container", Vertical)
        container.remove_children()
        self._tool_collapsibles.clear()
        self._hide_command_menu()
        if self.remote_url:
            self._remote_session_id = str(uuid.uuid4())
            await self._sync_remote_session_state()
        if self._session:
            new_id = str(uuid.uuid4())
            if self._store:
                await self._store.create_session(
                    new_id, self._provider_model, project_dir=os.getcwd(),
                )
            self._session.session_id = new_id
            self._session.messages = self._session.messages[:1]
        self.call_after_refresh(self._focus_input)


def run_tui(
    remote_url: str = "",
    auth_token: str = "",
    forward_credentials: str = "",
    continue_session: bool = False,
    resume_id: str = "",
) -> None:
    """Entry point for the TUI."""
    app = WorkerApp(
        remote_url=remote_url,
        auth_token=auth_token,
        forward_credentials=forward_credentials,
        continue_session=continue_session,
        resume_id=resume_id,
    )
    app.run()
