from click.testing import CliRunner


def test_web_help_marks_surface_unavailable_in_current_checkout():
    from artel_core import cli as cli_mod

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["web", "--help"])

    assert result.exit_code == 0
    assert "web UI unavailable in this checkout" in result.output
    assert "Starts the NiceGUI-based web UI." not in result.output


def test_top_level_help_does_not_advertise_web_as_working_ui():
    from artel_core import cli as cli_mod

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["--help"])

    assert result.exit_code == 0
    assert "Reserved web command; unavailable in this checkout." in result.output
    assert "web          Start the NiceGUI-based web UI." not in result.output
