"""Tests for follow-friendly tool call location inference."""

from __future__ import annotations

from artel_server.server import _tool_locations


def test_tool_locations_resolve_read_paths_and_default_to_first_line(tmp_path):
    locations = _tool_locations("read", {"path": "src/app.py"}, cwd=str(tmp_path))

    assert locations == [
        {
            "path": str((tmp_path / "src" / "app.py").resolve()),
            "line": 1,
        }
    ]


def test_tool_locations_compute_edit_line_from_unique_match(tmp_path):
    target = tmp_path / "notes.txt"
    target.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")

    locations = _tool_locations(
        "edit",
        {"path": "notes.txt", "search": "beta", "replace": "delta"},
        cwd=str(tmp_path),
    )

    assert locations == [
        {
            "path": str(target.resolve()),
            "line": 2,
        }
    ]


def test_tool_locations_omit_edit_line_when_match_is_ambiguous(tmp_path):
    target = tmp_path / "dups.txt"
    target.write_text("repeat\nrepeat\n", encoding="utf-8")

    locations = _tool_locations(
        "edit",
        {"path": "dups.txt", "search": "repeat", "replace": "updated"},
        cwd=str(tmp_path),
    )

    assert locations == [{"path": str(target.resolve())}]


def test_tool_locations_ignore_non_file_or_navigation_tools(tmp_path):
    assert _tool_locations("grep", {"path": "."}, cwd=str(tmp_path)) is None
    assert _tool_locations("ls", {"path": "."}, cwd=str(tmp_path)) is None
