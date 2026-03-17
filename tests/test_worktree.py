from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from artel_core.tools.builtins import WorktreeTool
from artel_core.worktree import (
    CreateCommand,
    FinishCommand,
    HelpCommand,
    ListCommand,
    RemoveCommand,
    WorktreeError,
    WorktreeInfo,
    format_worktree_list,
    parse_worktree_porcelain,
    parse_wt_command,
    resolve_remove_target,
    run_worktree_command,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "git command failed")
    return result.stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    _git(repo, "branch", "-M", "main")


def test_parse_wt_command_variants() -> None:
    assert parse_wt_command("") == CreateCommand()
    assert parse_wt_command("feature/demo") == CreateCommand(branch="feature/demo")
    assert parse_wt_command("list") == ListCommand()
    assert parse_wt_command("ls") == ListCommand()
    assert parse_wt_command("rm demo_a1b2c3") == RemoveCommand(target="demo_a1b2c3")
    assert parse_wt_command("finish demo_a1b2c3") == FinishCommand(target="demo_a1b2c3")
    assert parse_wt_command("merge demo_a1b2c3") == FinishCommand(target="demo_a1b2c3")
    assert parse_wt_command("help") == HelpCommand()


def test_parse_wt_command_rejects_invalid_remove_usage() -> None:
    with pytest.raises(WorktreeError, match="Usage: /wt rm <uniq_subpath>"):
        parse_wt_command("rm")


def test_parse_wt_command_rejects_invalid_finish_usage() -> None:
    with pytest.raises(WorktreeError, match="Usage: /wt finish <uniq_subpath>"):
        parse_wt_command("finish")


def test_parse_worktree_porcelain_parses_branch_and_detached_entries() -> None:
    parsed = parse_worktree_porcelain(
        "\n".join(
            [
                "worktree /tmp/repo",
                "HEAD abcdef1234567890",
                "branch refs/heads/main",
                "",
                "worktree /tmp/repo-feature",
                "HEAD fedcba0987654321",
                "detached",
                "",
            ]
        )
    )

    assert parsed == [
        WorktreeInfo(
            path=Path("/tmp/repo").resolve(),
            head="abcdef1234567890",
            branch="main",
        ),
        WorktreeInfo(
            path=Path("/tmp/repo-feature").resolve(),
            head="fedcba0987654321",
            branch=None,
            detached=True,
        ),
    ]


def test_resolve_remove_target_matches_unique_managed_subpath() -> None:
    primary = Path("/tmp/repo")
    managed_root = Path("/tmp/managed/repo")
    managed_path = managed_root / "feature_demo_a1b2c3"
    resolved = resolve_remove_target(
        "a1b2c3",
        worktrees=[
            WorktreeInfo(path=primary, head="abc", branch="main"),
            WorktreeInfo(path=managed_path, head="def", branch="feature/demo"),
        ],
        managed_root=managed_root,
        primary_worktree=primary,
    )

    assert resolved == managed_path.resolve()


def test_resolve_remove_target_rejects_ambiguous_fragment() -> None:
    primary = Path("/tmp/repo")
    managed_root = Path("/tmp/managed/repo")
    with pytest.raises(WorktreeError, match="Ambiguous worktree target"):
        resolve_remove_target(
            "feature",
            worktrees=[
                WorktreeInfo(path=primary, head="abc", branch="main"),
                WorktreeInfo(path=managed_root / "feature_one_a1b2c3", head="def"),
                WorktreeInfo(path=managed_root / "feature_two_d4e5f6", head="ghi"),
            ],
            managed_root=managed_root,
            primary_worktree=primary,
        )


def test_format_worktree_list_marks_primary_and_managed() -> None:
    primary = Path("/tmp/repo")
    managed_root = Path("/tmp/managed/repo")
    output = format_worktree_list(
        worktrees=[
            WorktreeInfo(path=primary, head="abcdef0", branch="main"),
            WorktreeInfo(
                path=managed_root / "feature_demo_a1b2c3",
                head="1234567",
                branch=None,
                detached=True,
            ),
        ],
        managed_root=managed_root,
        primary_worktree=primary,
    )

    assert "- main [primary]" in output
    assert "- detached [managed, detached]" in output


@pytest.mark.asyncio
async def test_worktree_tool_without_branch_creates_detached_worktree(
    monkeypatch, tmp_path: Path
) -> None:
    managed_root = tmp_path / "managed"
    repo = tmp_path / "repo"
    monkeypatch.setenv("ARTEL_EXT_WORKTREE_BASE_DIR", str(managed_root))
    _init_repo(repo)

    tool = WorktreeTool(str(repo))
    output = await tool.execute(action="create")

    assert "checkout: detached" in output
    assert "source: main" in output


@pytest.mark.asyncio
async def test_worktree_tool_branch_create_list_remove_finish(monkeypatch, tmp_path: Path) -> None:
    managed_root = tmp_path / "managed"
    repo = tmp_path / "repo"
    monkeypatch.setenv("ARTEL_EXT_WORKTREE_BASE_DIR", str(managed_root))
    _init_repo(repo)

    tool = WorktreeTool(str(repo))
    create_output = await tool.execute(action="create", branch="feature/demo")
    assert "checkout: branch feature/demo" in create_output

    list_output = await tool.execute(action="list")
    assert "feature/demo" in list_output

    from artel_core.worktree import WorktreeManager

    manager = WorktreeManager(str(repo))
    source_worktree = next(
        worktree
        for worktree in manager.list_worktrees()
        if worktree.path != manager.primary_worktree
    )
    (source_worktree.path / "feature.txt").write_text("done\n", encoding="utf-8")
    _git(source_worktree.path, "add", "feature.txt")
    _git(source_worktree.path, "commit", "-m", "feature work")
    unique_subpath = source_worktree.path.name.rsplit("_", maxsplit=1)[-1]

    finish_output = await tool.execute(action="finish", target=unique_subpath)
    assert "source_branch: feature/demo" in finish_output
    assert "target_branch: main" in finish_output
    assert (repo / "feature.txt").read_text(encoding="utf-8") == "done\n"

    remove_output = await tool.execute(action="remove", target=unique_subpath)
    assert remove_output == f"Removed worktree: {source_worktree.path}"


def test_run_worktree_command_help_renders_usage(tmp_path: Path) -> None:
    output = run_worktree_command(str(tmp_path), "help")
    assert "Usage:" in output
    assert "/wt [branch]" in output
