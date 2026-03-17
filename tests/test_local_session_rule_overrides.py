from __future__ import annotations

import pytest
from artel_ai.models import Done, ToolCallDelta, Usage
from artel_core.agent import AgentEventType, AgentSession
from artel_core.rules import add_rule
from artel_core.tools.builtins import BashTool


class _Provider:
    def __init__(self, responses):
        self._responses = responses
        self._index = 0

    async def stream_chat(self, model, messages, **kwargs):
        events = self._responses[self._index]
        self._index += 1
        for event in events:
            yield event

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_local_session_rule_disable_override_allows_tool(monkeypatch, tmp_path):
    from artel_core import config as cfg_mod
    from artel_tui.app import ArtelApp

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    rule = add_rule(scope="project", text="Do not use bash.", project_dir=str(tmp_path))

    provider = _Provider(
        [[ToolCallDelta(id="tc_1", name="bash", arguments={"command": "pwd"}), Done(usage=Usage())]]
    )
    session = AgentSession(
        provider=provider, model="test", tools=[BashTool(str(tmp_path))], project_dir=str(tmp_path)
    )

    app = ArtelApp()
    app._session = session
    seen: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant": seen.append((content, role))  # type: ignore[method-assign]

    await app._cmd_rule(f"disable {rule.id}")
    events = [event async for event in session.run("show cwd")]
    results = [event for event in events if event.type == AgentEventType.TOOL_RESULT]

    assert any(f"Disabled rule {rule.id} for this session." in message for message, _ in seen)
    assert results
    assert results[0].is_error is False


@pytest.mark.asyncio
async def test_local_session_rule_reset_restores_enforcement(monkeypatch, tmp_path):
    from artel_core import config as cfg_mod
    from artel_tui.app import ArtelApp

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    rule = add_rule(scope="project", text="Do not use bash.", project_dir=str(tmp_path))

    provider = _Provider(
        [[ToolCallDelta(id="tc_1", name="bash", arguments={"command": "pwd"}), Done(usage=Usage())]]
    )
    session = AgentSession(
        provider=provider, model="test", tools=[BashTool(str(tmp_path))], project_dir=str(tmp_path)
    )

    app = ArtelApp()
    app._session = session
    app._add_message = lambda content, role="assistant": None  # type: ignore[method-assign]

    await app._cmd_rule(f"disable {rule.id}")
    await app._cmd_rule(f"reset {rule.id}")

    provider2 = _Provider(
        [[ToolCallDelta(id="tc_1", name="bash", arguments={"command": "pwd"}), Done(usage=Usage())]]
    )
    session.provider = provider2
    events = [event async for event in session.run("show cwd")]
    results = [event for event in events if event.type == AgentEventType.TOOL_RESULT]

    assert results
    assert results[0].is_error is True
    assert "Refused:" in results[0].content
