from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_tui_schedules_remote_command_lists_runs(monkeypatch):
    from artel_tui.app import ArtelApp

    app = ArtelApp(remote_url="ws://127.0.0.1:7432", auth_token="tok")
    messages: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant", **kwargs: messages.append((role, content))  # type: ignore[method-assign]

    class _Control:
        async def list_schedules(self):
            return {
                "count": 1,
                "next_run_at": "2025-01-01 10:00:00",
                "schedules": [
                    {
                        "schedule": {
                            "id": "heartbeat",
                            "enabled": True,
                            "kind": "interval",
                            "every_seconds": 60,
                        },
                        "state": {
                            "last_status": "succeeded",
                            "next_run_at": "2025-01-01 10:00:00",
                        },
                    }
                ],
            }

    app._remote_control = lambda: _Control()  # type: ignore[method-assign]

    await app._cmd_schedules("")

    assert messages
    assert messages[-1][0] == "tool"
    assert "heartbeat" in messages[-1][1]
    assert "Scheduled tasks:" in messages[-1][1]
