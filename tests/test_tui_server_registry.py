from __future__ import annotations


def test_server_registry_round_trip(monkeypatch, tmp_path):
    from artel_core import config as cfg_mod
    from artel_tui.server_registry import (
        SavedArtelServer,
        load_saved_servers,
        server_registry_path,
        upsert_saved_server,
    )

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)

    upsert_saved_server(
        SavedArtelServer(name="Prod", remote_url="ws://prod:7432", auth_token="tok")
    )
    upsert_saved_server(
        SavedArtelServer(
            name="Prod EU",
            remote_url="ws://prod-eu:7432",
            auth_token="tok2",
        )
    )

    assert server_registry_path() == fake_config / "servers.json"
    servers = load_saved_servers()
    assert [server.name for server in servers] == ["Prod", "Prod EU"]
    assert servers[0].auth_token == "tok"


def test_server_registry_upsert_replaces_same_url(monkeypatch, tmp_path):
    from artel_core import config as cfg_mod
    from artel_tui.server_registry import SavedArtelServer, load_saved_servers, upsert_saved_server

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)

    upsert_saved_server(
        SavedArtelServer(name="Prod", remote_url="ws://prod:7432", auth_token="old")
    )
    upsert_saved_server(
        SavedArtelServer(
            name="Production",
            remote_url="ws://prod:7432",
            auth_token="new",
        )
    )

    servers = load_saved_servers()
    assert len(servers) == 1
    assert servers[0].name == "Production"
    assert servers[0].auth_token == "new"
