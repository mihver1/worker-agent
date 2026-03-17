"""In-process delegation service for single-window Artel subagents."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any

from artel_core.agent import AgentEventType, AgentSession
from artel_core.bootstrap import bootstrap_runtime, create_agent_session_from_bootstrap
from artel_core.config import ArtelConfig
from artel_core.delegation.models import DelegatedRun
from artel_core.delegation.registry import get_registry
from artel_core.extensions import ExtensionContext
from artel_core.tools.builtins import create_all_tools, create_readonly_tools

_DELEGATE_PROMPT = (
    "You are a delegated Artel artel operating inside the current session context. "
    "Focus only on the assigned task. Use tools when they help. Return a concise final report "
    "with concrete findings, file paths, commands run, and unresolved issues."
)


class DelegationService:
    """Create and monitor delegated in-process runs."""

    def __init__(self, context: ExtensionContext | None = None) -> None:
        self.context = context
        self.registry = get_registry()

    @property
    def config(self) -> ArtelConfig:
        if isinstance(getattr(self.context, "config", None), ArtelConfig):
            return self.context.config
        return ArtelConfig()

    @property
    def project_dir(self) -> str:
        if self.context and self.context.project_dir:
            return self.context.project_dir
        return "."

    def list_for_session(self, parent_session_id: str) -> list[DelegatedRun]:
        return self.registry.list_runs(parent_session_id)

    def list_for_project(self, project_dir: str = "") -> list[DelegatedRun]:
        return self.registry.list_project_runs(project_dir)

    def get_for_session(self, parent_session_id: str, run_id: str) -> DelegatedRun | None:
        return self.registry.get_session_run(parent_session_id, run_id)

    async def wait_for_session_run(self, parent_session_id: str, run_id: str) -> DelegatedRun:
        run = self.registry.get_session_run(parent_session_id, run_id)
        if run is None:
            raise RuntimeError(f"Unknown delegate: {run_id}")
        return await self.registry.wait(run.id)

    def cancel_for_session(self, parent_session_id: str, run_id: str) -> bool:
        run = self.registry.get_session_run(parent_session_id, run_id)
        if run is None:
            return False
        return self.registry.cancel(run.id)

    async def spawn(
        self,
        parent_session: AgentSession,
        *,
        task: str,
        context: str = "",
        model: str = "",
        project_dir: str = "",
        mode: str = "readonly",
        wait: bool = False,
    ) -> DelegatedRun:
        normalized_mode = self._normalize_mode(mode)
        provider_name, model_id = self._resolve_model(parent_session, model)
        resolved_project_dir = project_dir.strip() or parent_session.project_dir or self.project_dir
        run = self.registry.create_run(
            parent_session_id=parent_session.session_id,
            task=task,
            context=context,
            model=f"{provider_name}/{model_id}",
            project_dir=resolved_project_dir,
            mode=normalized_mode,
        )
        task_handle = asyncio.create_task(
            self._run_delegate(
                run,
                provider_name=provider_name,
                model_id=model_id,
                permission_callback=getattr(parent_session, "permission_callback", None),
                parent_session=parent_session,
            )
        )
        self.registry.bind_task(run.id, task_handle)
        if wait:
            return await self.registry.wait(run.id)
        return run

    def _normalize_mode(self, mode: str) -> str:
        normalized = mode.strip().lower() or "readonly"
        if normalized not in {"readonly", "inherit"}:
            raise RuntimeError("mode must be one of: readonly, inherit")
        return normalized

    def _resolve_model(self, parent_session: AgentSession, model: str) -> tuple[str, str]:
        provider_name = getattr(parent_session.provider, "name", "unknown")
        requested = model.strip()
        inherited = str(getattr(parent_session, "model", "")).strip()
        if not requested or requested.lower() == "inherit":
            if "/" in inherited:
                return tuple(inherited.split("/", 1))  # type: ignore[return-value]
            return provider_name, inherited
        if "/" in requested:
            return tuple(requested.split("/", 1))  # type: ignore[return-value]
        return provider_name, requested

    def _config_for_mode(self, mode: str) -> ArtelConfig:
        config = self.config.model_copy(deep=True)
        if mode == "readonly":
            config.permissions.edit = "deny"
            config.permissions.write = "deny"
            config.permissions.bash = "deny"
            if hasattr(config.permissions, "bash_commands"):
                config.permissions.bash_commands = {}
        return config

    def _include_extensions_for_mode(self, mode: str) -> bool:
        return mode == "inherit"

    def _session_tools(self, bootstrap_tools: list[Any], project_dir: str, mode: str) -> list[Any]:
        if mode == "readonly":
            builtin_tools = create_readonly_tools(project_dir)
            builtin_names = {tool.name for tool in builtin_tools}
            readonly_runtime_tools = [
                tool
                for tool in bootstrap_tools
                if getattr(tool, "name", "").startswith("lsp_")
                and getattr(tool, "name", "") not in builtin_names
            ]
            return [*builtin_tools, *readonly_runtime_tools]
        builtin_tools = create_all_tools(project_dir)
        builtin_names = {tool.name for tool in builtin_tools}
        extension_tools = [
            tool
            for tool in bootstrap_tools
            if getattr(tool, "name", "") and getattr(tool, "name", "") not in builtin_names
        ]
        return [*builtin_tools, *extension_tools]

    def _build_prompt(self, task: str, context: str) -> str:
        parts = [_DELEGATE_PROMPT]
        if context.strip():
            parts.append(f"Parent context:\n{context.strip()}")
        parts.append(f"Assigned task:\n{task.strip()}")
        return "\n\n".join(parts)

    async def _run_delegate(
        self,
        run: DelegatedRun,
        *,
        provider_name: str,
        model_id: str,
        permission_callback: Any | None,
        parent_session: AgentSession,
    ) -> None:
        from artel_core.cli import _resolve_api_key

        runtime = None
        self.registry.mark_running(run.id)
        try:
            session_config = self._config_for_mode(run.mode)
            runtime = await bootstrap_runtime(
                session_config,
                provider_name,
                model_id,
                project_dir=run.project_dir,
                resolve_api_key=_resolve_api_key,
                include_extensions=self._include_extensions_for_mode(run.mode),
                runtime=self.context.runtime if self.context else "local",
            )
            runtime.tools = self._session_tools(runtime.tools, run.project_dir, run.mode)
            session = create_agent_session_from_bootstrap(
                session_config,
                runtime,
                project_dir=run.project_dir,
                session_id=run.id,
                permission_callback=permission_callback if run.mode == "inherit" else None,
            )
            prompt = self._build_prompt(run.task, run.context)
            final_chunks: list[str] = []
            async for event in session.run(prompt):
                if event.type == AgentEventType.TEXT_DELTA:
                    final_chunks.append(event.content)
                elif event.type == AgentEventType.TOOL_CALL:
                    self.registry.append_event(run.id, f"tool {event.tool_name}")
                elif event.type == AgentEventType.TOOL_RESULT:
                    if event.content.strip():
                        summary = event.content.strip().splitlines()[0]
                    else:
                        summary = "(no output)"
                    self.registry.append_event(run.id, f"result {event.tool_name}: {summary}")
                elif event.type == AgentEventType.ERROR:
                    raise RuntimeError(event.error or "Delegate failed.")
            result = "".join(final_chunks).strip() or "(no output)"
            self.registry.mark_completed(run.id, result)
            callback = getattr(parent_session, "delegation_event_callback", None)
            if callable(callback):
                with suppress(Exception):
                    callback(
                        "delegation_completed",
                        {
                            "id": run.id,
                            "task": run.task,
                            "status": "completed",
                            "result_preview": result[:400],
                        },
                    )
        except asyncio.CancelledError:
            self.registry.mark_cancelled(run.id)
            raise
        except Exception as exc:
            self.registry.mark_failed(run.id, str(exc))
            callback = getattr(parent_session, "delegation_event_callback", None)
            if callable(callback):
                with suppress(Exception):
                    callback(
                        "delegation_failed",
                        {
                            "id": run.id,
                            "task": run.task,
                            "status": "failed",
                            "error": str(exc),
                        },
                    )
        finally:
            if runtime is not None:
                with suppress(Exception):
                    await runtime.provider.close()
                if runtime.small_provider is not None:
                    with suppress(Exception):
                        await runtime.small_provider.close()
                if runtime.mcp_runtime is not None:
                    with suppress(Exception):
                        await runtime.mcp_runtime.close()
                if runtime.lsp_runtime is not None:
                    with suppress(Exception):
                        await runtime.lsp_runtime.close()


__all__ = ["DelegationService"]
