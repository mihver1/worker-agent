"""ACP protocol integration checks against the real stdio subprocess."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

import pytest


async def _send_json(proc: asyncio.subprocess.Process, payload: dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    await proc.stdin.drain()


async def _read_json_messages_until(
    proc: asyncio.subprocess.Process,
    *,
    response_id: int,
    timeout_seconds: float = 5.0,
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


async def _start_acp_subprocess(
    *,
    cwd: str,
    env: dict[str, str],
) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "worker_core.cli",
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


@pytest.mark.asyncio
async def test_worker_acp_setters_emit_valid_session_update_notifications(tmp_path):
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
        assert _config_option_current_value(
            thinking_response["result"]["configOptions"],
            "thinking",
        ) == "high"
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
async def test_worker_acp_lists_and_resumes_persisted_sessions_after_restart(tmp_path):
    home_dir = tmp_path / "home"
    project_dir = tmp_path / "project"
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
                "method": "session/resume",
                "params": {
                    "cwd": str(project_dir),
                    "sessionId": session_id,
                    "mcpServers": [],
                },
            },
        )
        resume_messages = await _read_json_messages_until(second_proc, response_id=4)
        resume_response = resume_messages[-1]
        assert "error" not in resume_response
        assert _config_option_current_value(
            resume_response["result"]["configOptions"],
            "thinking",
        ) == "off"
    finally:
        await _terminate_process(second_proc)
