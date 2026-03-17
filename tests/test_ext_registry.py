"""Tests for extension registry: fetch, search, cache, CLI commands."""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest
from artel_core import ext_registry
from artel_core.config import RegistryConfig

# ── Fixtures ──────────────────────────────────────────────────────

SAMPLE_ENTRIES = [
    {
        "name": "artel-ext-foo",
        "description": "Foo tools",
        "repo": "git+https://example.com/foo.git",
        "tags": ["tools", "demo"],
        "author": "alice",
    },
    {
        "name": "artel-ext-bar",
        "description": "Bar integration",
        "repo": "git+https://example.com/bar.git",
        "tags": ["integration"],
        "author": "bob",
    },
    {
        "name": "artel-ext-mcp",
        "description": "MCP client for tools and resources",
        "repo": "git+https://example.com/mcp.git",
        "tags": ["mcp", "tools"],
        "author": "alice",
    },
]


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(ext_registry, "CACHE_DIR", tmp_path / "cache")
    yield tmp_path / "cache"


def _mock_httpx_get(entries: list[dict], *, fmt: str = "json"):
    """Mock httpx.get returning *entries* as JSON bytes (default) or TOML bytes."""
    if fmt == "toml":
        import tomli_w

        body = tomli_w.dumps({"extensions": entries})
    else:
        body = json.dumps(entries)
    resp = Mock()
    resp.content = body.encode("utf-8")
    resp.raise_for_status = Mock()
    return patch("artel_core.ext_registry.httpx.get", return_value=resp)


# ── fetch_registry ────────────────────────────────────────────────


class TestFetchRegistry:
    def test_fetches_and_returns_list(self):
        with _mock_httpx_get(SAMPLE_ENTRIES):
            result = ext_registry.fetch_registry("https://r.example.com/ext.json", use_cache=False)
        assert len(result) == 3
        assert result[0]["name"] == "artel-ext-foo"

    def test_returns_empty_for_non_list(self):
        resp = Mock(content=json.dumps({"not": "a list"}).encode(), raise_for_status=Mock())
        with patch("artel_core.ext_registry.httpx.get", return_value=resp):
            result = ext_registry.fetch_registry("https://r.example.com/ext.json", use_cache=False)
        assert result == []

    def test_fetches_toml_format(self):
        with _mock_httpx_get(SAMPLE_ENTRIES, fmt="toml"):
            result = ext_registry.fetch_registry(
                "https://r.example.com/ext.toml",
                use_cache=False,
            )
        assert len(result) == 3
        assert result[0]["name"] == "artel-ext-foo"

    def test_writes_cache(self, _isolated_cache):
        with _mock_httpx_get(SAMPLE_ENTRIES):
            ext_registry.fetch_registry("https://r.example.com/ext.json", use_cache=True)
        cache_files = list(_isolated_cache.glob("*.json"))
        assert len(cache_files) == 1
        data = json.loads(cache_files[0].read_text())
        assert len(data["entries"]) == 3

    def test_reads_from_cache(self, _isolated_cache):
        # Pre-populate cache
        with _mock_httpx_get(SAMPLE_ENTRIES):
            ext_registry.fetch_registry("https://r.example.com/ext.json", use_cache=True)

        # Second call should NOT hit httpx
        with patch("artel_core.ext_registry.httpx.get") as mock_get:
            result = ext_registry.fetch_registry("https://r.example.com/ext.json", use_cache=True)
            mock_get.assert_not_called()
        assert len(result) == 3

    def test_expired_cache_refetches(self, _isolated_cache, monkeypatch):
        with _mock_httpx_get(SAMPLE_ENTRIES):
            ext_registry.fetch_registry("https://r.example.com/ext.json", use_cache=True)

        # Expire cache
        monkeypatch.setattr(ext_registry, "CACHE_TTL_SECONDS", 0)

        with _mock_httpx_get([SAMPLE_ENTRIES[0]]) as mock_get:
            result = ext_registry.fetch_registry("https://r.example.com/ext.json", use_cache=True)
            mock_get.assert_called_once()
        assert len(result) == 1


# ── search_all ────────────────────────────────────────────────────


