from __future__ import annotations

import pytest


def test_write_diff_result_keeps_single_tool_lifecycle_card():
    from artel_core.tool_display import (
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
    from artel_tui.app import _resolve_diff_lexer

    lexer = _resolve_diff_lexer("demo.py", "@@ -0,0 +1 @@\n+print(1)\n")

    assert "python" in getattr(lexer, "aliases", ())


@pytest.mark.asyncio
async def test_diff_widget_renders_syntax_highlighted_body():
    from artel_tui.app import DiffWidget
    from rich.table import Table
    from textual.app import App, ComposeResult
    from textual.visual import RichVisual
    from textual.widgets import Static

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DiffWidget("demo.py", "+1  -0", "@@ -0,0 +1 @@\n+print(1)\n")

    app = TestApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.query_one(".tool-diff-body", Static)

        assert isinstance(body.visual, RichVisual)
        assert isinstance(body.visual._renderable, Table)


@pytest.mark.asyncio
async def test_tool_card_file_diff_renders_inside_scrollable_container():
    from artel_tui.app import ToolCard
    from textual.app import App, ComposeResult

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield ToolCard(
                "⚙ write demo.py",
                result_title="demo.py",
                result_body="@@ -0,0 +1 @@\n+print(1)\n",
                result_kind="file_diff",
                result_status_badge="+1  -0",
                result_status_variant="success",
            )

    app = TestApp()

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        card = app.query_one(ToolCard)

        assert card.query_one(".tool-card-scroll") is not None
        assert len(list(card.query(".tool-message-title").results())) == 1
        assert len(list(card.query(".tool-diff-stats").results())) == 0


def test_highlight_diff_code_text_does_not_force_black_background():
    from artel_tui.app import _highlight_diff_code_text, _resolve_diff_lexer

    lexer = _resolve_diff_lexer("demo.py", "+def foo():\n")
    text = _highlight_diff_code_text("def foo():", lexer)

    assert text.spans
    assert text.plain == "def foo():"
    assert all(span.style.bgcolor is None for span in text.spans if span.style is not None)
