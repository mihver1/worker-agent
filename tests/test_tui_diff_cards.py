from __future__ import annotations

import pytest


def test_write_diff_result_keeps_single_tool_lifecycle_card():
    from worker_core.tool_display import (
        build_file_diff_display,
        format_tool_call_display,
        format_tool_result_display,
    )

    call = format_tool_call_display("write", {"path": "demo.py", "content": "print(1)\n"})
    result = format_tool_result_display(
        tool_name="write",
        content="Created 1 lines to /tmp/demo.py",
        is_error=False,
        display=build_file_diff_display(
            tool_name="write",
            path="demo.py",
            before="",
            after="print(1)\n",
        ),
    )

    assert call.title == "⚙ write demo.py"
    assert result.title == "demo.py"
    assert result.kind == "file_diff"
    assert result.status_badge == "+1  -0"
    assert result.markdown is False


def test_diff_lexer_follows_file_extension():
    from worker_tui.app import _resolve_diff_lexer

    lexer = _resolve_diff_lexer("demo.py", "@@ -0,0 +1 @@\n+print(1)\n")

    assert "python" in getattr(lexer, "aliases", ())


@pytest.mark.asyncio
async def test_diff_widget_renders_syntax_highlighted_body():
    from rich.table import Table
    from textual.app import App, ComposeResult
    from textual.visual import RichVisual
    from textual.widgets import Static
    from worker_tui.app import DiffWidget

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffWidget("demo.py", "+1  -0", "@@ -0,0 +1 @@\n+print(1)\n")

    app = TestApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.query_one(".tool-diff-body", Static)

        assert isinstance(body.visual, RichVisual)
        assert isinstance(body.visual._renderable, Table)
