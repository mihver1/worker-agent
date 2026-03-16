from __future__ import annotations

import pytest
from textual.containers import VerticalScroll


def test_rendered_write_tool_result_uses_diff_markdown():
    from worker_core.tool_display import build_file_diff_display, format_tool_result_display

    display = format_tool_result_display(
        tool_name="write",
        content="Created 1 lines to /tmp/demo.py",
        is_error=False,
        display=build_file_diff_display(
            tool_name="write", path="demo.py", before="", after="print(1)\n"
        ),
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


@pytest.mark.asyncio
async def test_tool_card_composes_result_row_with_status_variant():
    from textual.app import App, ComposeResult
    from worker_tui.app import ToolCard

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield ToolCard(
                "⚙ write demo.py",
                result_title="demo.py",
                result_body="Created demo.py",
                result_status_badge="+1  -0",
                result_status_variant="success",
            )

    app = TestApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        card = app.query_one(ToolCard)

        assert card.query_one(".tool-message-result-row") is not None
        assert card.query_one(".tool-message-result-title") is not None
        assert card.query_one(".tool-message-badge-success") is not None


@pytest.mark.asyncio
async def test_tool_card_result_row_stays_compact_inside_collapsible():
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.widgets import Collapsible
    from worker_tui.app import ToolCard

    class TestApp(App[None]):
        CSS = """
        #chat-scroll {
            height: 1fr;
        }
        #chat-container {
            height: auto;
        }
        """

        def compose(self) -> ComposeResult:
            with VerticalScroll(id="chat-scroll"), Vertical(id="chat-container"):
                yield Collapsible(
                    ToolCard(
                        "⚙ ripgrep",
                        "pattern='undo/rewind', path='tests', "
                        "glob_pattern='*.py', max_results='80'",
                        result_title="✓ ripgrep",
                        result_status_badge="0 matches",
                    ),
                    title="⚙ ripgrep",
                    collapsed=False,
                )

    app = TestApp()

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        card = app.query_one(ToolCard)
        row = card.query_one(".tool-message-result-row")

        assert row.outer_size.height == 1
        assert card.outer_size.height <= 4


@pytest.mark.asyncio
async def test_tool_card_uses_scrollable_container_for_long_result_body():
    from textual.app import App, ComposeResult
    from worker_tui.app import ToolCard

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield ToolCard(
                "⚙ shell pwd",
                result_title="✓ shell",
                result_body="\n".join(f"line {index}" for index in range(40)),
            )

    app = TestApp()

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        card = app.query_one(ToolCard)
        scroll = card.query_one(".tool-card-scroll", VerticalScroll)

        assert scroll is not None


@pytest.mark.asyncio
async def test_tool_card_block_result_preserves_newlines_in_scrollable_body():
    from textual.app import App, ComposeResult
    from textual.widgets import Static
    from worker_tui.app import ToolCard

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield ToolCard(
                "⚙ read demo.py",
                result_title="✓ read demo.py",
                result_body="1|alpha\n2|beta\n3|gamma",
                result_kind="block",
            )

    app = TestApp()

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        body = app.query_one(".tool-message-body", Static)
        scroll = app.query_one(".tool-card-scroll", VerticalScroll)

        assert scroll is not None
        assert str(body.render()) == "1|alpha\n2|beta\n3|gamma"


@pytest.mark.asyncio
async def test_tool_card_body_treats_rich_text_repr_as_plain_text():
    from textual.app import App, ComposeResult
    from textual.widgets import Static
    from worker_tui.app import ToolCard, _highlight_diff_code_text, _resolve_diff_lexer

    lexer = _resolve_diff_lexer("demo.py", "+def foo():\n")
    highlighted_repr = repr(_highlight_diff_code_text("def foo():", lexer))

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield ToolCard(
                "⚙ read demo.py",
                result_title="✓ read demo.py",
                result_body=highlighted_repr,
            )

    app = TestApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.query_one(".tool-message-body", Static)

        assert body is not None
