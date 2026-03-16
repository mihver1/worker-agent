from __future__ import annotations

import pytest
from worker_ai.models import Done, ToolCallDelta, Usage
from worker_core.agent import AgentEventType, AgentSession
from worker_core.rules import (
    add_rule,
    delete_rule,
    format_rules_for_system_prompt,
    get_rule,
    list_rules,
    update_rule,
)
from worker_core.tools.builtins import BashTool, EditTool


class _Provider:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []
        self._index = 0

    async def stream_chat(self, model, messages, **kwargs):
        self.calls.append({"model": model, "messages": messages, **kwargs})
        events = self._responses[self._index]
        self._index += 1
        for event in events:
            yield event

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_system_prompt_includes_active_rules(tmp_path, monkeypatch):
    from worker_core import config as cfg_mod

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    project_dir = tmp_path / "project"
    (project_dir / ".artel").mkdir(parents=True)

    add_rule(scope="global", text="Always keep tests updated.", project_dir=str(project_dir))
    add_rule(scope="project", text="Do not use bash.", project_dir=str(project_dir))

    prompt = AgentSession._build_system_prompt("", str(project_dir))
    assert "## Active Rules" in prompt
    assert "Always keep tests updated." in prompt
    assert "Do not use bash." in prompt


@pytest.mark.asyncio
async def test_rule_blocks_forbidden_bash_tool(tmp_path, monkeypatch):
    from worker_core import config as cfg_mod

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    project_dir = tmp_path / "project"
    (project_dir / ".artel").mkdir(parents=True)
    add_rule(scope="project", text="Do not use bash.", project_dir=str(project_dir))

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
    assert results[0].is_error is True
    assert "Refused:" in results[0].content
    assert "Do not use bash." in results[0].content


@pytest.mark.asyncio
async def test_rule_blocks_edit_of_read_only_path(tmp_path, monkeypatch):
    from worker_core import config as cfg_mod

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    project_dir = tmp_path / "project"
    artel_dir = project_dir / ".artel"
    artel_dir.mkdir(parents=True)
    target = project_dir / "README.md"
    target.write_text("hello\n", encoding="utf-8")
    add_rule(scope="project", text="README.md is read-only", project_dir=str(project_dir))

    provider = _Provider(
        [
            [
                ToolCallDelta(
                    id="tc_1",
                    name="edit",
                    arguments={"path": "README.md", "search": "hello", "replace": "updated"},
                ),
                Done(usage=Usage()),
            ]
        ]
    )
    session = AgentSession(
        provider=provider,
        model="test",
        tools=[EditTool(str(project_dir))],
        project_dir=str(project_dir),
    )

    events = [event async for event in session.run("update readme")]
    results = [event for event in events if event.type == AgentEventType.TOOL_RESULT]
    assert results
    assert results[0].is_error is True
    assert "read-only" in results[0].content.lower()
    assert target.read_text(encoding="utf-8") == "hello\n"


def test_rule_crud_and_scope(tmp_path, monkeypatch):
    from worker_core import config as cfg_mod

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    project_dir = tmp_path / "project"
    (project_dir / ".artel").mkdir(parents=True)

    global_rule = add_rule(scope="global", text="Global rule.", project_dir=str(project_dir))
    project_rule = add_rule(
        scope="project", text="Project rule.", project_dir=str(project_dir), enabled=False
    )

    rules = list_rules(str(project_dir))
    assert [rule.id for rule in rules] == [global_rule.id, project_rule.id]
    assert get_rule(global_rule.id, str(project_dir)) is not None

    updated = update_rule(
        project_rule.id, project_dir=str(project_dir), text="Project rule updated.", enabled=True
    )
    assert updated.text == "Project rule updated."
    assert updated.enabled is True

    deleted = delete_rule(global_rule.id, str(project_dir))
    assert deleted is not None
    assert get_rule(global_rule.id, str(project_dir)) is None
    assert format_rules_for_system_prompt(str(project_dir))
