"""Formatting helpers for MCP responses rendered back through Artel tools."""

from __future__ import annotations

import json
from typing import Any


def format_call_tool_result(result: Any) -> str:
    """Render an MCP tool result into a readable text blob."""
    content = getattr(result, "content", []) or []
    parts = [_format_content_item(item) for item in content]
    structured = getattr(result, "structuredContent", None)
    if structured:
        parts.append(_json_block(structured))
    rendered = "\n\n".join(part for part in parts if part).strip() or "(no content)"
    if bool(getattr(result, "isError", False)):
        return f"Error:\n{rendered}"
    return rendered


def format_prompt_result(result: Any) -> str:
    """Render a prompt template result."""
    parts: list[str] = []
    description = getattr(result, "description", "")
    if description:
        parts.append(str(description))
    for message in getattr(result, "messages", []) or []:
        role = getattr(message, "role", "assistant")
        parts.append(f"{role}: {_format_content_item(getattr(message, 'content', ''))}")
    return "\n\n".join(part for part in parts if part).strip() or "(empty prompt)"


def format_read_resource_result(result: Any) -> str:
    """Render resource contents."""
    rendered: list[str] = []
    for item in getattr(result, "contents", []) or []:
        item_name = type(item).__name__
        uri = str(getattr(item, "uri", ""))
        mime_type = getattr(item, "mimeType", None) or getattr(item, "mime_type", None)
        if item_name == "TextResourceContents":
            header = uri
            if mime_type:
                header += f" ({mime_type})"
            rendered.append(f"{header}\n{getattr(item, 'text', '')}")
        elif item_name == "BlobResourceContents":
            mime = mime_type or "application/octet-stream"
            blob = str(getattr(item, "blob", ""))
            rendered.append(f"{uri} ({mime})\n[blob: {len(blob)} base64 chars]")
    return "\n\n".join(rendered).strip() or "(empty resource)"


def format_tools_listing(tools: list[Any]) -> str:
    if not tools:
        return "No MCP tools available."
    lines = []
    for tool in tools:
        description = getattr(tool, "description", "") or getattr(tool, "title", "") or ""
        lines.append(f"- {getattr(tool, 'name', 'tool')}: {description}".rstrip(": "))
    return "\n".join(lines)


def format_prompts_listing(prompts: list[Any]) -> str:
    if not prompts:
        return "No MCP prompts available."
    lines = []
    for prompt in prompts:
        args = ", ".join(getattr(arg, "name", "") for arg in getattr(prompt, "arguments", []) or [])
        suffix = f" ({args})" if args else ""
        description = getattr(prompt, "description", "") or getattr(prompt, "title", "") or ""
        lines.append(f"- {getattr(prompt, 'name', 'prompt')}{suffix}: {description}".rstrip(": "))
    return "\n".join(lines)


def format_resources_listing(resources: list[Any], resource_templates: list[Any]) -> str:
    parts: list[str] = []
    if resources:
        parts.append("Resources:")
        parts.extend(
            f"- {getattr(resource, 'name', 'resource')}: {getattr(resource, 'uri', '')}"
            for resource in resources
        )
    if resource_templates:
        if parts:
            parts.append("")
        parts.append("Resource templates:")
        parts.extend(
            f"- {getattr(template, 'name', 'template')}: {getattr(template, 'uriTemplate', '')}"
            for template in resource_templates
        )
    return "\n".join(parts).strip() or "No MCP resources available."


def _format_content_item(item: Any) -> str:
    item_name = type(item).__name__
    if item_name == "TextContent":
        return str(getattr(item, "text", ""))
    if item_name == "ImageContent":
        mime_type = getattr(item, "mimeType", "")
        data_len = len(str(getattr(item, "data", "")))
        return f"[image: {mime_type}, {data_len} base64 chars]"
    if item_name == "AudioContent":
        mime_type = getattr(item, "mimeType", "")
        data_len = len(str(getattr(item, "data", "")))
        return f"[audio: {mime_type}, {data_len} base64 chars]"
    if item_name == "ResourceLink":
        return f"[resource] {getattr(item, 'name', '')}: {getattr(item, 'uri', '')}"
    if item_name == "EmbeddedResource":
        resource = getattr(item, "resource", None)
        resource_name = type(resource).__name__
        if resource_name == "TextResourceContents":
            uri = getattr(resource, "uri", "")
            text = getattr(resource, "text", "")
            return f"[embedded resource] {uri}\n{text}"
        if resource_name == "BlobResourceContents":
            mime = getattr(resource, "mimeType", None) or "application/octet-stream"
            uri = getattr(resource, "uri", "")
            blob_len = len(str(getattr(resource, "blob", "")))
            return f"[embedded resource] {uri} ({mime}, {blob_len} base64 chars)"
    if hasattr(item, "model_dump"):
        return _json_block(item.model_dump(mode="json"))
    return str(item)


def _json_block(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
