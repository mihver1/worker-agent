"""Extension system — discovery, loading, hooks, commands."""

from __future__ import annotations

import importlib
import importlib.metadata
import inspect
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from artel_core.tools import Tool

# ── Types ─────────────────────────────────────────────────────────

CommandHandler = Callable[[str], Awaitable[str | None]]
"""Async function receiving arg string, returning optional response text."""


@dataclass(slots=True)
class ExtensionContext:
    """Runtime context shared with loaded extensions."""

    project_dir: str = ""
    runtime: str = "local"
    config: Any | None = None
    extras: dict[str, Any] = field(default_factory=dict)


# ── Base classes ──────────────────────────────────────────────────


class BaseExtension:
    """Shared lifecycle and context for Artel extension groups."""

    name: str = ""
    version: str = "0.0.0"
    context: ExtensionContext | None = None

    def bind_context(self, context: ExtensionContext) -> None:
        """Bind runtime context before the extension is loaded."""
        self.context = context

    async def on_load(self) -> None:
        """Called when the extension is loaded."""

    async def on_unload(self) -> None:
        """Called when the extension is unloaded."""


class Extension(BaseExtension):
    """Base class for Artel extensions.

    Subclass this and declare tools, hooks, and commands.
    """

    def get_tools(self) -> list[Tool]:
        """Return extra tools to register with the agent."""
        return []

    def get_commands(self) -> dict[str, CommandHandler]:
        """Return slash commands to register in the TUI.

        Keys are command names (without /), values are async handlers.
        Example: {"mycommand": self.handle_mycommand}
        """
        return {}


class TuiExtension(BaseExtension):
    """Base class for TUI extensions (Textual widgets, shortcuts, renderers)."""

    def get_widgets(self) -> list[Any]:
        """Return Textual widgets to mount in the TUI."""
        return []

    def get_keybindings(self) -> dict[str, Callable[..., Any]]:
        """Return keyboard shortcuts."""
        return {}

    async def mount(self, app: Any) -> None:
        """Mount widgets into the running app by default."""
        widgets = self.get_widgets()
        if not widgets:
            return
        main = app.query_one("#main-content")
        for widget in widgets:
            await main.mount(widget)


class ServerExtension(BaseExtension):
    """Base class for server/runtime extensions."""

    def configure_rest_app(self, app: Any) -> None:
        """Register extra REST routes or middleware."""
        return None


class AIExtension(BaseExtension):
    """Base class for AI/runtime provider extensions."""

    def register_providers(self, registry: Any) -> None:
        """Register additional providers with the shared provider registry."""
        return None


class WebExtension(BaseExtension):
    """Base class for future Web UI extensions."""


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

ARTEL_EXTENSION_GROUP = "artel.extensions"
ARTEL_TUI_EXTENSION_GROUP = "artel.tui"
ARTEL_SERVER_EXTENSION_GROUP = "artel.server"
ARTEL_AI_EXTENSION_GROUP = "artel.ai"
ARTEL_WEB_EXTENSION_GROUP = "artel.web"

COMPATIBLE_EXTENSION_GROUPS: dict[str, tuple[str, ...]] = {
    ARTEL_EXTENSION_GROUP: (ARTEL_EXTENSION_GROUP,),
    ARTEL_TUI_EXTENSION_GROUP: (ARTEL_TUI_EXTENSION_GROUP,),
    ARTEL_SERVER_EXTENSION_GROUP: (ARTEL_SERVER_EXTENSION_GROUP,),
    ARTEL_AI_EXTENSION_GROUP: (ARTEL_AI_EXTENSION_GROUP,),
    ARTEL_WEB_EXTENSION_GROUP: (ARTEL_WEB_EXTENSION_GROUP,),
}


def _entry_points_for_group(group: str) -> Any:
    try:
        return importlib.metadata.entry_points(group=group)
    except TypeError:
        # Python 3.11 compat
        return importlib.metadata.entry_points().get(group, [])


def discover_extensions(group: str = ARTEL_EXTENSION_GROUP) -> dict[str, type[Any]]:
    """Discover installed extensions via entry_points."""
    extensions: dict[str, type[Any]] = {}
    for entry_point_group in COMPATIBLE_EXTENSION_GROUPS.get(group, (group,)):
        for ep in _entry_points_for_group(entry_point_group):
            if ep.name in extensions:
                continue
            try:
                cls = ep.load()
                extensions[ep.name] = cls
            except Exception:
                continue
    return extensions


def _callable_kwargs(fn: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter kwargs to what the callable accepts unless it has **kwargs."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return kwargs

    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params):
        return kwargs

    supported_names = {
        param.name
        for param in params
        if param.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
    }
    return {name: value for name, value in kwargs.items() if name in supported_names}


def _instantiate_extensions(
    classes: dict[str, type[Any]],
    *,
    context: ExtensionContext | None = None,
) -> list[Any]:
    instances: list[Any] = []
    for cls in classes.values():
        try:
            ext = cls()
        except Exception:
            continue
        if context is not None and hasattr(ext, "bind_context"):
            with suppress(Exception):
                ext.bind_context(context)
        instances.append(ext)
    return instances


