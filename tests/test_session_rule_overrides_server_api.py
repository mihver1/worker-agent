from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_session_rule_override_endpoints(tmp_path, monkeypatch):
    from artel_core import config as cfg_mod
    from artel_core.config import ArtelConfig
    from artel_server.server import ServerState, _create_rest_app

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    project_dir = tmp_path / "project"
    (project_dir / ".artel").mkdir(parents=True)

    state = ServerState(config=ArtelConfig(), default_project_dir=str(project_dir))
    app = _create_rest_app(state, "test_token")
    headers = {"Authorization": "Bearer test_token"}

    async with TestClient(TestServer(app)) as client:
        get_resp = await client.get("/api/sessions/sess-1/rules", headers=headers)
        assert get_resp.status == 200
        initial = await get_resp.json()
        assert initial["rule_overrides"] == {"disabled_rule_ids": [], "enabled_rule_ids": []}

        disable_resp = await client.put(
            "/api/sessions/sess-1/rules/rule-1",
            headers=headers,
            json={"enabled": False},
        )
        assert disable_resp.status == 200
        disabled = await disable_resp.json()
        assert disabled["rule_overrides"]["disabled_rule_ids"] == ["rule-1"]

        reset_resp = await client.put(
            "/api/sessions/sess-1/rules/rule-1",
            headers=headers,
            json={"enabled": None},
        )
        assert reset_resp.status == 200
        reset_one = await reset_resp.json()
        assert reset_one["rule_overrides"] == {"disabled_rule_ids": [], "enabled_rule_ids": []}

        disable_again = await client.put(
            "/api/sessions/sess-1/rules/rule-2",
            headers=headers,
            json={"enabled": False},
        )
        assert disable_again.status == 200

        reset_all = await client.put(
            "/api/sessions/sess-1/rules/*",
            headers=headers,
            json={"enabled": None},
        )
        assert reset_all.status == 200
        reset_all_payload = await reset_all.json()
        assert reset_all_payload["rule_overrides"] == {
            "disabled_rule_ids": [],
            "enabled_rule_ids": [],
        }
