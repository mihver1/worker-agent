from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_schedule_service_manual_run_and_overlap_skip(tmp_path, monkeypatch):
    from artel_core import config as cfg_mod
    from artel_core.config import ArtelConfig
    from artel_core.schedules import add_schedule
    from artel_server import server as server_mod
    from artel_server.server import ScheduleService, ServerState

    fake_config = tmp_path / "config"
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".artel").mkdir()

    add_schedule(
        scope="project",
        schedule_id="heartbeat",
        project_dir=str(project_dir),
        kind="interval",
        every_seconds=60,
        prompt="Hello",
        overlap_policy="skip",
        run_missed="latest",
    )

    state = ServerState(config=ArtelConfig(), default_project_dir=str(project_dir))
    service = ScheduleService(state)
    state.schedule_service = service
    await service.reload()

    created_sessions: list[str] = []

    async def fake_create_server_session(state_obj, session_id, **kwargs):
        class _Session:
            def __init__(self):
                self.project_dir = kwargs.get("project_dir", str(project_dir))
                self.messages = [
                    object(),
                    type(
                        "M", (), {"role": type("R", (), {"value": "assistant"})(), "content": "OK"}
                    )(),
                ]
                self.provider = type("P", (), {"close": staticmethod(lambda: None)})()

            def abort(self):
                return None

        created_sessions.append(session_id)
        state_obj.sessions[session_id] = _Session()
        return state_obj.sessions[session_id]

    class _Controller:
        def __init__(self):
            self._run_task = None
            self.started = 0

        async def ensure_idle(self):
            if self._run_task is not None and not self._run_task.done():
                raise RuntimeError("Session is busy")

        async def start(self, content, attachments=None):
            del content, attachments
            self.started += 1

            async def _job():
                await asyncio.sleep(0.05)

            self._run_task = asyncio.create_task(_job())

        async def abort(self):
            if self._run_task is not None:
                self._run_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await self._run_task

    controller = _Controller()
    monkeypatch.setattr(server_mod, "_create_server_session", fake_create_server_session)
    monkeypatch.setattr(server_mod, "_get_session_controller", lambda _state, _sid: controller)

    await service.run_now("heartbeat")
    await service.run_now("heartbeat")
    await asyncio.sleep(0.1)

    snapshot = service.snapshot()
    state_payload = snapshot["schedules"][0]["state"]
    assert controller.started == 1
    assert state_payload["last_status"] == "succeeded"
    assert state_payload["total_skips"] == 1
    assert created_sessions == ["schedule:heartbeat"]
