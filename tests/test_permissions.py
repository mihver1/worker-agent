"""Tests for the permission system."""

from __future__ import annotations

import pytest

from worker_core.config import PermissionsConfig
from worker_core.permissions import Decision, PermissionPolicy


@pytest.mark.asyncio
async def test_allow_by_default():
    config = PermissionsConfig(edit="allow", write="allow", bash="allow")
    policy = PermissionPolicy(config)
    result = await policy.check("edit", {"path": "foo.py", "search": "a", "replace": "b"})
    assert result.allowed is True


@pytest.mark.asyncio
async def test_deny_bash():
    config = PermissionsConfig(bash="deny")
    policy = PermissionPolicy(config)
    result = await policy.check("bash", {"command": "rm -rf /"})
    assert result.allowed is False
    assert "denied" in result.reason


@pytest.mark.asyncio
async def test_ask_with_callback_approved():
    config = PermissionsConfig(bash="ask")

    async def approve(tool: str, args: dict) -> bool:
        return True

    policy = PermissionPolicy(config, callback=approve)
    result = await policy.check("bash", {"command": "ls"})
    assert result.allowed is True


@pytest.mark.asyncio
async def test_ask_with_callback_denied():
    config = PermissionsConfig(bash="ask")

    async def deny(tool: str, args: dict) -> bool:
        return False

    policy = PermissionPolicy(config, callback=deny)
    result = await policy.check("bash", {"command": "ls"})
    assert result.allowed is False


@pytest.mark.asyncio
async def test_ask_without_callback():
    config = PermissionsConfig(bash="ask")
    policy = PermissionPolicy(config, callback=None)
    result = await policy.check("bash", {"command": "ls"})
    assert result.allowed is False  # No callback → deny for safety


@pytest.mark.asyncio
async def test_bash_glob_allow():
    config = PermissionsConfig(bash="deny", bash_commands={"git *": "allow"})
    policy = PermissionPolicy(config)
    result = await policy.check("bash", {"command": "git status"})
    assert result.allowed is True


@pytest.mark.asyncio
async def test_bash_glob_deny():
    config = PermissionsConfig(bash="allow", bash_commands={"rm *": "deny"})
    policy = PermissionPolicy(config)
    result = await policy.check("bash", {"command": "rm -rf /"})
    assert result.allowed is False


@pytest.mark.asyncio
async def test_bash_glob_no_match_falls_through():
    config = PermissionsConfig(bash="allow", bash_commands={"git *": "deny"})
    policy = PermissionPolicy(config)
    result = await policy.check("bash", {"command": "echo hello"})
    assert result.allowed is True  # Falls through to bash="allow"


@pytest.mark.asyncio
async def test_read_always_allowed():
    config = PermissionsConfig(edit="deny", write="deny", bash="deny")
    policy = PermissionPolicy(config)
    result = await policy.check("read", {"path": "foo.py"})
    assert result.allowed is True


@pytest.mark.asyncio
async def test_unknown_tool_asks():
    config = PermissionsConfig()

    async def deny(tool: str, args: dict) -> bool:
        return False

    policy = PermissionPolicy(config, callback=deny)
    result = await policy.check("custom_tool", {})
    assert result.allowed is False  # "ask" for unknown → denied by callback
