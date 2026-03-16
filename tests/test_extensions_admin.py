"""Tests for shared extension admin helpers."""

from __future__ import annotations

from unittest.mock import Mock


def test_list_installed_extensions_reads_discovery_and_manifest(monkeypatch):
    from worker_core.extensions_admin import list_installed_extensions

    class _Ext:
        version = "1.2.3"

    monkeypatch.setattr(
        "worker_core.extensions_admin.discover_extensions",
        lambda: {"worker-ext-demo": _Ext},
    )
    monkeypatch.setattr(
        "worker_core.extensions_admin.ext_manifest.list_entries",
        lambda: [
            type(
                "Entry",
                (),
                {"name": "worker-ext-demo", "source": "git+https://example.com/demo.git"},
            )()
        ],
    )

    result = list_installed_extensions()
    by_name = {item.name: item for item in result}

    assert by_name["worker-ext-demo"].version == "1.2.3"
    assert by_name["worker-ext-demo"].source == "git+https://example.com/demo.git"
    assert by_name["artel-lsp"].source == "bundled"
    assert by_name["artel-mcp"].source == "bundled"


def test_install_extension_uses_uv_and_updates_manifest(monkeypatch):
    from worker_core.extensions_admin import install_extension

    added: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "worker_core.extensions_admin._resolve_install_source",
        lambda source: source,
        raising=False,
    )
    monkeypatch.setattr(
        "worker_core.cli._resolve_install_source",
        lambda source: source,
    )
    monkeypatch.setattr(
        "worker_core.cli._parse_installed_package_name",
        lambda stdout, source: "worker-ext-demo",
    )
    monkeypatch.setattr(
        "worker_core.extensions_admin.ext_manifest.add",
        lambda name, source: added.append((name, source)),
    )
    monkeypatch.setattr(
        "subprocess.run",
        lambda *args, **kwargs: Mock(returncode=0, stdout="", stderr=""),
    )

    ok, message = install_extension("worker-ext-demo")

    assert ok is True
    assert "worker-ext-demo" in message
    assert added == [("worker-ext-demo", "worker-ext-demo")]
