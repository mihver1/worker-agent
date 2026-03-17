"""LSP-backed semantic code-intelligence tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from artel_ai.models import ToolDef

from artel_core.execution import get_current_tool_execution_context
from artel_core.lsp_runtime import LspRuntimeManager, _path_from_uri
from artel_core.tools import Tool

_SYMBOL_KINDS = {
    1: "File",
    2: "Module",
    3: "Namespace",
    4: "Package",
    5: "Class",
    6: "Method",
    7: "Property",
    8: "Field",
    9: "Constructor",
    10: "Enum",
    11: "Interface",
    12: "Function",
    13: "Variable",
    14: "Constant",
    15: "String",
    16: "Number",
    17: "Boolean",
    18: "Array",
    19: "Object",
    20: "Key",
    21: "Null",
    22: "EnumMember",
    23: "Struct",
    24: "Event",
    25: "Operator",
    26: "TypeParameter",
}


def _coerce_positive_int(value: Any, *, default: int = 1) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def _range_label(range_data: dict[str, Any]) -> str:
    start = range_data.get("start") or {}
    line = int(start.get("line", 0) or 0) + 1
    column = int(start.get("character", 0) or 0) + 1
    return f"{line}:{column}"


def _preview_line(path: str, line: int) -> str:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    index = line - 1
    if index < 0 or index >= len(lines):
        return ""
    return lines[index].strip()


def _format_hover(value: Any, *, path: str, line: int, column: int) -> str:
    if not value:
        return f"No hover information for {path}:{line}:{column}."

    contents = value.get("contents") if isinstance(value, dict) else value
    blocks: list[str] = []
    if isinstance(contents, str):
        blocks.append(contents.strip())
    elif isinstance(contents, dict):
        text = contents.get("value") or contents.get("language")
        if text:
            blocks.append(str(text).strip())
    elif isinstance(contents, list):
        for item in contents:
            if isinstance(item, str) and item.strip():
                blocks.append(item.strip())
            elif isinstance(item, dict):
                text = item.get("value") or item.get("language")
                if text:
                    blocks.append(str(text).strip())

    rendered = "\n\n".join(block for block in blocks if block)
    if not rendered:
        return f"No hover information for {path}:{line}:{column}."
    return f"Hover for {path}:{line}:{column}\n\n{rendered}"


def _format_locations(
    title: str,
    locations: list[dict[str, Any]],
    *,
    max_results: int,
) -> str:
    if not locations:
        return f"No {title.lower()} found."

    lines = [f"{title} ({len(locations)} result(s)):"]
    for item in locations[:max_results]:
        uri = str(item.get("uri", "") or "")
        range_data = item.get("range") if isinstance(item.get("range"), dict) else {}
        file_path = _path_from_uri(uri)
        label = _range_label(range_data)
        preview = _preview_line(file_path, int(range_data.get("start", {}).get("line", 0) or 0) + 1)
        rendered = f"- {file_path}:{label}"
        if preview:
            rendered += f" — {preview}"
        lines.append(rendered)
    if len(locations) > max_results:
        lines.append(f"... ({len(locations)} total, showing {max_results})")
    return "\n".join(lines)


def _flatten_document_symbols(
    symbols: list[dict[str, Any]],
    *,
    depth: int = 0,
) -> list[str]:
    lines: list[str] = []
    for item in symbols:
        name = str(item.get("name", "") or "").strip() or "(anonymous)"
        kind = _SYMBOL_KINDS.get(int(item.get("kind", 0) or 0), "Symbol")
        range_data = item.get("selectionRange") or item.get("range") or {}
        indent = "  " * depth
        lines.append(f"{indent}- {kind} {name} [{_range_label(range_data)}]")
        children = item.get("children")
        if isinstance(children, list):
            lines.extend(_flatten_document_symbols(children, depth=depth + 1))
    return lines


def _format_document_symbols(path: str, symbols: list[dict[str, Any]]) -> str:
    if not symbols:
        return f"No document symbols for {path}."
    lines = [f"Document symbols for {path}:"]
    lines.extend(_flatten_document_symbols(symbols))
    return "\n".join(lines)


def _format_workspace_symbols(symbols: list[dict[str, Any]], *, max_results: int) -> str:
    if not symbols:
        return "No workspace symbols found."

    lines = [f"Workspace symbols ({len(symbols)} result(s)):"]
    for item in symbols[:max_results]:
        name = str(item.get("name", "") or "").strip() or "(anonymous)"
        kind = _SYMBOL_KINDS.get(int(item.get("kind", 0) or 0), "Symbol")
        location = item.get("location") if isinstance(item.get("location"), dict) else {}
        uri = str(location.get("uri", "") or "")
        range_data = location.get("range") if isinstance(location.get("range"), dict) else {}
        file_path = _path_from_uri(uri)
        lines.append(f"- {kind} {name} — {file_path}:{_range_label(range_data)}")
    if len(symbols) > max_results:
        lines.append(f"... ({len(symbols)} total, showing {max_results})")
    return "\n".join(lines)


def _format_diagnostics(path: str, diagnostics: list[dict[str, Any]]) -> str:
    if not diagnostics:
        return f"No diagnostics for {path}."

    severity_map = {1: "error", 2: "warning", 3: "info", 4: "hint"}
    lines = [f"Diagnostics for {path} ({len(diagnostics)} item(s)):"]
    for item in diagnostics:
        message = str(item.get("message", "") or "").strip() or "(no message)"
        severity = severity_map.get(int(item.get("severity", 0) or 0), "unknown")
        range_data = item.get("range") if isinstance(item.get("range"), dict) else {}
        lines.append(f"- {severity} [{_range_label(range_data)}] {message}")
    return "\n".join(lines)


class _BaseLspTool(Tool):
    name = ""
    description = ""

    def __init__(self, working_dir: str, runtime: LspRuntimeManager) -> None:
        self.working_dir = working_dir
        self.runtime = runtime

    def _resolve_path(self, raw_path: str) -> str:
        path = Path(raw_path)
        if path.is_absolute():
            return str(path.resolve())
        return str((Path(self.working_dir) / path).resolve())

    def _set_display_path(self, path: str) -> None:
        ctx = get_current_tool_execution_context()
        if ctx is not None:
            ctx.display_payload = {"path": path}


class LspHoverTool(_BaseLspTool):
    name = "lsp_hover"
    description = (
        "Get semantic hover information at a source position using a live Language Server Protocol "
        "server. Use this for types, docstrings, and symbol context when plain text search is not "
        "enough. Line and column are 1-based."
    )

    async def execute(self, **kwargs: Any) -> str:
        path = self._resolve_path(str(kwargs.get("path", "") or ""))
        line = _coerce_positive_int(kwargs.get("line"))
        column = _coerce_positive_int(kwargs.get("column", 1))
        self._set_display_path(path)
        try:
            result = await self.runtime.hover(path, line=line, column=column)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        return _format_hover(result, path=path, line=line, column=column)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[],
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path, relative to the project root or absolute.",
                    },
                    "line": {
                        "type": "integer",
                        "description": "1-based line number.",
                        "minimum": 1,
                    },
                    "column": {
                        "type": "integer",
                        "description": "1-based column number. Defaults to 1.",
                        "minimum": 1,
                        "default": 1,
                    },
                },
                "required": ["path", "line"],
            },
        )


class LspDefinitionTool(_BaseLspTool):
    name = "lsp_definition"
    description = (
        "Find semantic definitions for the symbol at a source position using LSP. Returns resolved "
        "file locations with code previews, which is usually more precise than grep. Line and "
        "column are 1-based."
    )

    async def execute(self, **kwargs: Any) -> str:
        path = self._resolve_path(str(kwargs.get("path", "") or ""))
        line = _coerce_positive_int(kwargs.get("line"))
        column = _coerce_positive_int(kwargs.get("column", 1))
        max_results = _coerce_positive_int(kwargs.get("max_results", 10), default=10)
        self._set_display_path(path)
        try:
            result = await self.runtime.definition(path, line=line, column=column)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        return _format_locations(
            f"Definitions for {path}:{line}:{column}",
            result,
            max_results=max_results,
        )

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[],
            input_schema=_position_schema(max_results_default=10),
        )


class LspReferencesTool(_BaseLspTool):
    name = "lsp_references"
    description = (
        "Find semantic references for the symbol at a source position using LSP. Returns semantic "
        "cross-file locations with line previews so the agent can inspect real usages quickly. "
        "Line and column are 1-based."
    )

    async def execute(self, **kwargs: Any) -> str:
        path = self._resolve_path(str(kwargs.get("path", "") or ""))
        line = _coerce_positive_int(kwargs.get("line"))
        column = _coerce_positive_int(kwargs.get("column", 1))
        max_results = _coerce_positive_int(kwargs.get("max_results", 20), default=20)
        self._set_display_path(path)
        try:
            result = await self.runtime.references(path, line=line, column=column)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        return _format_locations(
            f"References for {path}:{line}:{column}",
            result,
            max_results=max_results,
        )

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[],
            input_schema=_position_schema(max_results_default=20),
        )


class LspImplementationTool(_BaseLspTool):
    name = "lsp_implementation"
    description = (
        "Find semantic implementations for the symbol at a source position using LSP. Useful for "
        "interfaces, abstract methods, and protocol-style navigation. Line and column are 1-based."
    )

    async def execute(self, **kwargs: Any) -> str:
        path = self._resolve_path(str(kwargs.get("path", "") or ""))
        line = _coerce_positive_int(kwargs.get("line"))
        column = _coerce_positive_int(kwargs.get("column", 1))
        max_results = _coerce_positive_int(kwargs.get("max_results", 20), default=20)
        self._set_display_path(path)
        try:
            result = await self.runtime.implementation(path, line=line, column=column)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        return _format_locations(
            f"Implementations for {path}:{line}:{column}",
            result,
            max_results=max_results,
        )

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[],
            input_schema=_position_schema(max_results_default=20),
        )


class LspDocumentSymbolsTool(_BaseLspTool):
    name = "lsp_document_symbols"
    description = (
        "List semantic symbols defined in one file using LSP. Use this to understand the file "
        "structure "
        "such as classes, functions, methods, and variables before editing."
    )

    async def execute(self, **kwargs: Any) -> str:
        path = self._resolve_path(str(kwargs.get("path", "") or ""))
        self._set_display_path(path)
        try:
            result = await self.runtime.document_symbols(path)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        return _format_document_symbols(path, result)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[],
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path, relative to the project root or absolute.",
                    }
                },
                "required": ["path"],
            },
        )


class LspWorkspaceSymbolsTool(_BaseLspTool):
    name = "lsp_workspace_symbols"
    description = (
        "Search workspace-level semantic symbols using LSP. Use this when you know a symbol name "
        "but not its file, and you want classes/functions/constants instead of plain text matches."
    )

    async def execute(self, **kwargs: Any) -> str:
        query = str(kwargs.get("query", "") or "").strip()
        if not query:
            return "Error: query must not be empty"
        max_results = _coerce_positive_int(kwargs.get("max_results", 20), default=20)
        try:
            result = await self.runtime.workspace_symbols(query)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        return _format_workspace_symbols(result, max_results=max_results)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[],
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Symbol name or partial name to search for.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return.",
                        "minimum": 1,
                        "default": 20,
                    },
                },
                "required": ["query"],
            },
        )


class LspDiagnosticsTool(_BaseLspTool):
    name = "lsp_diagnostics"
    description = (
        "Get current LSP diagnostics for one file, including severity, position, and message. Use "
        "this before or after edits to understand parser/type-checker feedback."
    )

    async def execute(self, **kwargs: Any) -> str:
        path = self._resolve_path(str(kwargs.get("path", "") or ""))
        self._set_display_path(path)
        try:
            result = await self.runtime.diagnostics(path)
        except Exception as exc:  # noqa: BLE001
            return f"Error: {exc}"
        return _format_diagnostics(path, result)

    def definition(self) -> ToolDef:
        return ToolDef(
            name=self.name,
            description=self.description,
            parameters=[],
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path, relative to the project root or absolute.",
                    }
                },
                "required": ["path"],
            },
        )


def _position_schema(*, max_results_default: int) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path, relative to the project root or absolute.",
            },
            "line": {
                "type": "integer",
                "description": "1-based line number.",
                "minimum": 1,
            },
            "column": {
                "type": "integer",
                "description": "1-based column number. Defaults to 1.",
                "minimum": 1,
                "default": 1,
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of locations to return.",
                "minimum": 1,
                "default": max_results_default,
            },
        },
        "required": ["path", "line"],
    }


def create_lsp_tools(working_dir: str, runtime: LspRuntimeManager) -> list[Tool]:
    return [
        LspHoverTool(working_dir, runtime),
        LspDefinitionTool(working_dir, runtime),
        LspReferencesTool(working_dir, runtime),
        LspImplementationTool(working_dir, runtime),
        LspDocumentSymbolsTool(working_dir, runtime),
        LspWorkspaceSymbolsTool(working_dir, runtime),
        LspDiagnosticsTool(working_dir, runtime),
    ]


__all__ = [
    "LspDefinitionTool",
    "LspDiagnosticsTool",
    "LspDocumentSymbolsTool",
    "LspHoverTool",
    "LspImplementationTool",
    "LspReferencesTool",
    "LspWorkspaceSymbolsTool",
    "create_lsp_tools",
]
