"""Built-in Artel capability registry.

These capabilities are bundled with the product and should load without
external extension installation while preserving a conceptual extension-like
registration boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from artel_core.mcp import MCPRegistry


@dataclass(slots=True)
class BuiltinCapability:
    name: str
    kind: str
    bundled: bool = True
    removable: bool = False
    instance: Any | None = None


def load_builtin_capabilities(*, project_dir: str = "") -> dict[str, BuiltinCapability]:
    del project_dir
    from artel_core.lsp_runtime import LspRuntimeManager

    mcp = BuiltinCapability(
        name="artel-mcp",
        kind="mcp",
        instance=MCPRegistry(),
    )
    lsp = BuiltinCapability(
        name="artel-lsp",
        kind="lsp",
        instance=LspRuntimeManager(),
    )
    return {
        mcp.name: mcp,
        lsp.name: lsp,
    }


def builtin_capability_names() -> list[str]:
    return list(load_builtin_capabilities().keys())


__all__ = ["BuiltinCapability", "builtin_capability_names", "load_builtin_capabilities"]
