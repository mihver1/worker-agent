from __future__ import annotations

from artel_core.session_rewind import collect_last_ai_changed_paths


class _Role:
    def __init__(self, value: str):
        self.value = value


class _ToolResult:
    def __init__(self, display: dict[str, object] | None):
        self.display = display

    def model_dump(self):
        return {"display": self.display}


class _Message:
    def __init__(self, role: str, display: dict[str, object] | None = None):
        self.role = _Role(role)
        self.tool_result = _ToolResult(display) if display is not None else None


def test_collect_last_ai_changed_paths_stops_at_user_boundary():
    messages = [
        _Message("user"),
        _Message("tool", {"kind": "file_diff", "path": "old.py"}),
        _Message("user"),
        _Message("tool", {"kind": "file_diff", "path": "a.py"}),
        _Message("tool", {"kind": "file_diff", "path": "b.py"}),
    ]

    assert collect_last_ai_changed_paths(messages) == ["a.py", "b.py"]
