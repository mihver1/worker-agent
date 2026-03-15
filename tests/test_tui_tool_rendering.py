from __future__ import annotations


def test_rendered_write_tool_result_uses_diff_markdown():
    from worker_core.tool_display import build_file_diff_display, format_tool_result_display

    display = format_tool_result_display(
        tool_name="write",
        content="Created 1 lines to /tmp/demo.py",
        is_error=False,
        display=build_file_diff_display(tool_name="write", path="demo.py", before="", after="print(1)\n"),
    )

    assert display.title == "demo.py"
    assert display.kind == "file_diff"
    assert display.status_badge == "+1  -0"
    assert display.markdown is False
    assert "+print(1)" in display.body


def test_tool_card_set_result_accepts_status_variant():
    from worker_tui.app import ToolCard

    card = ToolCard("⚙ write demo.py")

    card.set_result(
        title="demo.py",
        body="@@\n+print(1)",
        kind="file_diff",
        status_badge="+1  -0",
        status_variant="success",
    )

    assert card._result_title == "demo.py"
    assert card._result_kind == "file_diff"
    assert card._result_status_badge == "+1  -0"
    assert card._result_status_variant == "success"