async def _activate_extensions(instances: list[Any]) -> list[Any]:
    active: list[Any] = []
    for ext in instances:
        try:
            await ext.on_load()
        except Exception:
            with suppress(Exception):
                await ext.on_unload()
            continue
        active.append(ext)
    return active


async def _load_extension_group_async(
    *,
    group: str,
    context: ExtensionContext | None = None,
) -> list[Any]:
    instances = _instantiate_extensions(discover_extensions(group=group), context=context)
    return await _activate_extensions(instances)


async def _reload_extension_group_async(
    *,
    group: str,
    current_instances: list[Any] | None = None,
    context: ExtensionContext | None = None,
) -> list[Any]:
    for ext in current_instances or []:
        with suppress(Exception):
            await ext.on_unload()
    importlib.invalidate_caches()
    return await _load_extension_group_async(group=group, context=context)


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
        with suppress(Exception):
            self._commands.update(ext.get_commands())

    @property
    def commands(self) -> dict[str, CommandHandler]:
        """Slash commands registered by extensions."""
        return self._commands

    async def fire(self, event: str, **kwargs: Any) -> None:
        """Fire all hooks for the given event."""
        for fn in self._hooks.get(event, []):
            result = fn(**_callable_kwargs(fn, kwargs))
            if inspect.isawaitable(result):
                await result

    async def fire_filter(self, event: str, value: Any, **kwargs: Any) -> Any:
        """Fire hooks that can modify a value (pipeline pattern).

        Each hook receives `value=...` plus kwargs. If a hook returns
        a non-None result, the value is replaced for subsequent hooks.
        """
        for fn in self._hooks.get(event, []):
            call_kwargs = _callable_kwargs(fn, {"value": value, **kwargs})
            result = fn(**call_kwargs)
            if inspect.isawaitable(result):
                result = await result
            if result is not None:
                value = result
        return value


def load_extensions(
    context: ExtensionContext | None = None,
) -> tuple[list[Extension], HookDispatcher]:
    """Discover, instantiate, and return extensions + hook dispatcher."""
    instances = _instantiate_extensions(discover_extensions(), context=context)
    return instances, HookDispatcher(instances)


async def load_extensions_async(
    context: ExtensionContext | None = None,
) -> tuple[list[Extension], HookDispatcher]:
    """Discover, instantiate, activate, and return extensions + hook dispatcher."""
    instances = await _load_extension_group_async(group=ARTEL_EXTENSION_GROUP, context=context)
    return instances, HookDispatcher(instances)


async def reload_extensions_async(
    current_instances: list[Extension] | None = None,
    *,
    context: ExtensionContext | None = None,
) -> tuple[list[Extension], HookDispatcher]:
    """Hot-reload core extensions, invalidate caches, and re-discover."""
    instances = await _reload_extension_group_async(
        group=ARTEL_EXTENSION_GROUP, current_instances=current_instances, context=context
    )
    return instances, HookDispatcher(instances)


def discover_tui_extensions() -> dict[str, type[TuiExtension]]:
    """Discover installed TUI extensions."""
    return discover_extensions(group=ARTEL_TUI_EXTENSION_GROUP)  # type: ignore[return-value]


def discover_server_extensions() -> dict[str, type[ServerExtension]]:
    """Discover installed server extensions."""
    return discover_extensions(group=ARTEL_SERVER_EXTENSION_GROUP)  # type: ignore[return-value]


def discover_ai_extensions() -> dict[str, type[AIExtension]]:
    """Discover installed AI extensions."""
    return discover_extensions(group=ARTEL_AI_EXTENSION_GROUP)  # type: ignore[return-value]


def discover_web_extensions() -> dict[str, type[WebExtension]]:
    """Discover installed Web UI extensions."""
    return discover_extensions(group=ARTEL_WEB_EXTENSION_GROUP)  # type: ignore[return-value]


async def load_tui_extensions_async(
    context: ExtensionContext | None = None,
) -> list[TuiExtension]:
    """Load and activate TUI extensions."""
    return await _load_extension_group_async(  # type: ignore[return-value]
        group=ARTEL_TUI_EXTENSION_GROUP, context=context
    )


async def reload_tui_extensions_async(
    current_instances: list[TuiExtension] | None = None,
    *,
    context: ExtensionContext | None = None,
) -> list[TuiExtension]:
    """Hot-reload TUI extensions."""
    return await _reload_extension_group_async(  # type: ignore[return-value]
        group=ARTEL_TUI_EXTENSION_GROUP, current_instances=current_instances, context=context
    )


async def load_server_extensions_async(
    context: ExtensionContext | None = None,
) -> list[ServerExtension]:
    """Load and activate server extensions."""
    return await _load_extension_group_async(  # type: ignore[return-value]
        group=ARTEL_SERVER_EXTENSION_GROUP, context=context
    )


async def load_ai_extensions_async(
    context: ExtensionContext | None = None,
) -> list[AIExtension]:
    """Load and activate AI extensions."""
    return await _load_extension_group_async(  # type: ignore[return-value]
        group=ARTEL_AI_EXTENSION_GROUP, context=context
    )


async def load_web_extensions_async(
    context: ExtensionContext | None = None,
) -> list[WebExtension]:
    """Load and activate Web extensions."""
    return await _load_extension_group_async(  # type: ignore[return-value]
        group=ARTEL_WEB_EXTENSION_GROUP, context=context
    )
