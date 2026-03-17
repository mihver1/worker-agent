from __future__ import annotations

from typing import Any

from artel_ai.models import Done, ToolDef, Usage
from artel_core.agent import AgentEvent, AgentEventType, AgentSession
from artel_core.bootstrap import RuntimeBootstrap
from artel_core.config import ArtelConfig
from artel_core.delegation.registry import reset_registry
from artel_core.delegation.service import DelegationService
from artel_core.extensions import ExtensionContext, HookDispatcher
from artel_core.tools import Tool
from conftest import MockProvider


class _ExtensionTool(Tool):
    name = "dummy_ext"
    description = "Dummy extension tool."

    async def execute(self, **kwargs: Any) -> str:
        return "ok"

    def definition(self) -> ToolDef:
        return ToolDef(name=self.name, description=self.description, parameters=[])


class _FakeChildSession:
    async def run(self, prompt: str):
        yield AgentEvent(type=AgentEventType.TEXT_DELTA, content=f"done: {prompt}")
        yield AgentEvent(type=AgentEventType.DONE, usage=Usage())


async def _fake_bootstrap_runtime(*args, **kwargs) -> RuntimeBootstrap:
    return RuntimeBootstrap(
        provider_name="mock",
        model_id="mock-model",
        provider=MockProvider(responses=[[Done(usage=Usage())]]),
        tools=[],
        hooks=HookDispatcher(),
        extensions=[],
        context_window=0,
        input_price_per_m=0.0,
        output_price_per_m=0.0,
    )


async def test_delegate_task_runs_and_waits(tmp_path, monkeypatch) -> None:
    import artel_core.delegation.service as service_mod

    reset_registry()
    monkeypatch.setattr(service_mod, "bootstrap_runtime", _fake_bootstrap_runtime)
    monkeypatch.setattr(
        service_mod,
        "create_agent_session_from_bootstrap",
        lambda *args, **kwargs: _FakeChildSession(),
    )

    service = DelegationService(
        ExtensionContext(project_dir=str(tmp_path), runtime="local", config=ArtelConfig())
    )
    parent_session = AgentSession(
        provider=MockProvider(),
        model="parent-model",
        tools=[],
        project_dir=str(tmp_path),
        session_id="parent-session",
    )

    result = await service.spawn(parent_session, task="Inspect the repo", wait=True)

    assert result.status == "completed"
    assert "Assigned task:" in result.result
    assert "Inspect the repo" in result.result


class _DummyContext:
    def __init__(self) -> None:
        self.config = ArtelConfig()
        self.config.permissions.edit = "ask"
        self.config.permissions.write = "ask"
        self.config.permissions.bash = "ask"
        self.config.permissions.bash_commands = {"pytest *": "allow"}
        self.project_dir = "."
        self.runtime = "local"


def test_config_for_readonly_is_self_contained() -> None:
    service = DelegationService(_DummyContext())

    config = service._config_for_mode("readonly")

    assert config.permissions.edit == "deny"
    assert config.permissions.write == "deny"
    assert config.permissions.bash == "deny"
    assert config.permissions.bash_commands == {}


def test_config_for_inherit_preserves_parent_permissions() -> None:
    service = DelegationService(_DummyContext())

    config = service._config_for_mode("inherit")

    assert config.permissions.edit == "ask"
    assert config.permissions.write == "ask"
    assert config.permissions.bash == "ask"
    assert config.permissions.bash_commands == {"pytest *": "allow"}


async def test_inherit_profile_keeps_extensions_and_parent_permission_callback(monkeypatch) -> None:
    import artel_core.delegation.service as service_mod

    reset_registry()
    captured: dict[str, Any] = {}

    async def _capture_bootstrap_runtime(*args, **kwargs) -> RuntimeBootstrap:
        captured["provider_name"] = args[1]
        captured["model_id"] = args[2]
        captured["include_extensions"] = kwargs["include_extensions"]
        return RuntimeBootstrap(
            provider_name="mock",
            model_id="mock-model",
            provider=MockProvider(),
            tools=[_ExtensionTool()],
            hooks=HookDispatcher(),
            extensions=[],
            context_window=0,
            input_price_per_m=0.0,
            output_price_per_m=0.0,
        )

    def _fake_create_session(*args, **kwargs):
        captured["permission_callback"] = kwargs.get("permission_callback")
        runtime = args[1]
        captured["tool_names"] = [tool.name for tool in runtime.tools]
        return _FakeChildSession()

    monkeypatch.setattr(service_mod, "bootstrap_runtime", _capture_bootstrap_runtime)
    monkeypatch.setattr(service_mod, "create_agent_session_from_bootstrap", _fake_create_session)

    async def _parent_permission_callback(tool_name: str, args: dict[str, Any]) -> bool:
        return True

    service = DelegationService(_DummyContext())
    parent_session = AgentSession(
        provider=MockProvider(),
        model="parent-model",
        tools=[],
        project_dir=".",
        session_id="parent-session",
        permission_callback=_parent_permission_callback,
    )

    run = await service.spawn(
        parent_session, task="Commit and push", model="inherit", mode="inherit", wait=True
    )

    assert run.status == "completed"
    assert run.model == "mock/parent-model"
    assert captured["provider_name"] == "mock"
    assert captured["model_id"] == "parent-model"
    assert captured["include_extensions"] is True
    assert captured["permission_callback"] is _parent_permission_callback
    assert {
        "read",
        "write",
        "edit",
        "bash",
        "worktree",
        "delegate_task",
        "list_delegates",
        "get_delegate",
        "cancel_delegate",
        "grep",
        "find",
        "ls",
        "dummy_ext",
    }.issubset(set(captured["tool_names"]))
