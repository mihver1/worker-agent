"""ACP protocol integration checks against the real stdio subprocess."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import pytest
from artel_ai.models import Message, Role
from artel_core.sessions import SessionStore


async def _send_json(proc: asyncio.subprocess.Process, payload: dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    await proc.stdin.drain()


async def _read_json_messages_until(
    proc: asyncio.subprocess.Process,
    *,
    response_id: int,
    timeout_seconds: float = 15.0,
) -> list[dict[str, Any]]:
    assert proc.stdout is not None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    messages: list[dict[str, Any]] = []
    while loop.time() < deadline:
        remaining = deadline - loop.time()
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
        if not line:
            break
        message = json.loads(line.decode("utf-8"))
        messages.append(message)
        if message.get("id") == response_id:
            return messages
    raise AssertionError(f"Timed out waiting for response id={response_id}; received={messages!r}")


async def _read_json_messages_until_with_auto_permissions(
    proc: asyncio.subprocess.Process,
    *,
    response_id: int,
    permission_option_id: str = "approve",
    timeout_seconds: float = 15.0,
) -> list[dict[str, Any]]:
    assert proc.stdout is not None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    messages: list[dict[str, Any]] = []
    while loop.time() < deadline:
        remaining = deadline - loop.time()
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
        if not line:
            break
        message = json.loads(line.decode("utf-8"))
        messages.append(message)
        if message.get("method") == "session/request_permission" and "id" in message:
            await _send_json(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": message["id"],
                    "result": {
                        "outcome": {
                            "outcome": "selected",
                            "optionId": permission_option_id,
                        }
                    },
                },
            )
            continue
        if message.get("id") == response_id:
            return messages
    raise AssertionError(f"Timed out waiting for response id={response_id}; received={messages!r}")


async def _start_acp_subprocess(
    *,
    cwd: str,
    env: dict[str, str],
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "artel_core.cli",
        "acp",
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    if proc.stdin is not None:
        proc.stdin.close()
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except TimeoutError:
        proc.kill()
        await proc.wait()


def _config_option_current_value(config_options: list[dict[str, Any]], option_id: str) -> Any:
    for option in config_options:
        if option.get("id") == option_id:
            return option.get("currentValue")
    return None


def _session_updates(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        message.get("params", {}).get("update", {})
        for message in messages
        if message.get("method") == "session/update"
    ]


@pytest.mark.asyncio
async def test_artel_acp_setters_emit_valid_session_update_notifications(tmp_path):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PYTHONUNBUFFERED"] = "1"
    proc = await _start_acp_subprocess(cwd=str(tmp_path), env=env)
    try:
        await _send_json(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {"fs": {"readTextFile": True}},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            },
        )
        init_messages = await _read_json_messages_until(proc, response_id=0)
        assert init_messages[-1]["result"]["protocolVersion"] == 1

        await _send_json(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session/new",
                "params": {
                    "cwd": str(tmp_path),
                    "mcpServers": [],
                },
            },
        )
        new_messages = await _read_json_messages_until(proc, response_id=1)
        session_id = new_messages[-1]["result"]["sessionId"]

        await _send_json(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/set_mode",
                "params": {
                    "sessionId": session_id,
                    "modeId": "code",
                },
            },
        )
        mode_messages = await _read_json_messages_until(proc, response_id=2)
        mode_response = mode_messages[-1]
        assert "error" not in mode_response
        assert any(
            message.get("method") == "session/update"
            and message.get("params", {}).get("update", {}).get("sessionUpdate")
            == "current_mode_update"
            and message.get("params", {}).get("update", {}).get("currentModeId") == "code"
            for message in mode_messages
        )
        assert any(
            message.get("method") == "session/update"
            and message.get("params", {}).get("update", {}).get("sessionUpdate")
            == "config_option_update"
            and _config_option_current_value(
                message.get("params", {}).get("update", {}).get("configOptions", []),
                "mode",
            )
            == "code"
            for message in mode_messages
        )

        await _send_json(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/set_config_option",
                "params": {
                    "sessionId": session_id,
                    "configId": "thinking",
                    "value": "high",
                },
            },
        )
        thinking_messages = await _read_json_messages_until(proc, response_id=3)
        thinking_response = thinking_messages[-1]
        assert "error" not in thinking_response
        assert (
            _config_option_current_value(
                thinking_response["result"]["configOptions"],
                "thinking",
            )
            == "high"
        )
        assert any(
            message.get("method") == "session/update"
            and message.get("params", {}).get("update", {}).get("sessionUpdate")
            == "config_option_update"
            and _config_option_current_value(
                message.get("params", {}).get("update", {}).get("configOptions", []),
                "thinking",
            )
            == "high"
            for message in thinking_messages
        )
    finally:
        await _terminate_process(proc)


@pytest.mark.asyncio
async def test_artel_acp_file_tool_calls_publish_absolute_locations(tmp_path):
    home_dir = tmp_path / "home"
    project_dir = tmp_path / "project"
    support_dir = tmp_path / "support"
    home_dir.mkdir()
    project_dir.mkdir()
    support_dir.mkdir()
    (project_dir / "README.md").write_text(
        "\n".join(f"line-{index}" for index in range(1, 10)) + "\n",
        encoding="utf-8",
    )

    (project_dir / ".artel").mkdir()
    (project_dir / ".artel" / "config.toml").write_text(
        """
