"""Tests for extension manifest persistence and CLI package-name parsing."""

from __future__ import annotations

import json

import pytest
from artel_core import ext_manifest
from artel_core.cli import _parse_installed_package_name

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_manifest(tmp_path, monkeypatch):
    """Redirect the manifest to a temp directory for every test."""
    manifest = tmp_path / "extensions.lock"
    monkeypatch.setattr(ext_manifest, "MANIFEST_PATH", manifest)
    yield manifest


# ── ext_manifest: basic CRUD ─────────────────────────────────────


def test_list_empty_when_no_file():
    assert ext_manifest.list_entries() == []


def test_add_and_list():
    ext_manifest.add("foo", "git+https://example.com/foo.git")
    entries = ext_manifest.list_entries()
    assert len(entries) == 1
    assert entries[0].name == "foo"
    assert entries[0].source == "git+https://example.com/foo.git"


def test_add_overwrites_same_name():
    ext_manifest.add("foo", "old-source")
    ext_manifest.add("foo", "new-source")
    entries = ext_manifest.list_entries()
    assert len(entries) == 1
    assert entries[0].source == "new-source"


def test_add_multiple():
    ext_manifest.add("a", "src-a")
    ext_manifest.add("b", "src-b")
    ext_manifest.add("c", "src-c")
    names = [e.name for e in ext_manifest.list_entries()]
    assert names == ["a", "b", "c"]


def test_remove_existing():
    ext_manifest.add("a", "src-a")
    ext_manifest.add("b", "src-b")
    assert ext_manifest.remove("a") is True
    names = [e.name for e in ext_manifest.list_entries()]
    assert names == ["b"]


def test_remove_nonexistent():
    ext_manifest.add("a", "src-a")
    assert ext_manifest.remove("z") is False
    assert len(ext_manifest.list_entries()) == 1


def test_remove_from_empty():
    assert ext_manifest.remove("z") is False


# ── ext_manifest: resilience ─────────────────────────────────────


def test_corrupt_json_returns_empty(_isolated_manifest):
    _isolated_manifest.write_text("not json {{{", encoding="utf-8")
    assert ext_manifest.list_entries() == []


def test_wrong_json_type_returns_empty(_isolated_manifest):
    _isolated_manifest.write_text('"string"', encoding="utf-8")
    assert ext_manifest.list_entries() == []


def test_entries_with_missing_keys_skipped(_isolated_manifest):
    data = [{"name": "good", "source": "s"}, {"name": "bad"}, {"other": 1}]
    _isolated_manifest.write_text(json.dumps(data), encoding="utf-8")
    entries = ext_manifest.list_entries()
    assert len(entries) == 1
    assert entries[0].name == "good"


# ── ext_manifest: file on disk ───────────────────────────────────


def test_manifest_persists_to_disk(_isolated_manifest):
    ext_manifest.add("x", "source-x")
    raw = json.loads(_isolated_manifest.read_text(encoding="utf-8"))
    assert len(raw) == 1
    assert raw[0]["name"] == "x"


# ── _parse_installed_package_name ────────────────────────────────


class TestParseInstalledPackageName:
    def test_uv_output_with_plus_line(self):
        stdout = "Resolved 3 packages\n + artel-ext-foo==0.2.1\n + dep==1.0\n"
        assert _parse_installed_package_name(stdout, "whatever") == "artel-ext-foo"

    def test_fallback_git_https(self):
        assert (
            _parse_installed_package_name("", "git+https://github.com/user/artel-ext-bar.git")
            == "artel-ext-bar"
        )

    def test_fallback_git_https_with_branch(self):
        assert (
            _parse_installed_package_name("", "git+https://github.com/user/artel-ext-bar.git@main")
            == "artel-ext-bar"
        )

    def test_fallback_git_ssh(self):
        assert (
            _parse_installed_package_name("", "git+ssh://git@github.com/org/my-ext.git") == "my-ext"
        )

    def test_fallback_simple_name(self):
        assert _parse_installed_package_name("", "artel-ext-hello") == "artel-ext-hello"

    def test_fallback_local_path(self):
        assert _parse_installed_package_name("", "/home/user/projects/my-ext") == "my-ext"

    def test_fallback_relative_path(self):
        assert _parse_installed_package_name("", "./extensions/my-ext") == "my-ext"

    def test_fallback_scp_style(self):
        assert _parse_installed_package_name("", "git@github.com:org/my-ext.git") == "my-ext"

    def test_fallback_scp_style_with_branch(self):
        assert _parse_installed_package_name("", "git@github.com:org/my-ext.git@main") == "my-ext"
