"""Extension system — discovery, loading, hooks, commands."""

from __future__ import annotations

import importlib
import importlib.metadata
import inspect
from collections.abc import Awaitable, Callable
from typing import Any

from worker_core.tools import Tool

# ── Types ─────────────────────────────────────────────────────────

CommandHandler = Callable[[str], Awaitable[str | None]]
"""Async function receiving arg string, returning optional response text."""


# ── Base classes ──────────────────────────────────────────────────


class Extension:
    """Base class for worker extensions.

    Subclass this and declare tools, hooks, and commands.
    """

    name: str = ""
    version: str = "0.0.0"

    async def on_load(self) -> None:
        """Called when the extension is loaded."""

    async def on_unload(self) -> None:
        """Called when the extension is unloaded."""

    def get_tools(self) -> list[Tool]:
        """Return extra tools to register with the agent."""
        return []

    def get_commands(self) -> dict[str, CommandHandler]:
        """Return slash commands to register in the TUI.

        Keys are command names (without /), values are async handlers.
        Example: {"mycommand": self.handle_mycommand}
        """
        return {}


class TuiExtension:
    """Base class for TUI extensions (Textual widgets, shortcuts, renderers)."""

    name: str = ""
    version: str = "0.0.0"

    def get_widgets(self) -> list[Any]:
        """Return Textual widgets to mount in the TUI."""
        return []

    def get_keybindings(self) -> dict[str, Callable[..., Any]]:
        """Return keyboard shortcuts."""
        return {}


class WebExtension:
    """Base class for future Web UI extensions."""

    name: str = ""
    version: str = "0.0.0"


def hook(event: str) -> Callable[..., Any]:
    """Decorator to mark a method as a lifecycle hook.

    Supported events:
        before_turn, after_turn, on_tool_call, before_tool_call,
        on_session_start, on_session_end, on_compaction,
        on_message, on_error.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn._hook_event = event  # type: ignore[attr-defined]
        return fn

    return decorator


# ── Discovery ─────────────────────────────────────────────────────


def discover_extensions(group: str = "worker.extensions") -> dict[str, type[Extension]]:
    """Discover installed extensions via entry_points."""
    extensions: dict[str, type[Extension]] = {}
    try:
        eps = importlib.metadata.entry_points(group=group)
    except TypeError:
        # Python 3.11 compat
        eps = importlib.metadata.entry_points().get(group, [])

    for ep in eps:
        try:
            cls = ep.load()
            extensions[ep.name] = cls
        except Exception:
            continue
    return extensions


class HookDispatcher:
    """Collects and dispatches hooks from loaded extension instances."""

    def __init__(self, extensions: list[Extension] | None = None):
        self._hooks: dict[str, list[Callable[..., Any]]] = {}
        self._commands: dict[str, CommandHandler] = {}
        for ext in extensions or []:
            self._register(ext)

    def _register(self, ext: Extension) -> None:
        for attr_name in dir(ext):
            method = getattr(ext, attr_name, None)
            if callable(method) and hasattr(method, "_hook_event"):
                event = method._hook_event
                self._hooks.setdefault(event, []).append(method)
        try:
            self._commands.update(ext.get_commands())
        except Exception:
            pass

    @property
    def commands(self) -> dict[str, CommandHandler]:
        """Slash commands registered by extensions."""
        return self._commands

    async def fire(self, event: str, **kwargs: Any) -> None:
        """Fire all hooks for the given event."""
        for fn in self._hooks.get(event, []):
            result = fn(**kwargs)
            if inspect.isawaitable(result):
                await result

    async def fire_filter(self, event: str, value: Any, **kwargs: Any) -> Any:
        """Fire hooks that can modify a value (pipeline pattern).

        Each hook receives `value=...` plus kwargs.  If a hook returns
        a non-None result, the value is replaced for subsequent hooks.
        """
        for fn in self._hooks.get(event, []):
            result = fn(value=value, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                value = result
        return value


def load_extensions() -> tuple[list[Extension], HookDispatcher]:
    """Discover, instantiate, and return extensions + hook dispatcher."""
    classes = discover_extensions()
    instances: list[Extension] = []
    for name, cls in classes.items():
        try:
            instances.append(cls())
        except Exception:
            continue
    return instances, HookDispatcher(instances)


async def reload_extensions_async(
    current_instances: list[Extension] | None = None,
) -> tuple[list[Extension], HookDispatcher]:
    """Hot-reload: unload current extensions, invalidate caches, re-discover."""
    for ext in current_instances or []:
        try:
            await ext.on_unload()
        except Exception:
            pass
    importlib.invalidate_caches()
    return load_extensions()


def discover_tui_extensions() -> dict[str, type[TuiExtension]]:
    """Discover installed TUI extensions."""
    return discover_extensions(group="worker.tui")  # type: ignore[return-value]


def discover_web_extensions() -> dict[str, type[WebExtension]]:
    """Discover installed Web UI extensions."""
    return discover_extensions(group="worker.web")  # type: ignore[return-value]
