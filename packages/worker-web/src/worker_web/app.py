"""Minimal Artel web entrypoint placeholder."""

from __future__ import annotations

from typing import Any


def run_web(**kwargs: Any) -> None:
    """Placeholder web entrypoint.

    The full NiceGUI surface is not yet present in this checkout, but the CLI and
    tests expect the module import surface to exist.
    """
    raise RuntimeError(
        "The Artel web UI source is not available in this checkout yet. "
        "The current repository only ships the shared rendering helpers and "
        "compatibility import surface."
    )
