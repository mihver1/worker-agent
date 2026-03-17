"""Helpers for undoing recent AI file edits and rewinding sessions."""

from __future__ import annotations

from typing import Any


def _message_role(message: Any) -> str:
    role = getattr(message, "role", "")
    if hasattr(role, "value"):
        return str(role.value)
    if isinstance(message, dict):
        return str(message.get("role", "") or "")
    return str(role or "")


def _tool_result_payload(message: Any) -> dict[str, Any] | None:
    if isinstance(message, dict):
        payload = message.get("tool_result")
        return payload if isinstance(payload, dict) else None
    payload = getattr(message, "tool_result", None)
    if payload is None:
        return None
    if hasattr(payload, "model_dump"):
        dumped = payload.model_dump()
        return dumped if isinstance(dumped, dict) else None
    if isinstance(payload, dict):
        return payload
    return None


def collect_last_ai_changed_paths(messages: list[Any]) -> list[str]:
    """Return unique file paths changed by the latest AI turn in the session.

    Heuristic: scan backwards and collect file-diff tool results until the first
    user message boundary is reached after at least one diff result was found.
    """

    found: list[str] = []
    seen: set[str] = set()
    collecting = False

    for message in reversed(messages):
        role = _message_role(message)
        if role == "user" and collecting:
            break
        payload = _tool_result_payload(message)
        if not isinstance(payload, dict):
            continue
        display = payload.get("display")
        if not isinstance(display, dict):
            continue
        if display.get("kind") != "file_diff":
            continue
        path = str(display.get("path", "") or "").strip()
        if not path:
            continue
        collecting = True
        if path not in seen:
            seen.add(path)
            found.append(path)

    found.reverse()
    return found


__all__ = ["collect_last_ai_changed_paths"]
