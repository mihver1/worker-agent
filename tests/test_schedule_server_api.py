from __future__ import annotations

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_schedule_rest_crud_and_run(tmp_path, monkeypatch):
    from artel_core import config as cfg_mod
    from artel_core.config import ArtelConfig
    from artel_server import server as server_mod
    from artel_server.server import ScheduleService, ServerState, _create_rest_app

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".artel").mkdir()

    state = ServerState(config=ArtelConfig(), default_project_dir=str(project_dir))
    state.schedule_service = ScheduleService(state)
    await state.schedule_service.reload()

    seen: list[tuple[str, str]] = []

    async def fake_create_server_session(state_obj, session_id, **kwargs):
        class _Session:
            def __init__(self):
                self.project_dir = kwargs.get("project_dir", str(project_dir))
                self.messages = [
                    object(),
                    type(
                        "M",
                        (),
                        {"role": type("R", (), {"value": "assistant"})(), "content": "done"},
                    )(),
                ]
                self.provider = type("P", (), {"close": staticmethod(lambda: None)})()

            def abort(self):
                return None

        seen.append((session_id, kwargs.get("project_dir", "")))
        state_obj.sessions[session_id] = _Session()
        return state_obj.sessions[session_id]

    controller_runs: list[str] = []

    class _Controller:
        def __init__(self, session_id):
            self.session_id = session_id
            self._run_task = None

        async def ensure_idle(self):
            return None

        async def start(self, content, attachments=None):
            del attachments
            controller_runs.append(content)

            async def _job():
                await asyncio.sleep(0)

            self._run_task = asyncio.create_task(_job())
            await self._run_task

        async def abort(self):
            return None

    monkeypatch.setattr(server_mod, "_create_server_session", fake_create_server_session)
    monkeypatch.setattr(server_mod, "_get_session_controller", lambda _state, sid: _Controller(sid))

    app = _create_rest_app(state, "test_token")
    headers = {"Authorization": "Bearer test_token"}

    async with TestClient(TestServer(app)) as client:
        add_resp = await client.post(
            "/api/schedules",
            headers=headers,
            json={
                "id": "heartbeat",
                "scope": "project",
                "kind": "interval",
                "every_seconds": 60,
                "prompt": "Summarize repo health",
                "run_missed": "latest",
            },
        )
        assert add_resp.status == 200
        added = await add_resp.json()
        assert added["schedule"]["id"] == "heartbeat"

        list_resp = await client.get("/api/schedules", headers=headers)
        assert list_resp.status == 200
        listed = await list_resp.json()
        assert listed["count"] == 1
        assert listed["schedules"][0]["schedule"]["id"] == "heartbeat"

        run_resp = await client.post("/api/schedules/heartbeat/run", headers=headers, json={})
        assert run_resp.status == 200
        run_payload = await run_resp.json()
        assert run_payload["schedules"][0]["state"]["last_status"] in {"running", "succeeded"}

        await asyncio.sleep(0.05)
        snapshot = state.schedule_service.snapshot()
        assert snapshot["schedules"][0]["state"]["last_status"] == "succeeded"
        assert controller_runs == ["Summarize repo health"]
        assert seen

        edit_resp = await client.put(
            "/api/schedules/heartbeat",
            headers=headers,
            json={"enabled": False, "arg": "repo=backend"},
        )
        assert edit_resp.status == 200
        edited = await edit_resp.json()
        assert edited["schedule"]["enabled"] is False

        delete_resp = await client.delete("/api/schedules/heartbeat", headers=headers)
        assert delete_resp.status == 200
        deleted = await delete_resp.json()
        assert deleted["schedule"]["id"] == "heartbeat"
