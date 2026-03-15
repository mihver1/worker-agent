from __future__ import annotations

from unittest.mock import Mock

from worker_core.git_surface import render_git_diff, render_git_status, restore_all, restore_path


def test_render_git_status_groups_entries(monkeypatch, tmp_path):
    def fake_run(args, cwd, capture_output, text):
        assert args == ["git", "status", "--short", "--branch"]
        return Mock(returncode=0, stdout="## main\n M app.py\nA  new.py\n?? notes.txt\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    output = render_git_status(cwd=str(tmp_path))

    assert "Git status" in output
    assert "Modified (1):" in output
    assert "Added (1):" in output
    assert "Untracked (1):" in output


def test_render_git_diff_wraps_in_diff_fence(monkeypatch, tmp_path):
    def fake_run(args, cwd, capture_output, text):
        assert args == ["git", "diff"]
        return Mock(returncode=0, stdout="diff --git a/app.py b/app.py\n+print(1)\n", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    output = render_git_diff(cwd=str(tmp_path))

    assert output.startswith("Git diff: working tree")
    assert "```diff" in output
    assert "+print(1)" in output


def test_restore_path_requires_path(tmp_path):
    assert restore_path(cwd=str(tmp_path), pathspec="") == "Usage: /rollback <path>"


def test_restore_all_reports_success(monkeypatch, tmp_path):
    def fake_run(args, cwd, capture_output, text):
        assert args == ["git", "restore", "."]
        return Mock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    assert restore_all(cwd=str(tmp_path)) == "Restored all unstaged changes."
