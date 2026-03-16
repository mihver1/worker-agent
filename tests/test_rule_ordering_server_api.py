from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_rule_move_rest_endpoint(tmp_path, monkeypatch):
    from worker_core import config as cfg_mod
    from worker_core.config import WorkerConfig
    from worker_core.rules import add_rule
    from worker_server.server import ServerState, _create_rest_app

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    project_dir = tmp_path / "project"
    (project_dir / ".artel").mkdir(parents=True)

    add_rule(scope="project", text="First", project_dir=str(project_dir))
    second = add_rule(scope="project", text="Second", project_dir=str(project_dir))

    state = ServerState(config=WorkerConfig(), default_project_dir=str(project_dir))
    app = _create_rest_app(state, "test_token")
    headers = {"Authorization": "Bearer test_token"}

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            f"/api/rules/{second.id}/move",
            headers=headers,
            json={"position": 1},
        )
        assert resp.status == 200
        payload = await resp.json()
        assert payload["rule"]["id"] == second.id
        assert payload["rule"]["order"] == 1
