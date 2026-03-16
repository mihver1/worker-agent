from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_rules_rest_crud(tmp_path, monkeypatch):
    from worker_core import config as cfg_mod
    from worker_core.config import WorkerConfig
    from worker_server.server import ServerState, _create_rest_app

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    project_dir = tmp_path / "project"
    (project_dir / ".artel").mkdir(parents=True)

    state = ServerState(config=WorkerConfig(), default_project_dir=str(project_dir))
    app = _create_rest_app(state, "test_token")
    headers = {"Authorization": "Bearer test_token"}

    async with TestClient(TestServer(app)) as client:
        add_resp = await client.post(
            "/api/rules",
            headers=headers,
            json={"scope": "project", "text": "Do not use bash.", "enabled": True},
        )
        assert add_resp.status == 200
        added = await add_resp.json()
        rule_id = added["rule"]["id"]
        assert added["rule"]["text"] == "Do not use bash."

        list_resp = await client.get("/api/rules", headers=headers)
        assert list_resp.status == 200
        listed = await list_resp.json()
        assert any(rule["id"] == rule_id for rule in listed["rules"])

        edit_resp = await client.put(
            f"/api/rules/{rule_id}",
            headers=headers,
            json={"text": "Do not use shell.", "enabled": False},
        )
        assert edit_resp.status == 200
        edited = await edit_resp.json()
        assert edited["rule"]["text"] == "Do not use shell."
        assert edited["rule"]["enabled"] is False

        delete_resp = await client.delete(f"/api/rules/{rule_id}", headers=headers)
        assert delete_resp.status == 200
        deleted = await delete_resp.json()
        assert deleted["rule"]["id"] == rule_id


@pytest.mark.asyncio
async def test_rules_rest_delete_missing_returns_404(tmp_path, monkeypatch):
    from worker_core import config as cfg_mod
    from worker_core.config import WorkerConfig
    from worker_server.server import ServerState, _create_rest_app

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    project_dir = tmp_path / "project"
    (project_dir / ".artel").mkdir(parents=True)

    state = ServerState(config=WorkerConfig(), default_project_dir=str(project_dir))
    app = _create_rest_app(state, "test_token")
    headers = {"Authorization": "Bearer test_token"}

    async with TestClient(TestServer(app)) as client:
        resp = await client.delete("/api/rules/missing-rule", headers=headers)
        assert resp.status == 404
        payload = await resp.json()
        assert "not found" in payload["error"].lower()