[agent]
model = "mock/mock-model"

[providers.mock]
type = "mock"
requires_api_key = false

[providers.mock.models.mock-model]
name = "Mock Model"
context_window = 32000
supports_tools = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (support_dir / "mock_provider_runtime.py").write_text(
        """
from __future__ import annotations

from artel_ai.models import Done, ModelInfo, TextDelta, ToolCallDelta, Usage
from artel_ai.provider import Provider


class MockRuntimeProvider(Provider):
    name = "mock"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._call_index = 0

    async def stream_chat(
        self,
        model,
        messages,
        *,
        tools=None,
        temperature=0.0,
        max_tokens=None,
        thinking_level="off",
    ):
        del model, messages, tools, temperature, max_tokens, thinking_level
        self._call_index += 1
        if self._call_index == 1:
            yield ToolCallDelta(
                id="tc_read",
                name="read",
                arguments={"path": "README.md", "start_line": 7},
            )
            yield Done(usage=Usage(input_tokens=1, output_tokens=1))
            return
        yield TextDelta(content="read finished")
        yield Done(usage=Usage(input_tokens=2, output_tokens=3))

    def list_models(self):
        return [
            ModelInfo(
                id="mock-model",
                provider="mock",
                name="Mock Model",
                context_window=32000,
                supports_tools=True,
            )
        ]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (support_dir / "sitecustomize.py").write_text(
        """
from mock_provider_runtime import MockRuntimeProvider
import artel_ai.providers as _providers

_original_create_default_registry = _providers.create_default_registry


def _patched_create_default_registry():
    registry = _original_create_default_registry()
    registry.register("mock", MockRuntimeProvider)
    return registry


_providers.create_default_registry = _patched_create_default_registry
""".strip()
        + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = (
        f"{support_dir}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else str(support_dir)
    )

    proc = await _start_acp_subprocess(cwd=str(project_dir), env=env)
    try:
        await _send_json(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {"fs": {"readTextFile": True}},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            },
        )
        init_messages = await _read_json_messages_until(proc, response_id=0)
        assert init_messages[-1]["result"]["protocolVersion"] == 1

        await _send_json(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session/new",
                "params": {
                    "cwd": str(project_dir),
                    "mcpServers": [],
                },
            },
        )
        new_messages = await _read_json_messages_until(proc, response_id=1)
        session_id = new_messages[-1]["result"]["sessionId"]

        await _send_json(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": "Read the README"}],
                },
            },
        )
        prompt_messages = await _read_json_messages_until(proc, response_id=2, timeout_seconds=10.0)
        prompt_response = prompt_messages[-1]
        assert "error" not in prompt_response

        tool_updates = _session_updates(prompt_messages)
        tool_call_updates = [
            update
            for update in tool_updates
            if update.get("sessionUpdate") == "tool_call" and update.get("toolCallId")
        ]
        assert len(tool_call_updates) == 1
        assert tool_call_updates[0]["locations"] == [
            {
                "path": str((project_dir / "README.md").resolve()),
                "line": 7,
            }
        ]
    finally:
        await _terminate_process(proc)


@pytest.mark.asyncio
async def test_artel_acp_lists_and_resumes_persisted_sessions_after_restart(tmp_path):
    home_dir = tmp_path / "home"
    project_dir = tmp_path / "project"
    db_path = home_dir / ".config" / "artel" / "sessions.db"
    home_dir.mkdir()
    project_dir.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PYTHONUNBUFFERED"] = "1"

    first_proc = await _start_acp_subprocess(cwd=str(project_dir), env=env)
    try:
        await _send_json(
            first_proc,
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {"fs": {"readTextFile": True}},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            },
        )
        init_messages = await _read_json_messages_until(first_proc, response_id=0)
        assert init_messages[-1]["result"]["protocolVersion"] == 1

        await _send_json(
            first_proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session/new",
                "params": {
                    "cwd": str(project_dir),
                    "mcpServers": [],
                },
            },
        )
        new_messages = await _read_json_messages_until(first_proc, response_id=1)
        session_id = new_messages[-1]["result"]["sessionId"]
    finally:
        await _terminate_process(first_proc)
    store = SessionStore(str(db_path))
    await store.open()
    try:
        await store.add_message(session_id, Message(role=Role.USER, content="Restored user"))
        await store.add_message(
            session_id,
            Message(
                role=Role.ASSISTANT,
                reasoning="Restored reasoning",
                content="Restored assistant",
            ),
        )
    finally:
        await store.close()

    second_proc = await _start_acp_subprocess(cwd=str(project_dir), env=env)
    try:
        await _send_json(
            second_proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {"fs": {"readTextFile": True}},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            },
        )
        init_messages = await _read_json_messages_until(second_proc, response_id=2)
        session_capabilities = init_messages[-1]["result"]["agentCapabilities"][
            "sessionCapabilities"
        ]
        assert session_capabilities["list"] == {}
        assert session_capabilities["resume"] == {}

        await _send_json(
            second_proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/list",
                "params": {
                    "cwd": str(project_dir),
                },
            },
        )
        list_messages = await _read_json_messages_until(second_proc, response_id=3)
        list_response = list_messages[-1]
        assert "error" not in list_response
        assert len(list_response["result"]["sessions"]) == 1
        listed_session = list_response["result"]["sessions"][0]
        assert listed_session["cwd"] == str(project_dir)
        assert listed_session["sessionId"] == session_id
        assert "updatedAt" in listed_session

        await _send_json(
            second_proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "session/load",
                "params": {
                    "cwd": str(project_dir),
                    "sessionId": session_id,
                    "mcpServers": [],
                },
            },
        )
        load_messages = await _read_json_messages_until(second_proc, response_id=4)
        load_response = load_messages[-1]
        assert "error" not in load_response
        load_updates = _session_updates(load_messages)
        assert any(
            update.get("sessionUpdate") == "user_message_chunk"
            and update.get("content", {}).get("text") == "Restored user"
            for update in load_updates
        )
        assert any(
            update.get("sessionUpdate") == "agent_thought_chunk"
            and update.get("content", {}).get("text") == "Restored reasoning"
            for update in load_updates
        )
        assert any(
            update.get("sessionUpdate") == "agent_message_chunk"
            and update.get("content", {}).get("text") == "Restored assistant"
            for update in load_updates
        )

        await _send_json(
            second_proc,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "session/resume",
                "params": {
                    "cwd": str(project_dir),
                    "sessionId": session_id,
                    "mcpServers": [],
                },
            },
        )
        resume_messages = await _read_json_messages_until(second_proc, response_id=5)
        resume_response = resume_messages[-1]
        assert "error" not in resume_response
        resume_updates = _session_updates(resume_messages)
        assert any(
            update.get("sessionUpdate") == "user_message_chunk"
            and update.get("content", {}).get("text") == "Restored user"
            for update in resume_updates
        )
        assert any(
            update.get("sessionUpdate") == "agent_thought_chunk"
            and update.get("content", {}).get("text") == "Restored reasoning"
            for update in resume_updates
        )
        assert any(
            update.get("sessionUpdate") == "agent_message_chunk"
            and update.get("content", {}).get("text") == "Restored assistant"
            for update in resume_updates
        )
        assert (
            _config_option_current_value(
                resume_response["result"]["configOptions"],
                "thinking",
            )
            == "off"
        )
    finally:
        await _terminate_process(second_proc)


@pytest.mark.asyncio
async def test_artel_acp_tool_call_permission_flow_uses_tracked_tool_call_ids(tmp_path):
    home_dir = tmp_path / "home"
    project_dir = tmp_path / "project"
    support_dir = tmp_path / "support"
    home_dir.mkdir()
    project_dir.mkdir()
    support_dir.mkdir()

    (project_dir / ".artel").mkdir()
    (project_dir / ".artel" / "config.toml").write_text(
        """
[agent]
model = "mock/mock-model"

[providers.mock]
type = "mock"
requires_api_key = false

[providers.mock.models.mock-model]
name = "Mock Model"
context_window = 32000
supports_tools = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (support_dir / "mock_provider_runtime.py").write_text(
        """
from __future__ import annotations

from artel_ai.models import Done, ModelInfo, TextDelta, ToolCallDelta, Usage
from artel_ai.provider import Provider


class MockRuntimeProvider(Provider):
    name = "mock"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._call_index = 0

    async def stream_chat(
        self,
        model,
        messages,
        *,
        tools=None,
        temperature=0.0,
        max_tokens=None,
        thinking_level="off",
    ):
        del model, messages, tools, temperature, max_tokens, thinking_level
        self._call_index += 1
        if self._call_index == 1:
            yield ToolCallDelta(
                id="tc_shell",
                name="bash",
                arguments={"command": "printf integration-ok"},
            )
            yield Done(usage=Usage(input_tokens=1, output_tokens=1))
            return
        yield TextDelta(content="bash finished")
        yield Done(usage=Usage(input_tokens=2, output_tokens=3))

    def list_models(self):
        return [
            ModelInfo(
                id="mock-model",
                provider="mock",
                name="Mock Model",
                context_window=32000,
                supports_tools=True,
            )
        ]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (support_dir / "sitecustomize.py").write_text(
        """
from mock_provider_runtime import MockRuntimeProvider
import artel_ai.providers as _providers

_original_create_default_registry = _providers.create_default_registry


def _patched_create_default_registry():
    registry = _original_create_default_registry()
    registry.register("mock", MockRuntimeProvider)
    return registry


_providers.create_default_registry = _patched_create_default_registry
""".strip()
        + "\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["HOME"] = str(home_dir)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = (
        f"{support_dir}{os.pathsep}{env['PYTHONPATH']}"
        if env.get("PYTHONPATH")
        else str(support_dir)
    )

    proc = await _start_acp_subprocess(cwd=str(project_dir), env=env)
    try:
        await _send_json(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": 1,
                    "clientCapabilities": {"fs": {"readTextFile": True}},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            },
        )
        init_messages = await _read_json_messages_until(proc, response_id=0)
        assert init_messages[-1]["result"]["protocolVersion"] == 1

        await _send_json(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "session/new",
                "params": {
                    "cwd": str(project_dir),
                    "mcpServers": [],
                },
            },
        )
        new_messages = await _read_json_messages_until(proc, response_id=1)
        session_id = new_messages[-1]["result"]["sessionId"]

        await _send_json(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "session/prompt",
                "params": {
                    "sessionId": session_id,
                    "prompt": [{"type": "text", "text": "Run a shell command"}],
                },
            },
        )
        prompt_messages = await _read_json_messages_until_with_auto_permissions(
            proc,
            response_id=2,
            timeout_seconds=10.0,
        )
        prompt_response = prompt_messages[-1]
        assert "error" not in prompt_response
        permission_requests = [
            message
            for message in prompt_messages
            if message.get("method") == "session/request_permission"
        ]
        assert len(permission_requests) == 1
        tool_call = permission_requests[0]["params"]["toolCall"]
        tool_call_id = tool_call["toolCallId"]
        assert tool_call["status"] == "pending"
        assert tool_call["rawInput"] == {"command": "printf integration-ok"}

        tool_updates = _session_updates(prompt_messages)
        assert any(
            update.get("sessionUpdate") == "tool_call"
            and update.get("toolCallId") == tool_call_id
            and update.get("status") == "pending"
            for update in tool_updates
        )
        assert any(
            update.get("sessionUpdate") == "tool_call_update"
            and update.get("toolCallId") == tool_call_id
            and update.get("status") == "in_progress"
            for update in tool_updates
        )
        assert any(
            update.get("sessionUpdate") == "tool_call_update"
            and update.get("toolCallId") == tool_call_id
            and update.get("status") == "completed"
            for update in tool_updates
        )
    finally:
        await _terminate_process(proc)
