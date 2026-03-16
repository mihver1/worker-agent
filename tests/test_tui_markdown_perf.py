from __future__ import annotations

import inspect


class _WorkerStub:
    def __init__(self):
        self.calls: list[object] = []

    def __call__(self, work, **kwargs):
        self.calls.append(work)
        if inspect.iscoroutine(work):
            work.close()
        return None


def test_message_widget_uses_stream_writer_when_available():
    from worker_tui.app import MessageWidget

    calls: list[tuple[str, str]] = []
    worker = _WorkerStub()

    class _StreamStub:
        async def write(self, delta: str) -> None:
            calls.append(("write", delta))

    class _MarkdownStub:
        def append(self, delta: str) -> None:
            calls.append(("append", delta))

    widget = MessageWidget("hello", role="assistant")
    widget._markdown = _MarkdownStub()  # type: ignore[assignment]
    widget._markdown_stream = _StreamStub()  # type: ignore[assignment]
    widget.run_worker = worker  # type: ignore[assignment]

    widget.append_content(" world")

    assert widget.content == "hello world"
    assert calls == []
    assert len(worker.calls) == 1


def test_message_widget_falls_back_to_markdown_append_without_stream():
    from worker_tui.app import MessageWidget

    calls: list[tuple[str, str]] = []

    class _MarkdownStub:
        def append(self, delta: str) -> None:
            calls.append(("append", delta))

        def update(self, content: str) -> None:
            calls.append(("update", content))

    widget = MessageWidget("hello", role="assistant")
    widget._markdown = _MarkdownStub()  # type: ignore[assignment]

    widget.append_content(" world")

    assert widget.content == "hello world"
    assert calls == [("append", " world")]


def test_message_widget_non_markdown_roles_refresh_layout_on_append(monkeypatch):
    from worker_tui.app import MessageWidget

    refresh_calls: list[tuple[bool, bool, bool]] = []

    widget = MessageWidget("hello", role="tool")

    def _refresh(*, repaint: bool = True, layout: bool = False, recompose: bool = False) -> None:
        refresh_calls.append((repaint, layout, recompose))

    monkeypatch.setattr(widget, "refresh", _refresh)

    widget.append_content(" world")

    assert widget.content == "hello world"
    assert refresh_calls == [(True, True, False)]
