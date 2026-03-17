from __future__ import annotations

import asyncio

from aiohttp.test_utils import TestClient, TestServer
from artel_core.config import ArtelConfig
from artel_core.delegation.registry import get_registry, reset_registry
from artel_server.server import ServerState, _create_rest_app


async def test_server_lists_gets_and_cancels_delegates() -> None:
    reset_registry()
    registry = get_registry()
    run = registry.create_run(
        parent_session_id="session-1",
        task="Inspect the Artel server",
        context="",
        model="mock/mock-model",
        project_dir="/tmp/project",
        mode="readonly",
    )
    registry.mark_running(run.id)

    async def _running_job() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            registry.mark_cancelled(run.id)
            raise

    registry.bind_task(run.id, asyncio.create_task(_running_job()))

    state = ServerState(config=ArtelConfig(), default_project_dir="/tmp/project")
    app = _create_rest_app(state, "test_token")

    async with TestClient(TestServer(app)) as client:
        resp = await client.get(
            "/api/sessions/session-1/delegates", headers={"Authorization": "Bearer test_token"}
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["delegates"][0]["id"] == run.id

        resp = await client.get(
            f"/api/sessions/session-1/delegates/{run.id}",
            headers={"Authorization": "Bearer test_token"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["delegate"]["task"] == "Inspect the Artel server"
        assert data["delegate"]["latest_update"] == "started"

        resp = await client.post(
            f"/api/sessions/session-1/delegates/{run.id}/cancel",
            headers={"Authorization": "Bearer test_token"},
            json={},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["cancelled"] is True

    await asyncio.sleep(0)
    assert registry.get_run(run.id).status == "cancelled"
