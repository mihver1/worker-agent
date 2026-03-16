"""Shared test fixtures."""

from __future__ import annotations

import importlib
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SRC_PATHS = [
    REPO_ROOT / "packages/worker-ai/src",
    REPO_ROOT / "packages/worker-core/src",
    REPO_ROOT / "packages/worker-server/src",
    REPO_ROOT / "packages/worker-tui/src",
    REPO_ROOT / "packages/worker-web/src",
]
for package_path in reversed(PACKAGE_SRC_PATHS):
    package_str = str(package_path)
    if package_str not in sys.path:
        sys.path.insert(0, package_str)
_worker_ai_models = importlib.import_module("worker_ai.models")
Done = _worker_ai_models.Done
Message = _worker_ai_models.Message
ModelInfo = _worker_ai_models.ModelInfo
ReasoningDelta = _worker_ai_models.ReasoningDelta
StreamEvent = _worker_ai_models.StreamEvent
TextDelta = _worker_ai_models.TextDelta
ToolDef = _worker_ai_models.ToolDef
Usage = _worker_ai_models.Usage
Provider = importlib.import_module("worker_ai.provider").Provider


class MockProvider(Provider):
    """A controllable mock LLM provider for tests."""

    name = "mock"

    def __init__(self, responses: list[list[StreamEvent]] | None = None):
        self._responses = responses or []
        self._call_index = 0
        self.calls: list[dict[str, Any]] = []

    async def stream_chat(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        thinking_level: str = "off",
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append({"model": model, "messages": messages, "tools": tools})
        if self._call_index < len(self._responses):
            events = self._responses[self._call_index]
            self._call_index += 1
            for event in events:
                yield event
        else:
            yield TextDelta(content="mock response")
            yield Done(usage=Usage(input_tokens=10, output_tokens=5))

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="mock-model", provider="mock", name="Mock Model")]

    async def close(self) -> None:
        pass


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
def tmp_workdir(tmp_path):
    """Create a temporary working directory with some test files."""
    (tmp_path / "hello.txt").write_text("Hello, World!\nLine 2\nLine 3\n")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.py").write_text("print('nested')\n")
    return str(tmp_path)


@pytest.fixture
def repo_src_path():
    yield PACKAGE_SRC_PATHS
