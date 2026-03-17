"""Permission system — check tool access before execution."""

from __future__ import annotations

import fnmatch
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from artel_core.config import PermissionsConfig


class Decision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


# Callback signature: (tool_name, args) → bool (approved or not)
PermissionCallback = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass
class PermissionResult:
    allowed: bool
    reason: str = ""


class PermissionPolicy:
    """Evaluate tool permissions based on config."""

    def __init__(self, config: PermissionsConfig, callback: PermissionCallback | None = None):
        self.config = config
        self.callback = callback

    async def check(self, tool_name: str, args: dict[str, Any]) -> PermissionResult:
        """Check if a tool call is permitted."""
        decision = self._get_decision(tool_name, args)

        if decision == Decision.ALLOW:
            return PermissionResult(allowed=True)

        if decision == Decision.DENY:
            return PermissionResult(
                allowed=False, reason=f"Tool '{tool_name}' is denied by policy."
            )

        # ASK — requires user confirmation
        if self.callback:
            approved = await self.callback(tool_name, args)
            if approved:
                return PermissionResult(allowed=True)
            return PermissionResult(allowed=False, reason="User denied permission.")

        # No callback — default deny for safety
        return PermissionResult(
            allowed=False, reason="Permission required but no callback configured."
        )

    def _get_decision(self, tool_name: str, args: dict[str, Any]) -> Decision:
        """Determine the base decision from config."""
        if tool_name == "bash":
            return self._check_bash(args)
        if tool_name in {
            "read",
            "grep",
            "find",
            "ls",
            "glob",
            "ag",
            "ripgrep",
            "lsp_hover",
            "lsp_definition",
            "lsp_references",
            "lsp_implementation",
            "lsp_document_symbols",
            "lsp_workspace_symbols",
            "lsp_diagnostics",
            "web_search",
            "web_fetch",
            "list_delegates",
            "get_delegate",
        }:
            return Decision.ALLOW
        if tool_name == "worktree":
            return self._worktree_decision()
        if tool_name in {"delegate_task", "cancel_delegate"}:
            return Decision.ASK

        mapping = {
            "edit": self.config.edit,
            "write": self.config.write,
        }
        raw = mapping.get(tool_name, "ask")
        return Decision(raw)

    def _check_bash(self, args: dict[str, Any]) -> Decision:
        """Check bash permissions with glob matching for specific commands."""
        command = args.get("command", "")

        # Check specific command rules (last match wins)
        last_match: Decision | None = None
        for pattern, policy in self.config.bash_commands.items():
            if fnmatch.fnmatch(command, pattern):
                last_match = Decision(policy)

        if last_match is not None:
            return last_match

        # Fall back to general bash policy
        return Decision(self.config.bash)

    def _worktree_decision(self) -> Decision:
        raw = self.config.write
        return Decision(raw)
