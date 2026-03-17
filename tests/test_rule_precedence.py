from __future__ import annotations

import pytest
from artel_ai.models import Done, ToolCallDelta, Usage
from artel_core.agent import AgentEventType, AgentSession
from artel_core.rules import add_rule, move_rule
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
async def test_first_matching_rule_by_order_wins(monkeypatch, tmp_path):
    from artel_core import config as cfg_mod

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    project_dir = tmp_path / "project"
    (project_dir / ".artel").mkdir(parents=True)

    add_rule(scope="project", text="Do not use bash.", project_dir=str(project_dir))
    specific = add_rule(scope="project", text="Do not run `pwd`.", project_dir=str(project_dir))
    move_rule(specific.id, project_dir=str(project_dir), position=1)

    provider = _Provider(
        [[ToolCallDelta(id="tc_1", name="bash", arguments={"command": "pwd"}), Done(usage=Usage())]]
    )
    session = AgentSession(
        provider=provider,
        model="test",
        tools=[BashTool(str(project_dir))],
        project_dir=str(project_dir),
    )

    events = [event async for event in session.run("show cwd")]
    results = [event for event in events if event.type == AgentEventType.TOOL_RESULT]
    assert results
    assert "pwd" in results[0].content
