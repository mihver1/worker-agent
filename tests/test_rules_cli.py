from __future__ import annotations

from click.testing import CliRunner


def test_rule_cli_add_list_edit_enable_disable_delete(monkeypatch, tmp_path):
    import importlib

    from artel_core import config as cfg_mod

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()

    cli_mod = importlib.import_module("artel_core.cli")
    runner = CliRunner()

    add_result = runner.invoke(
        cli_mod.cli,
        ["rule", "add", "--scope", "project", "--text", "Do not use bash."],
    )
    assert add_result.exit_code == 0
    assert "Rule added:" in add_result.output
    rule_id = add_result.output.split("Rule added:", 1)[1].split()[0].strip()

    list_result = runner.invoke(cli_mod.cli, ["rules"])
    assert list_result.exit_code == 0
    assert rule_id in list_result.output
    assert "Do not use bash." in list_result.output

    edit_result = runner.invoke(
        cli_mod.cli,
        ["rule", "edit", rule_id, "--text", "Do not use shell.", "--disable"],
    )
    assert edit_result.exit_code == 0
    assert "Rule updated:" in edit_result.output

    enable_result = runner.invoke(cli_mod.cli, ["rule", "enable", rule_id])
    assert enable_result.exit_code == 0
    assert f"Rule enabled: {rule_id}" in enable_result.output

    disable_result = runner.invoke(cli_mod.cli, ["rule", "disable", rule_id])
    assert disable_result.exit_code == 0
    assert f"Rule disabled: {rule_id}" in disable_result.output

    delete_result = runner.invoke(cli_mod.cli, ["rule", "delete", rule_id])
    assert delete_result.exit_code == 0
    assert f"Rule deleted: {rule_id}" in delete_result.output


def test_rule_cli_remove_alias_errors_for_missing_rule(monkeypatch, tmp_path):
    import importlib

    from artel_core import config as cfg_mod

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".artel").mkdir()

    cli_mod = importlib.import_module("artel_core.cli")
    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["rule", "remove", "missing-id"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()
