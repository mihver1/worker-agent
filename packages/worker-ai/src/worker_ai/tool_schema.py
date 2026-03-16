"""Helpers for converting internal tool definitions to provider-specific schemas."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from worker_ai.models import ToolDef

_JSON_TYPE_MAP = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "object": "OBJECT",
    "array": "ARRAY",
    "null": "NULL",
}

_SCALAR_SCHEMA_KEYS = (
    "title",
    "description",
    "enum",
    "default",
    "format",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
)


def tool_input_schema(tool: ToolDef) -> dict[str, Any]:
    """Return the tool input schema, preferring raw passthrough when available."""
    if tool.input_schema:
        return normalize_json_schema(tool.input_schema)

    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in tool.parameters:
        prop: dict[str, Any] = {"type": param.type, "description": param.description}
        if param.enum:
            prop["enum"] = param.enum
        if param.default is not None:
            prop["default"] = param.default
        properties[param.name] = prop
        if param.required:
            required.append(param.name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def normalize_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve local refs where possible and strip definitions once inlined."""
    root = deepcopy(schema)
    resolved = _resolve_schema_node(root, root, ())
    if not _schema_has_refs(resolved):
        return _strip_schema_definitions(resolved)
    return resolved


def json_schema_to_gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert standard JSON Schema into Gemini function declaration schema."""
    result: dict[str, Any] = {}

    raw_type = schema.get("type", "object")
    nullable = False
    if isinstance(raw_type, list):
        variants = [value for value in raw_type if value != "null"]
        nullable = "null" in raw_type
        raw_type = variants[0] if variants else "string"

    schema_type = str(raw_type or "object")
    result["type"] = _JSON_TYPE_MAP.get(schema_type, schema_type.upper())
    if nullable:
        result["nullable"] = True

    for key in _SCALAR_SCHEMA_KEYS:
        if key in schema:
            result[key] = schema[key]

    if result["type"] == "OBJECT":
        raw_properties = schema.get("properties", {})
        if isinstance(raw_properties, dict):
            result["properties"] = {
                key: json_schema_to_gemini_schema(value)
                for key, value in raw_properties.items()
                if isinstance(value, dict)
            }
        raw_required = schema.get("required", [])
        if isinstance(raw_required, list) and raw_required:
            result["required"] = [str(value) for value in raw_required]
        additional_properties = schema.get("additionalProperties")
        if isinstance(additional_properties, dict):
            result["additionalProperties"] = json_schema_to_gemini_schema(additional_properties)
        elif isinstance(additional_properties, bool):
            result["additionalProperties"] = additional_properties

    if result["type"] == "ARRAY":
        items = schema.get("items")
        if isinstance(items, dict):
            result["items"] = json_schema_to_gemini_schema(items)

    for key in ("oneOf", "anyOf", "allOf"):
        raw_value = schema.get(key)
        if isinstance(raw_value, list):
            result[key] = [
                json_schema_to_gemini_schema(item) if isinstance(item, dict) else item
                for item in raw_value
            ]

    return result


def _resolve_schema_node(node: Any, root: dict[str, Any], ref_stack: tuple[str, ...]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/") and ref not in ref_stack:
            resolved = _resolve_json_pointer(root, ref)
            if isinstance(resolved, dict):
                merged = deepcopy(resolved)
                for key, value in node.items():
                    if key == "$ref":
                        continue
                    merged[key] = value
                return _resolve_schema_node(merged, root, (*ref_stack, ref))
        return {key: _resolve_schema_node(value, root, ref_stack) for key, value in node.items()}
    if isinstance(node, list):
        return [_resolve_schema_node(item, root, ref_stack) for item in node]
    return node


def _resolve_json_pointer(document: dict[str, Any], pointer: str) -> Any:
    current: Any = document
    for part in pointer.removeprefix("#/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _schema_has_refs(node: Any) -> bool:
    if isinstance(node, dict):
        if "$ref" in node:
            return True
        return any(_schema_has_refs(value) for value in node.values())
    if isinstance(node, list):
        return any(_schema_has_refs(item) for item in node)
    return False


def _strip_schema_definitions(node: Any) -> Any:
    if isinstance(node, dict):
        return {
            key: _strip_schema_definitions(value)
            for key, value in node.items()
            if key not in {"$defs", "definitions"}
        }
    if isinstance(node, list):
        return [_strip_schema_definitions(item) for item in node]
    return node