class TestSearchAll:
    def _regs(self, *urls: str) -> list[RegistryConfig]:
        return [RegistryConfig(name=f"r{i}", url=u) for i, u in enumerate(urls)]

    def test_search_by_name(self):
        with _mock_httpx_get(SAMPLE_ENTRIES):
            results = ext_registry.search_all(
                self._regs("https://r.example.com/ext.json"),
                "foo",
                use_cache=False,
            )
        assert len(results) == 1
        assert results[0].name == "artel-ext-foo"

    def test_search_by_tag(self):
        with _mock_httpx_get(SAMPLE_ENTRIES):
            results = ext_registry.search_all(
                self._regs("https://r.example.com/ext.json"),
                "mcp",
                use_cache=False,
            )
        assert len(results) == 1
        assert results[0].name == "artel-ext-mcp"

    def test_search_by_description(self):
        with _mock_httpx_get(SAMPLE_ENTRIES):
            results = ext_registry.search_all(
                self._regs("https://r.example.com/ext.json"),
                "integration",
                use_cache=False,
            )
        assert len(results) == 1
        assert results[0].name == "artel-ext-bar"

    def test_search_across_multiple_registries(self):
        entries_a = [SAMPLE_ENTRIES[0]]
        entries_b = [SAMPLE_ENTRIES[1]]
        resp_a = Mock(
            content=json.dumps(entries_a).encode(),
            raise_for_status=Mock(),
        )
        resp_b = Mock(
            content=json.dumps(entries_b).encode(),
            raise_for_status=Mock(),
        )

        with patch("artel_core.ext_registry.httpx.get", side_effect=[resp_a, resp_b]):
            results = ext_registry.search_all(
                self._regs("https://a.example.com/ext.json", "https://b.example.com/ext.json"),
                "artel-ext",
                use_cache=False,
            )
        assert len(results) == 2
        names = {r.name for r in results}
        assert names == {"artel-ext-foo", "artel-ext-bar"}

    def test_search_no_matches(self):
        with _mock_httpx_get(SAMPLE_ENTRIES):
            results = ext_registry.search_all(
                self._regs("https://r.example.com/ext.json"),
                "nonexistent",
                use_cache=False,
            )
        assert results == []

    def test_search_skips_failing_registry(self):
        with patch("artel_core.ext_registry.httpx.get", side_effect=Exception("network")):
            results = ext_registry.search_all(
                self._regs("https://broken.example.com/ext.json"),
                "foo",
                use_cache=False,
            )
        assert results == []

    def test_registry_name_propagated(self):
        with _mock_httpx_get(SAMPLE_ENTRIES):
            results = ext_registry.search_all(
                [RegistryConfig(name="mycompany", url="https://r.example.com/ext.json")],
                "foo",
                use_cache=False,
            )
        assert results[0].registry_name == "mycompany"


# ── invalidate_cache ──────────────────────────────────────────────


def test_invalidate_cache(_isolated_cache):
    with _mock_httpx_get(SAMPLE_ENTRIES):
        ext_registry.fetch_registry("https://r.example.com/a.json", use_cache=True)
        ext_registry.fetch_registry("https://r.example.com/b.json", use_cache=True)
    assert len(list(_isolated_cache.glob("*.json"))) == 2
    ext_registry.invalidate_cache()
    assert len(list(_isolated_cache.glob("*.json"))) == 0


# ── CLI: ext registry list/add/remove ─────────────────────────────


class TestCliRegistryCommands:
    def test_registry_list(self, tmp_path, monkeypatch):
        from artel_core import cli as cli_mod
        from artel_core import config as config_mod
        from artel_core import extensions_admin as admin_mod
        from click.testing import CliRunner

        fake_config = tmp_path / "config.toml"
        if fake_config.exists():
            fake_config.unlink()
        monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(config_mod, "GLOBAL_CONFIG", fake_config)
        monkeypatch.setattr(config_mod, "LEGACY_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(config_mod, "LEGACY_GLOBAL_CONFIG", fake_config)
        monkeypatch.setattr(admin_mod, "GLOBAL_CONFIG", fake_config)

        runner = CliRunner()
        result = runner.invoke(cli_mod.cli, ["ext", "registry", "list"])
        assert result.exit_code == 0
        assert "official" in result.output

    def test_registry_add_and_remove(self, tmp_path, monkeypatch):
        import tomllib

        from artel_core import cli as cli_mod
        from artel_core import config as config_mod
        from artel_core import extensions_admin as admin_mod
        from click.testing import CliRunner

        fake_config = tmp_path / "config.toml"
        if fake_config.exists():
            fake_config.unlink()
        monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(config_mod, "GLOBAL_CONFIG", fake_config)
        monkeypatch.setattr(config_mod, "LEGACY_CONFIG_DIR", tmp_path)
        monkeypatch.setattr(config_mod, "LEGACY_GLOBAL_CONFIG", fake_config)
        monkeypatch.setattr(admin_mod, "GLOBAL_CONFIG", fake_config)

        runner = CliRunner()

        # Add
        result = runner.invoke(
            cli_mod.cli,
            ["ext", "registry", "add", "myco", "https://myco.example.com/ext.json"],
        )
        assert result.exit_code == 0
        assert "added" in result.output

        with open(fake_config, "rb") as f:
            data = tomllib.load(f)
        regs = data["extensions"]["registries"]
        assert any(r["name"] == "myco" for r in regs)

        # Duplicate
        result = runner.invoke(
            cli_mod.cli,
            ["ext", "registry", "add", "myco", "https://other.example.com/ext.json"],
        )
        assert "already exists" in result.output

        # Remove
        result = runner.invoke(cli_mod.cli, ["ext", "registry", "remove", "myco"])
        assert result.exit_code == 0
        assert "removed" in result.output

        with open(fake_config, "rb") as f:
            data = tomllib.load(f)
        regs = data["extensions"]["registries"]
        assert not any(r["name"] == "myco" for r in regs)

    def test_cannot_remove_official(self):
        from artel_core import cli as cli_mod
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli_mod.cli, ["ext", "registry", "remove", "official"])
        assert "Cannot remove" in result.output

    def test_remove_nonexistent(self):
        from artel_core import cli as cli_mod
        from click.testing import CliRunner

        runner = CliRunner()
        result = runner.invoke(cli_mod.cli, ["ext", "registry", "remove", "nosuch"])
        assert "not found" in result.output
