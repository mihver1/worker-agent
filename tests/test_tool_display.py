from __future__ import annotations

from artel_core.tool_display import (
    build_file_diff_display,
    format_tool_call_display,
    format_tool_result_display,
)


def test_format_tool_call_display_compacts_write_call():
    display = format_tool_call_display(
        "write",
        {"path": "src/app.py", "content": "print('hello')\nprint('world')\n"},
    )

    assert display.title == "⚙ write src/app.py"
    assert "content: 3 line(s)" in display.body
    assert "print('hello')" not in display.body


def test_build_file_diff_display_counts_added_removed_lines():
    payload = build_file_diff_display(
        tool_name="edit",
        path="src/app.py",
        before="a\nold\n",
        after="a\nnew\nmore\n",
    )

    assert payload["kind"] == "file_diff"
    assert payload["path"] == "src/app.py"
    assert payload["added_lines"] >= 2
    assert payload["removed_lines"] >= 1
    assert "@@" in payload["diff"]


def test_format_tool_result_display_renders_diff_markdown():
    payload = build_file_diff_display(
        tool_name="write",
        path="src/app.py",
        before="",
        after="print('hello')\n",
    )
    display = format_tool_result_display(
        tool_name="write",
        content="Created 1 lines to /tmp/src/app.py",
        is_error=False,
        display=payload,
    )

    assert display.markdown is False
    assert display.kind == "file_diff"
    assert display.title == "src/app.py"
    assert display.status_badge == "+1  -0"
    assert "+print('hello')" in display.body


def test_format_tool_call_display_compacts_lsp_call():
    display = format_tool_call_display(
        "lsp_definition",
        {"path": "src/app.py", "line": 12, "column": 5, "max_results": 8},
    )

    assert display.title == "⚙ lsp_definition src/app.py"
    assert "line=12" in display.body
    assert "column=5" in display.body
    assert "max_results=8" in display.body


def test_format_tool_result_display_preserves_multiline_read_output():
    display = format_tool_result_display(
        tool_name="read",
        content="1|first line\n2|second line\n3|third line",
        is_error=False,
    )

    assert display.kind == "block"
    assert display.markdown is False
    assert display.body.splitlines() == [
        "1|first line",
        "2|second line",
        "3|third line",
    ]


def test_format_tool_result_display_preserves_multiline_bash_output():
    display = format_tool_result_display(
        tool_name="bash",
        content="$ pwd\n/tmp/project\nSTDERR:\nwarning",
        is_error=False,
    )

    assert display.kind == "block"
    assert "$ pwd" in display.body
    assert "STDERR:" in display.body
    assert "\n/tmp/project\n" in display.body
