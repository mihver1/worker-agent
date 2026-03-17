"""Tests for managed local server registry paths during the Artel transition."""

from __future__ import annotations

import json

from artel_tui.local_server import managed_server_registry_path


class TestLocalServerPaths:
    def test_managed_server_registry_path_uses_artel_dir(self, tmp_path):
        assert managed_server_registry_path(str(tmp_path)) == tmp_path / ".artel" / "server.json"

    def test_load_registry_falls_back_to_legacy_artel_server_json(
        self,
        tmp_path,
        monkeypatch,
    ):
        import artel_tui.local_server as local_server_mod

        legacy_registry = tmp_path / ".artel" / "server.json"
        legacy_registry.parent.mkdir(parents=True)
        legacy_registry.write_text(
            json.dumps(
                {
                    "remote_url": "ws://127.0.0.1:7432",
                    "auth_token": "artel_token",
                    "pid": 12345,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            local_server_mod,
            "effective_project_server_registry_path",
            lambda _project_dir: legacy_registry,
        )

        handle = local_server_mod._load_registry(str(tmp_path))

        assert handle is not None
        assert handle.remote_url == "ws://127.0.0.1:7432"
        assert handle.auth_token == "artel_token"
        assert handle.pid == 12345
