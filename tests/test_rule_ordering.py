from __future__ import annotations

import pytest
from click.testing import CliRunner


def test_rule_ordering_and_move_in_storage(monkeypatch, tmp_path):
    from worker_core import config as cfg_mod
    from worker_core.rules import add_rule, list_rules, move_rule

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    project_dir = tmp_path / "project"
    (project_dir / ".artel").mkdir(parents=True)

    first = add_rule(scope="project", text="First", project_dir=str(project_dir))
    add_rule(scope="project", text="Second", project_dir=str(project_dir))
    third = add_rule(scope="project", text="Third", project_dir=str(project_dir))

    assert [rule.text for rule in list_rules(str(project_dir)) if rule.scope == "project"] == [
        "First",
        "Second",
        "Third",
    ]

    moved = move_rule(third.id, project_dir=str(project_dir), position=1)
    assert moved.order == 1
    assert [rule.text for rule in list_rules(str(project_dir)) if rule.scope == "project"] == [
        "Third",
        "First",
        "Second",
    ]

    moved_again = move_rule(first.id, project_dir=str(project_dir), offset=1)
    assert moved_again.text == "First"
    assert [rule.text for rule in list_rules(str(project_dir)) if rule.scope == "project"] == [
        "Third",
        "Second",
        "First",
    ]


def test_rule_cli_move(monkeypatch, tmp_path):
    import importlib

    from worker_core import config as cfg_mod
    from worker_core.rules import add_rule

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()

    add_rule(scope="project", text="First", project_dir=str(tmp_path))
    second = add_rule(scope="project", text="Second", project_dir=str(tmp_path))

    cli_mod = importlib.import_module("worker_core.cli")
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["rule", "move", second.id, "--to", "1"])
    assert result.exit_code == 0
    assert f"Rule moved: {second.id} -> position 1" in result.output

    listed = runner.invoke(cli_mod.cli, ["rules"])
    assert listed.exit_code == 0
    first_line = listed.output.splitlines()[0]
    assert second.id in first_line


@pytest.mark.asyncio
async def test_tui_rule_move_local(monkeypatch, tmp_path):
    from worker_core import config as cfg_mod
    from worker_core.rules import add_rule, list_rules
    from worker_tui.app import WorkerApp

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()

    add_rule(scope="project", text="First", project_dir=str(tmp_path))
    second = add_rule(scope="project", text="Second", project_dir=str(tmp_path))

    app = WorkerApp()
    seen: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant": seen.append((content, role))  # type: ignore[method-assign]

    await app._cmd_rule(f"move {second.id} up")

    ordered = [rule.text for rule in list_rules(str(tmp_path)) if rule.scope == "project"]
    assert ordered == ["Second", "First"]
    assert any(f"Moved rule {second.id} to position 1." in message for message, _ in seen)
