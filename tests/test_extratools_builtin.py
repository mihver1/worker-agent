from __future__ import annotations

import os
import stat
import textwrap
from pathlib import Path

import pytest
from artel_core.tools.builtins import create_builtin_tools
from artel_core.tools.extra_search import AgTool, GlobTool, RipgrepTool, create_extra_tools

_FAKE_SEARCH_SCRIPT = """#!/usr/bin/env python3
from __future__ import annotations

import fnmatch
from pathlib import Path
import sys


def parse_args(argv: list[str]) -> tuple[str, Path, int, str | None]:
    max_count = 1000
    glob_pattern = None
    positional: list[str] = []
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            positional.extend(argv[i + 1 :])
            break
        if arg in {"--nocolor", "--nogroup", "--line-number", "--no-heading", "--color=never"}:
            i += 1
            continue
        if arg == "-m":
            max_count = int(argv[i + 1])
            i += 2
            continue
        if arg.startswith("--max-count="):
            max_count = int(arg.split("=", 1)[1])
            i += 1
            continue
        if arg == "--max-count":
            max_count = int(argv[i + 1])
            i += 2
            continue
        if arg.startswith("--glob="):
            glob_pattern = arg.split("=", 1)[1]
            i += 1
            continue
        if arg == "--glob":
            glob_pattern = argv[i + 1]
            i += 2
            continue
        positional.append(arg)
        i += 1
    if len(positional) != 2:
        raise SystemExit(2)
    pattern, search_path = positional
    return pattern, Path(search_path), max_count, glob_pattern


def iter_files(search_path: Path):
    if search_path.is_file():
        yield search_path
        return
    for candidate in sorted(search_path.rglob("*")):
        if candidate.is_file():
            yield candidate


def main() -> int:
    pattern, search_path, max_count, glob_pattern = parse_args(sys.argv)
    matched = 0
    root = search_path if search_path.is_dir() else search_path.parent
    for file_path in iter_files(search_path):
        relative = str(file_path.relative_to(root))
        if glob_pattern and not (
            fnmatch.fnmatch(file_path.name, glob_pattern) or fnmatch.fnmatch(relative, glob_pattern)
        ):
            continue
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines, start=1):
            if pattern in line:
                print(f"{file_path}:{line_number}:{line}")
                matched += 1
                if matched >= max_count:
                    return 0
    return 1 if matched == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
"""


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _install_fake_binary(bin_dir: Path, name: str) -> None:
    binary = bin_dir / name
    binary.write_text(textwrap.dedent(_FAKE_SEARCH_SCRIPT), encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)


def test_create_extra_tools_only_registers_available_binaries(monkeypatch, tmp_path: Path) -> None:
    paths = {"ag": "/fake/ag", "rg": None}
    monkeypatch.setattr(
        "artel_core.tools.extra_search.shutil.which",
        lambda name: paths.get(name),
    )

    tools = create_extra_tools(str(tmp_path))

    assert [tool.name for tool in tools] == ["ag", "glob"]


def test_ripgrep_build_command_supports_glob_pattern(tmp_path: Path) -> None:
    tool = RipgrepTool(str(tmp_path), executable="/fake/rg")

    command = tool._build_command(
        pattern="needle",
        search_path="src",
        max_results=25,
        kwargs={"glob_pattern": "*.py"},
    )

    assert command == [
        "/fake/rg",
        "--line-number",
        "--no-heading",
        "--color=never",
        "--max-count=25",
        "--glob=*.py",
        "--",
        "needle",
        "src",
    ]


def test_ag_build_command_uses_expected_flags(tmp_path: Path) -> None:
    tool = AgTool(str(tmp_path), executable="/fake/ag")

    command = tool._build_command(
        pattern="needle",
        search_path="src",
        max_results=10,
        kwargs={},
    )

    assert command == [
        "/fake/ag",
        "--nocolor",
        "--nogroup",
        "-m",
        "10",
        "--",
        "needle",
        "src",
    ]


@pytest.mark.asyncio
async def test_glob_tool_filters_hidden_paths_and_limits_results(tmp_path: Path) -> None:
    _write(tmp_path / "src" / "alpha.py", "print('alpha')\n")
    _write(tmp_path / "src" / "beta.py", "print('beta')\n")
    _write(tmp_path / ".hidden" / "secret.py", "print('secret')\n")
    _write(tmp_path / ".git" / "config", "[core]\n")

    tool = GlobTool(str(tmp_path))
    output = await tool.execute(pattern="**/*.py", max_results=1)

    assert "src/alpha.py" in output
    assert ".hidden/secret.py" not in output
    assert "... (2 total, showing 1)" in output


@pytest.mark.asyncio
async def test_builtin_tools_register_available_extra_tools_and_execute_them(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _write(repo / "src" / "alpha.py", "needle in python\n")
    _write(repo / "src" / "beta.txt", "needle in text\n")
    _write(repo / "docs" / "guide.md", "no hit here\n")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install_fake_binary(bin_dir, "ag")
    _install_fake_binary(bin_dir, "rg")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    tools = {tool.name: tool for tool in create_builtin_tools(str(repo))}

    assert "ag" in tools
    assert "ripgrep" in tools
    assert "glob" in tools

    ag_output = await tools["ag"].execute(pattern="needle", path="src")
    rg_output = await tools["ripgrep"].execute(
        pattern="needle",
        path="src",
        glob_pattern="*.py",
    )
    glob_output = await tools["glob"].execute(pattern="**/*.py")

    assert "src/alpha.py:1:needle in python" in ag_output
    assert "src/alpha.py:1:needle in python" in rg_output
    assert "beta.txt" not in rg_output
    assert "src/alpha.py" in glob_output


@pytest.mark.asyncio
async def test_builtin_tools_skip_missing_search_binaries(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _install_fake_binary(bin_dir, "rg")
    monkeypatch.setenv("PATH", str(bin_dir))

    tools = create_builtin_tools(str(repo))
    names = [tool.name for tool in tools]

    assert "ripgrep" in names
    assert "glob" in names
    assert "ag" not in names
