"""Tests for built-in tools: read, write, edit, bash."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from worker_core.tools.builtins import (
    BashTool,
    EditTool,
    ReadTool,
    WriteTool,
    create_builtin_tools,
)


# ── ReadTool ──────────────────────────────────────────────────────


class TestReadTool:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_workdir):
        self.tool = ReadTool(tmp_workdir)
        self.workdir = tmp_workdir

    @pytest.mark.asyncio
    async def test_read_existing_file(self):
        result = await self.tool.execute(path="hello.txt")
        assert "1|Hello, World!" in result
        assert "2|Line 2" in result
        assert "3|Line 3" in result

    @pytest.mark.asyncio
    async def test_read_with_line_range(self):
        result = await self.tool.execute(path="hello.txt", start_line=2, end_line=2)
        assert "2|Line 2" in result
        assert "1|Hello" not in result
        assert "3|Line 3" not in result

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self):
        result = await self.tool.execute(path="missing.txt")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_read_nested_file(self):
        result = await self.tool.execute(path="subdir/nested.py")
        assert "print('nested')" in result

    @pytest.mark.asyncio
    async def test_read_absolute_path(self):
        abs_path = os.path.join(self.workdir, "hello.txt")
        result = await self.tool.execute(path=abs_path)
        assert "Hello, World!" in result

    def test_definition(self):
        defn = self.tool.definition()
        assert defn.name == "read"
        assert any(p.name == "path" for p in defn.parameters)
        assert any(p.name == "start_line" for p in defn.parameters)


# ── WriteTool ─────────────────────────────────────────────────────


class TestWriteTool:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_workdir):
        self.tool = WriteTool(tmp_workdir)
        self.workdir = tmp_workdir

    @pytest.mark.asyncio
    async def test_write_new_file(self):
        result = await self.tool.execute(path="new.txt", content="hello\nworld\n")
        assert "Wrote" in result
        path = Path(self.workdir) / "new.txt"
        assert path.read_text() == "hello\nworld\n"

    @pytest.mark.asyncio
    async def test_write_overwrite(self):
        result = await self.tool.execute(path="hello.txt", content="overwritten")
        assert "Wrote" in result
        path = Path(self.workdir) / "hello.txt"
        assert path.read_text() == "overwritten"

    @pytest.mark.asyncio
    async def test_write_creates_dirs(self):
        result = await self.tool.execute(path="a/b/c.txt", content="deep")
        assert "Wrote" in result
        assert (Path(self.workdir) / "a" / "b" / "c.txt").read_text() == "deep"


# ── EditTool ──────────────────────────────────────────────────────


class TestEditTool:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_workdir):
        self.tool = EditTool(tmp_workdir)
        self.workdir = tmp_workdir

    @pytest.mark.asyncio
    async def test_edit_replace(self):
        result = await self.tool.execute(
            path="hello.txt", search="Line 2", replace="Modified Line"
        )
        assert "Applied edit" in result
        content = (Path(self.workdir) / "hello.txt").read_text()
        assert "Modified Line" in content
        assert "Line 2" not in content

    @pytest.mark.asyncio
    async def test_edit_not_found(self):
        result = await self.tool.execute(
            path="hello.txt", search="NONEXISTENT", replace="foo"
        )
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_edit_nonexistent_file(self):
        result = await self.tool.execute(
            path="missing.txt", search="x", replace="y"
        )
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_edit_ambiguous(self):
        """File with duplicate content should fail."""
        (Path(self.workdir) / "dups.txt").write_text("aaa\naaa\n")
        result = await self.tool.execute(path="dups.txt", search="aaa", replace="bbb")
        assert "found 2 times" in result


# ── BashTool ──────────────────────────────────────────────────────


class TestBashTool:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_workdir):
        self.tool = BashTool(tmp_workdir, timeout=10.0)
        self.workdir = tmp_workdir

    @pytest.mark.asyncio
    async def test_echo(self):
        result = await self.tool.execute(command="echo hello")
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_exit_code(self):
        result = await self.tool.execute(command="exit 42")
        assert "Exit code: 42" in result

    @pytest.mark.asyncio
    async def test_stderr(self):
        result = await self.tool.execute(command="echo err >&2")
        assert "STDERR" in result
        assert "err" in result

    @pytest.mark.asyncio
    async def test_timeout(self):
        tool = BashTool(self.workdir, timeout=0.5)
        result = await tool.execute(command="sleep 10", timeout=0.5)
        assert "timed out" in result

    @pytest.mark.asyncio
    async def test_working_dir(self):
        result = await self.tool.execute(command="pwd")
        assert self.workdir in result


# ── create_builtin_tools ──────────────────────────────────────────


def test_create_builtin_tools():
    tools = create_builtin_tools("/tmp")
    names = {t.name for t in tools}
    assert names == {"read", "write", "edit", "bash"}
