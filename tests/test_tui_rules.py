from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from artel_tui.app import RuleEditorSubmitted


@pytest.mark.asyncio
async def test_rule_add_opens_dialog_and_saves(monkeypatch, tmp_path):
    from artel_core import config as cfg_mod
    from artel_tui.app import ArtelApp

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())
    app = ArtelApp()
    seen: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant": seen.append((content, role))  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        await app._handle_command("/rule add")
        await pilot.pause()
        assert app._inline_rule_editor_panel().is_open() is True
        await app.on_rule_editor_submitted(
            RuleEditorSubmitted(
                {"scope": "project", "enabled": True, "text": "Do not use bash."}
            )
        )
        await pilot.pause()

    assert any("Added rule" in message for message, _ in seen)


@pytest.mark.asyncio
async def test_rule_edit_opens_dialog_for_existing_rule(monkeypatch, tmp_path):
    from artel_core import config as cfg_mod
    from artel_core.rules import add_rule, list_rules
    from artel_tui.app import ArtelApp

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    rule = add_rule(scope="project", text="Original rule", project_dir=str(tmp_path))
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())
    app = ArtelApp()
    seen: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant": seen.append((content, role))  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        await app._handle_command(f"/rule edit {rule.id}")
        await pilot.pause()
        assert app._inline_rule_editor_panel().is_open() is True
        await app.on_rule_editor_submitted(
            RuleEditorSubmitted(
                {"scope": "project", "enabled": False, "text": "Updated rule"}
            )
        )
        await pilot.pause()

    rules = list_rules(str(tmp_path))
    assert any(item.text == "Updated rule" and item.enabled is False for item in rules)
    assert any(
        "Updated rule" in message or message == "Updated rule" for message, _ in seen
    ) or any(item.text == "Updated rule" for item in rules)


@pytest.mark.asyncio
async def test_rules_command_lists_rules(monkeypatch, tmp_path):
    from artel_core import config as cfg_mod
    from artel_core.rules import add_rule
    from artel_tui.app import ArtelApp

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()
    add_rule(scope="project", text="Use pytest.", project_dir=str(tmp_path))
    monkeypatch.setattr("artel_tui.app.ArtelApp._init_local_session", AsyncMock())

    app = ArtelApp()
    seen: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant": seen.append((content, role))  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()
        await app._handle_command("/rules")
        await pilot.pause()

    assert any("Configured rules:" in message for message, _ in seen)
    assert any("persisted=enabled" in message for message, _ in seen)
    assert any("effective=enabled" in message for message, _ in seen)
    assert any("Use pytest." in message for message, _ in seen)
