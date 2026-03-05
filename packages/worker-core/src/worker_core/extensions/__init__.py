"""Extension system — discovery, loading, hooks."""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from typing import Any


class Extension:
    """Base class for worker extensions.

    Subclass this and declare tools/hooks via decorators.
    """

    name: str = ""
    version: str = "0.0.0"

    async def on_load(self) -> None:
        """Called when the extension is loaded."""

    async def on_unload(self) -> None:
        """Called when the extension is unloaded."""


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

    Supported events: before_turn, after_turn, on_tool_call,
    on_session_start, on_session_end, on_compaction.
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
        for ext in extensions or []:
            self._register(ext)

    def _register(self, ext: Extension) -> None:
        for attr_name in dir(ext):
            method = getattr(ext, attr_name, None)
            if callable(method) and hasattr(method, "_hook_event"):
                event = method._hook_event
                self._hooks.setdefault(event, []).append(method)

    async def fire(self, event: str, **kwargs: Any) -> None:
        """Fire all hooks for the given event."""
        import inspect

        for fn in self._hooks.get(event, []):
            result = fn(**kwargs)
            if inspect.isawaitable(result):
                await result


def load_extensions() -> tuple[list[Extension], HookDispatcher]:
    """Discover, instantiate, and return extensions + hook dispatcher."""
    classes = discover_extensions()
    instances = []
    for name, cls in classes.items():
        try:
            instances.append(cls())
        except Exception:
            continue
    return instances, HookDispatcher(instances)


def discover_tui_extensions() -> dict[str, type[TuiExtension]]:
    """Discover installed TUI extensions."""
    return discover_extensions(group="worker.tui")  # type: ignore[return-value]


def discover_web_extensions() -> dict[str, type[WebExtension]]:
    """Discover installed Web UI extensions."""
    return discover_extensions(group="worker.web")  # type: ignore[return-value]
