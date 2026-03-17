"""Tests for grep, find, and ls tools."""

from __future__ import annotations

import pytest
from artel_core.tools.find import FindTool
from artel_core.tools.grep import GrepTool
from artel_core.tools.ls import LsTool

# ── GrepTool ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grep_finds_pattern(tmp_workdir):
    tool = GrepTool(tmp_workdir)
    result = await tool.execute(pattern="Hello", path=".")
    assert "Hello" in result
    assert "hello.txt" in result


@pytest.mark.asyncio
async def test_grep_no_matches(tmp_workdir):
    tool = GrepTool(tmp_workdir)
    result = await tool.execute(pattern="zzz_nonexistent_zzz", path=".")
    assert "No matches" in result


@pytest.mark.asyncio
async def test_grep_missing_path(tmp_workdir):
    tool = GrepTool(tmp_workdir)
    result = await tool.execute(pattern="foo", path="no_such_dir")
    assert "Error" in result


@pytest.mark.asyncio
async def test_grep_with_include(tmp_workdir):
    tool = GrepTool(tmp_workdir)
    result = await tool.execute(pattern="print", include="*.py")
    assert "nested.py" in result


@pytest.mark.asyncio
async def test_grep_definition():
    tool = GrepTool()
    defn = tool.definition()
    assert defn.name == "grep"
    assert len(defn.parameters) == 4


# ── FindTool ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_all_files(tmp_workdir):
    tool = FindTool(tmp_workdir)
    result = await tool.execute(path=".")
    assert "hello.txt" in result
    assert "nested.py" in result


@pytest.mark.asyncio
async def test_find_by_pattern(tmp_workdir):
    tool = FindTool(tmp_workdir)
    result = await tool.execute(pattern="*.py" if not tool._use_fd else r"\.py$", path=".")
    assert "nested.py" in result
    # hello.txt should not appear when filtering for .py
    assert "hello.txt" not in result


@pytest.mark.asyncio
async def test_find_missing_path(tmp_workdir):
    tool = FindTool(tmp_workdir)
    result = await tool.execute(pattern="foo", path="no_such_dir")
    assert "Error" in result


@pytest.mark.asyncio
async def test_find_no_results(tmp_workdir):
    tool = FindTool(tmp_workdir)
    result = await tool.execute(pattern="zzz_nonexistent_zzz")
    assert "No files found" in result


@pytest.mark.asyncio
async def test_find_definition():
    tool = FindTool()
    defn = tool.definition()
    assert defn.name == "find"
    assert len(defn.parameters) == 4


# ── LsTool ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ls_basic(tmp_workdir):
    tool = LsTool(tmp_workdir)
    result = await tool.execute(path=".")
    assert "hello.txt" in result
    assert "subdir/" in result


@pytest.mark.asyncio
async def test_ls_recursive(tmp_workdir):
    tool = LsTool(tmp_workdir)
    result = await tool.execute(path=".", max_depth=2)
    assert "nested.py" in result


@pytest.mark.asyncio
async def test_ls_single_file(tmp_workdir):
    tool = LsTool(tmp_workdir)
    result = await tool.execute(path="hello.txt")
    assert "hello.txt" in result


@pytest.mark.asyncio
async def test_ls_missing_path(tmp_workdir):
    tool = LsTool(tmp_workdir)
    result = await tool.execute(path="no_such_dir")
    assert "Error" in result


@pytest.mark.asyncio
async def test_ls_empty_dir(tmp_workdir):
    import os

    os.makedirs(os.path.join(tmp_workdir, "empty_dir"), exist_ok=True)
    tool = LsTool(tmp_workdir)
    result = await tool.execute(path="empty_dir")
    assert "empty" in result.lower()


@pytest.mark.asyncio
async def test_ls_definition():
    tool = LsTool()
    defn = tool.definition()
    assert defn.name == "ls"
    assert len(defn.parameters) == 3
