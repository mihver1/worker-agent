"""Worker TUI — Textual application."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import Any

from rich.markdown import Markdown
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Static

from worker_ai.models import Role
from worker_core.agent import AgentEventType, AgentSession
from worker_core.config import load_config, resolve_model
from worker_core.sessions import SessionStore
from worker_core.tools.builtins import create_builtin_tools


# ── Widgets ───────────────────────────────────────────────────────


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
        self.refresh()


# ── Main App ──────────────────────────────────────────────────────


class WorkerApp(App):
    """Textual TUI for the Worker coding agent."""

    TITLE = "Worker"

    CSS = """
    #chat-scroll {
        height: 1fr;
    }
    #chat-container {
        height: auto;
    }
    #input-bar {
        dock: bottom;
        height: auto;
        max-height: 6;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "quit", "Quit"),
        Binding("ctrl+l", "clear", "Clear"),
    ]

    def __init__(
        self,
        *,
        remote_url: str = "",
        auth_token: str = "",
        continue_session: bool = False,
        resume_id: str = "",
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.remote_url = remote_url
        self.auth_token = auth_token
        self._continue_session = continue_session
        self._resume_id = resume_id
        self._session: AgentSession | None = None
        self._store: SessionStore | None = None
        self._extensions: list[Any] = []
        self._current_widget: MessageWidget | None = None
        self._ws: Any = None  # websocket connection for remote mode

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="chat-scroll"):
            yield Vertical(id="chat-container")
        yield Input(placeholder="Type a message... (Enter to send)", id="input-bar")
        yield Footer()

    async def on_mount(self) -> None:
        if self.remote_url:
            self.sub_title = f"remote: {self.remote_url}"
        else:
            await self._init_local_session()

    async def _init_local_session(self) -> None:
        from worker_ai.providers import create_default_registry
        from worker_core.cli import _resolve_api_key

        config = load_config(os.getcwd())
        provider_name, model_id = resolve_model(config)
        registry = create_default_registry()
        api_key, auth_type = _resolve_api_key(config, provider_name)

        prov_cfg = config.providers.get(provider_name)
        kwargs: dict[str, Any] = {}
        if prov_cfg and prov_cfg.base_url:
            kwargs["base_url"] = prov_cfg.base_url
        if auth_type == "oauth":
            kwargs["auth_type"] = "oauth"

        provider = registry.create(provider_name, api_key=api_key, **kwargs)
        tools = create_builtin_tools(os.getcwd())

        # Load extensions
        from worker_core.extensions import load_extensions

        self._extensions, hooks = load_extensions()
        for ext in self._extensions:
            tools.extend(ext.get_tools())

        # Session store
        self._store = SessionStore(config.sessions.db_path)
        await self._store.open()

        # Resolve session (resume or new)
        session_id = ""
        prior_messages = None

        if self._resume_id:
            info = await self._store.get_session(self._resume_id)
            if info:
                session_id = info.id
                prior_messages = await self._store.get_messages(session_id)
        elif self._continue_session:
            last = await self._store.get_last_session()
            if last:
                session_id = last.id
                prior_messages = await self._store.get_messages(session_id)

        if not session_id:
            session_id = str(uuid.uuid4())
            await self._store.create_session(session_id, model_id)

        self._session = AgentSession(
            provider=provider,
            model=model_id,
            tools=tools,
            system_prompt=config.agent.system_prompt,
            temperature=config.agent.temperature,
            max_turns=config.agent.max_turns,
            store=self._store,
            session_id=session_id,
            auto_compact=config.sessions.auto_compact,
            compact_threshold=config.sessions.compact_threshold,
            hooks=hooks,
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

        self.sub_title = f"{provider_name}/{model_id}"

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return

        event.input.value = ""

        # Handle bash commands with "!" prefix
        if text.startswith("!"):
            cmd = text[1:].strip()
            if cmd:
                self._add_message(f"$ {cmd}", role="user")
                self._run_bash(cmd)
            return

        # Handle slash commands
        if text.startswith("/"):
            await self._handle_command(text)
            return

        self._add_message(text, role="user")

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
            await self._close_store()
            self.exit()
        elif cmd == "/help":
            self._add_message(
                "Commands:\n"
                "  /model              \u2014 show current model\n"
                "  /model <p/model>    \u2014 switch model\n"
                "  /models             \u2014 list all available models\n"
                "  /connect <provider> \u2014 login to a provider\n"
                "  /resume             \u2014 list and resume a session\n"
                "  /sessions           \u2014 list recent sessions\n"
                "  /compact [prompt]   \u2014 compact conversation history\n"
                "  /name <title>       \u2014 rename current session\n"
                "  /tree               \u2014 show session message tree\n"
                "  /fork [index]       \u2014 fork session from message index\n"
                "  /reload             \u2014 hot-reload extensions\n"
                "  /clear              \u2014 clear chat & start new session\n"
                "  /quit               \u2014 exit\n"
                "  ! <command>         \u2014 run a shell command",
                role="tool",
            )
        elif cmd == "/model":
            if arg:
                await self._switch_model(arg)
            else:
                model = self._session.model if self._session else "remote"
                self._add_message(f"Current model: {model}", role="tool")
        elif cmd == "/models":
            self._list_models()
        elif cmd == "/connect":
            if not arg:
                self._add_message("Usage: /connect <provider>  (anthropic, openai, kimi)", role="tool")
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
        elif cmd == "/reload":
            await self._cmd_reload()
        else:
            # Check extension commands
            cmd_name = cmd.lstrip("/")
            if self._session and cmd_name in self._session.hooks.commands:
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

        widget = self._add_message("", role="assistant")

        async for event in self._session.run(text):
            if event.type == AgentEventType.TEXT_DELTA:
                widget.append_content(event.content)
            elif event.type == AgentEventType.TOOL_CALL:
                self._add_message(f"⚙ {event.tool_name}({', '.join(f'{k}={v!r}' for k, v in event.tool_args.items())})", role="tool")
            elif event.type == AgentEventType.TOOL_RESULT:
                output = event.content[:200] + "..." if len(event.content) > 200 else event.content
                self._add_message(f"  → {output}", role="tool")
            elif event.type == AgentEventType.ERROR:
                self._add_message(event.error, role="error")
            elif event.type == AgentEventType.COMPACT:
                self._add_message("\U0001f4cb Session auto-compacted.", role="tool")
            elif event.type == AgentEventType.DONE:
                if event.usage:
                    self._add_message(
                        f"tokens: {event.usage.input_tokens} in / {event.usage.output_tokens} out",
                        role="tool",
                    )

        self._scroll_to_bottom()

    @work(exclusive=True, thread=False)
    async def _run_remote(self, text: str) -> None:
        """Send a query to the remote server via WebSocket."""
        import websockets

        if not self._ws:
            try:
                self._ws = await websockets.connect(self.remote_url)
            except Exception as e:
                self._add_message(f"Connection failed: {e}", role="error")
                return

        await self._ws.send(json.dumps({"type": "message", "content": text}))

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
        """Show available models from models.dev catalog for configured providers."""
        from worker_ai.models_catalog import ModelsCatalog
        from worker_core.cli import _resolve_api_key

        config = load_config(os.getcwd())

        # Key providers we care about
        key_providers = ["anthropic", "openai", "google", "kimi", "groq", "openrouter", "deepseek", "mistral", "xai"]

        catalog = await ModelsCatalog.load()
        lines: list[str] = []
        for pid in key_providers:
            prov = catalog.get(pid)
            if not prov or not prov.models:
                continue
            # Check if we have credentials
            api_key, _ = _resolve_api_key(config, pid)
            marker = "\u2713" if api_key else " "
            lines.append(f"\n  [{marker}] {prov.name}:")
            for m in prov.models[:8]:  # Show top 8 models per provider
                ctx = f"{m.context_window // 1000}k" if m.context_window else "?"
                lines.append(f"      {pid}/{m.id}  ({m.name}, {ctx} ctx)")
            if len(prov.models) > 8:
                lines.append(f"      ... and {len(prov.models) - 8} more")

        if lines:
            self._add_message(
                "Models (\u2713 = credentials available):\n" + "\n".join(lines),
                role="tool",
            )
        else:
            self._add_message("Failed to load model catalog.", role="error")

    async def _switch_model(self, model_str: str) -> None:
        """Switch to a different model (provider/model-id format)."""
        if "/" not in model_str:
            self._add_message(
                "Format: provider/model-id (e.g. anthropic/claude-sonnet-4-20250514)",
                role="error",
            )
            return

        from worker_ai.models_catalog import ModelsCatalog
        from worker_ai.providers import create_default_registry
        from worker_core.cli import _resolve_api_key

        provider_name, model_id = model_str.split("/", 1)
        config = load_config(os.getcwd())
        registry = create_default_registry()

        if provider_name not in registry.available:
            self._add_message(f"Unknown provider: {provider_name}", role="error")
            return

        # Validate model exists in catalog
        catalog_model = await ModelsCatalog.get_model(provider_name, model_id)
        if not catalog_model:
            self._add_message(
                f"Model '{model_id}' not found for {provider_name}. Use /models to see available.",
                role="error",
            )
            return

        api_key, auth_type = _resolve_api_key(config, provider_name)
        if not api_key:
            self._add_message(
                f"No credentials for {provider_name}. Run /connect {provider_name}",
                role="error",
            )
            return

        prov_cfg = config.providers.get(provider_name)
        kwargs: dict[str, Any] = {}
        if prov_cfg and prov_cfg.base_url:
            kwargs["base_url"] = prov_cfg.base_url
        if auth_type == "oauth":
            kwargs["auth_type"] = "oauth"

        try:
            provider = registry.create(provider_name, api_key=api_key, **kwargs)
        except Exception as e:
            self._add_message(f"Failed to create provider: {e}", role="error")
            return

        # Close old provider
        if self._session:
            await self._session.provider.close()

        tools = create_builtin_tools(os.getcwd())
        session_id = str(uuid.uuid4())
        if self._store:
            await self._store.create_session(session_id, model_id)
        self._session = AgentSession(
            provider=provider,
            model=model_id,
            tools=tools,
            system_prompt=config.agent.system_prompt,
            temperature=config.agent.temperature,
            max_turns=config.agent.max_turns,
            store=self._store,
            session_id=session_id,
            auto_compact=config.sessions.auto_compact,
            compact_threshold=config.sessions.compact_threshold,
        )
        self.sub_title = f"{provider_name}/{model_id}"
        self._add_message(f"Switched to {provider_name}/{model_id}", role="tool")

    # ── Provider login ────────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def _run_connect(self, provider_name: str) -> None:
        """Run OAuth login for a provider."""
        from worker_ai.oauth import get_oauth_provider

        oauth = get_oauth_provider(provider_name)
        if oauth is None:
            self._add_message(
                f"OAuth not supported for '{provider_name}'. Supported: kimi, anthropic, openai",
                role="error",
            )
            return

        self._add_message(f"Starting {provider_name} login... Check your browser/terminal.", role="tool")
        try:
            await oauth.login()
            self._add_message(f"{provider_name.capitalize()} authorized! Use /model to switch.", role="tool")
        except Exception as e:
            self._add_message(f"Login failed: {e}", role="error")

    # ── Session commands ───────────────────────────────────────

    async def _cmd_resume(self, arg: str) -> None:
        """List sessions or resume by number/ID."""
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

        lines = ["Recent sessions:"]
        for i, s in enumerate(sessions, 1):
            title = s.title or "(untitled)"
            lines.append(f"  {i}. [{s.updated_at}] {title} ({s.model})")
        lines.append("\nType /resume <number> to load a session.")
        self._add_message("\n".join(lines), role="tool")

    async def _resume_session(self, session_id: str) -> None:
        """Load a session and replace current chat."""
        if not self._store or not self._session:
            return

        messages = await self._store.get_messages(session_id)
        info = await self._store.get_session(session_id)

        # Clear current chat
        container = self.query_one("#chat-container", Vertical)
        container.remove_children()

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

    async def _cmd_reload(self) -> None:
        """Hot-reload extensions."""
        from worker_core.extensions import reload_extensions_async

        if not self._session:
            self._add_message("No active session.", role="error")
            return

        self._extensions, new_hooks = await reload_extensions_async(self._extensions)
        self._session.hooks = new_hooks

        # Re-collect tools
        tools = create_builtin_tools(os.getcwd())
        for ext in self._extensions:
            tools.extend(ext.get_tools())
        self._session.tools = {t.name: t for t in tools}

        ext_names = [e.name for e in self._extensions if e.name]
        self._add_message(
            f"Reloaded {len(self._extensions)} extension(s): {', '.join(ext_names) or 'none'}",
            role="tool",
        )

    async def _close_store(self) -> None:
        """Close the session store."""
        if self._store:
            await self._store.close()
            self._store = None

    # ── Bash execution ────────────────────────────────────────

    @work(exclusive=True, thread=False)
    async def _run_bash(self, cmd: str) -> None:
        """Execute a shell command and display the output."""
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
        except Exception as e:
            self._add_message(f"Command failed: {e}", role="error")
        self._scroll_to_bottom()

    # ── Helpers ────────────────────────────────────────────────────

    def _add_message(self, content: str, role: str = "assistant") -> MessageWidget:
        container = self.query_one("#chat-container", Vertical)
        widget = MessageWidget(content, role=role)
        container.mount(widget)
        self._scroll_to_bottom()
        return widget

    def _scroll_to_bottom(self) -> None:
        scroll = self.query_one("#chat-scroll", VerticalScroll)
        scroll.scroll_end(animate=False)

    async def action_clear(self) -> None:
        container = self.query_one("#chat-container", Vertical)
        container.remove_children()
        if self._session:
            new_id = str(uuid.uuid4())
            if self._store:
                await self._store.create_session(new_id, self._session.model)
            self._session.session_id = new_id
            self._session.messages = self._session.messages[:1]


def run_tui(
    remote_url: str = "",
    auth_token: str = "",
    continue_session: bool = False,
    resume_id: str = "",
) -> None:
    """Entry point for the TUI."""
    app = WorkerApp(
        remote_url=remote_url,
        auth_token=auth_token,
        continue_session=continue_session,
        resume_id=resume_id,
    )
    app.run()
