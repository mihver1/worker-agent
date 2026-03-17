from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_remote_control_rules_endpoints_are_used():
    from artel_tui.app import ArtelApp

    class _RemoteClient:
        def __init__(self):
            self.calls = []

        async def list_rules(self, *, project_dir: str = ""):
            self.calls.append(("list", project_dir))
            return {
                "rules": [
                    {"id": "rule-1", "scope": "project", "enabled": True, "text": "Use pytest."}
                ]
            }

        async def add_rule(
            self, *, scope: str, text: str, enabled: bool = True, project_dir: str = ""
        ):
            self.calls.append(("add", scope, text, enabled, project_dir))
            return {"rule": {"id": "rule-2", "scope": scope, "enabled": enabled, "text": text}}

        async def edit_rule(
            self, rule_id: str, *, text=None, scope=None, enabled=None, project_dir: str = ""
        ):
            self.calls.append(("edit", rule_id, text, scope, enabled, project_dir))
            return {
                "rule": {
                    "id": rule_id,
                    "scope": scope or "project",
                    "enabled": True if enabled is None else enabled,
                    "text": text or "x",
                }
            }

        async def delete_rule(self, rule_id: str, *, project_dir: str = ""):
            self.calls.append(("delete", rule_id, project_dir))
            return {"rule": {"id": rule_id}}

        async def set_session_rule_enabled(
            self, session_id: str, rule_id: str, *, enabled: bool | None
        ):
            self.calls.append(("toggle", session_id, rule_id, enabled))
            if enabled is None and rule_id == "*":
                return {
                    "rule_id": rule_id,
                    "enabled": None,
                    "rule_overrides": {"enabled_rule_ids": [], "disabled_rule_ids": []},
                }
            if enabled is None:
                return {
                    "rule_id": rule_id,
                    "enabled": None,
                    "rule_overrides": {"enabled_rule_ids": [], "disabled_rule_ids": []},
                }
            return {
                "rule_id": rule_id,
                "enabled": enabled,
                "rule_overrides": {
                    "enabled_rule_ids": [rule_id] if enabled else [],
                    "disabled_rule_ids": [] if enabled else [rule_id],
                },
            }

        async def get_session_rule_overrides(self, session_id: str):
            self.calls.append(("get_overrides", session_id))
            return {
                "session_id": session_id,
                "rule_overrides": {"enabled_rule_ids": [], "disabled_rule_ids": []},
            }

    app = ArtelApp(remote_url="ws://localhost:7432")
    app._remote_project_dir = "/srv/project"
    app._remote_control_client = _RemoteClient()
    seen: list[tuple[str, str]] = []
    app._add_message = lambda content, role="assistant": seen.append((content, role))  # type: ignore[method-assign]

    await app._cmd_rules()
    await app._cmd_rule("enable rule-1")
    await app._cmd_rule("disable rule-1")
    await app._cmd_rule("delete rule-1")
    await app._cmd_rule("reset rule-1")
    await app._cmd_rule("reset all")

    assert any("Configured rules:" in message for message, _ in seen)
    assert any("persisted=enabled session=- effective=enabled" in message for message, _ in seen)
    assert any("Enabled rule rule-1 for this session." in message for message, _ in seen)
    assert any("Disabled rule rule-1 for this session." in message for message, _ in seen)
    assert any("Deleted rule rule-1." in message for message, _ in seen)
    assert any("Reset session override for rule rule-1." in message for message, _ in seen)
    assert any("Reset all session rule overrides." in message for message, _ in seen)
