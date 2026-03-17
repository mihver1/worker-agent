"""Shared runtime bootstrap helpers for Artel modes (CLI/TUI/RPC/server)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any

from artel_ai.providers import create_default_registry

from artel_core import provider_resolver as _provider_resolver
from artel_core.agent import AgentSession
from artel_core.builtin_capabilities import load_builtin_capabilities
from artel_core.config import ArtelConfig
from artel_core.extensions import (
    Extension,
    ExtensionContext,
    HookDispatcher,
    load_ai_extensions_async,
    load_extensions_async,
)
from artel_core.tools import Tool
from artel_core.tools.builtins import create_builtin_tools

ResolveApiKeyResult = tuple[str | None, str]
ResolveApiKey = Callable[
    [ArtelConfig, str],
    ResolveApiKeyResult | Awaitable[ResolveApiKeyResult],
]
provider_requires_api_key = _provider_resolver.provider_requires_api_key
resolve_provider_runtime_config = _provider_resolver.resolve_provider_runtime_config


@dataclass
class RuntimeBootstrap:
    provider_name: str
    model_id: str
    provider: Any
    tools: list[Tool]
    hooks: HookDispatcher
    extensions: list[Extension]
    context_window: int
    input_price_per_m: float
    output_price_per_m: float
    small_provider: Any | None = None
    small_model_id: str = ""
    mcp_runtime: Any | None = None
    lsp_runtime: Any | None = None


async def fetch_model_runtime_info(
    config: ArtelConfig,
    provider_name: str,
    model_id: str,
) -> tuple[int, float, float]:
    """Return (context_window, input_price_per_m, output_price_per_m)."""
    try:
        model = await _provider_resolver.get_effective_model_info(config, provider_name, model_id)
        if model:
            return (
                model.context_window or 0,
                model.input_price_per_m or 0.0,
                model.output_price_per_m or 0.0,
            )
    except Exception:
        pass
    return (0, 0.0, 0.0)


async def bootstrap_runtime(
    config: ArtelConfig,
    provider_name: str,
    model_id: str,
    *,
    project_dir: str,
    resolve_api_key: ResolveApiKey,
    include_extensions: bool = True,
    runtime: str = "local",
) -> RuntimeBootstrap:
    """Create provider/tools/hooks/extensions and model metadata for a session."""
    registry = create_default_registry()
    builtin_capabilities = load_builtin_capabilities(project_dir=project_dir)
    extension_context = ExtensionContext(
        project_dir=project_dir,
        runtime=runtime,
        config=config,
        extras={"builtin_capabilities": builtin_capabilities},
    )
    mcp_runtime = None
    lsp_runtime = None
    if include_extensions:
        ai_extensions = await load_ai_extensions_async(context=extension_context)
        for ext in ai_extensions:
            with suppress(Exception):
                ext.register_providers(registry)
    resolved_api_key = resolve_api_key(config, provider_name)
    if isawaitable(resolved_api_key):
        api_key, auth_type = await resolved_api_key
    else:
        api_key, auth_type = resolved_api_key
    provider_type, kwargs = resolve_provider_runtime_config(config, provider_name)
    if auth_type == "oauth":
        kwargs["auth_type"] = "oauth"
    provider = registry.create(provider_type, api_key=api_key, **kwargs)
    tools = create_builtin_tools(project_dir)

    mcp_capability = builtin_capabilities.get("artel-mcp")
    if mcp_capability is not None:
        with suppress(Exception):
            from artel_core.mcp_runtime import McpRuntimeManager

            mcp_runtime = McpRuntimeManager()
            await mcp_runtime.load(extension_context)
            tools.extend(mcp_runtime.tools)
            extension_context.extras["mcp_runtime"] = mcp_runtime

    lsp_capability = builtin_capabilities.get("artel-lsp")
    if lsp_capability is not None:
        with suppress(Exception):
            from artel_core.lsp_runtime import LspRuntimeManager

            lsp_runtime = LspRuntimeManager()
            await lsp_runtime.load(extension_context)
            tools.extend(lsp_runtime.tools)
            extension_context.extras["lsp_runtime"] = lsp_runtime

    extensions: list[Extension] = []
    hooks = HookDispatcher()
    if include_extensions:
        extensions, hooks = await load_extensions_async(context=extension_context)
        for ext in extensions:
            tools.extend(ext.get_tools())

    context_window, input_price_per_m, output_price_per_m = await fetch_model_runtime_info(
        config, provider_name, model_id
    )

    # Bootstrap small model if configured
    small_provider = None
    small_model_id = ""
    if config.agent.small_model and "/" in config.agent.small_model:
        sm_provider_name, small_model_id = config.agent.small_model.split("/", 1)
        sm_resolved = resolve_api_key(config, sm_provider_name)
        if isawaitable(sm_resolved):
            sm_key, sm_auth = await sm_resolved
        else:
            sm_key, sm_auth = sm_resolved
        sm_type, sm_kwargs = resolve_provider_runtime_config(config, sm_provider_name)
        if sm_auth == "oauth":
            sm_kwargs["auth_type"] = "oauth"
        small_provider = registry.create(sm_type, api_key=sm_key, **sm_kwargs)

    return RuntimeBootstrap(
        provider_name=provider_name,
        model_id=model_id,
        provider=provider,
        tools=tools,
        hooks=hooks,
        extensions=extensions,
        context_window=context_window,
        input_price_per_m=input_price_per_m,
        output_price_per_m=output_price_per_m,
        small_provider=small_provider,
        small_model_id=small_model_id,
        mcp_runtime=mcp_runtime,
        lsp_runtime=lsp_runtime,
    )


def create_agent_session_from_bootstrap(
    config: ArtelConfig,
    bootstrap: RuntimeBootstrap,
    *,
    project_dir: str,
    store: Any | None = None,
    session_id: str = "",
    permission_callback: Any | None = None,
) -> AgentSession:
    """Create an AgentSession with consistent defaults across all modes."""
    session = AgentSession(
        provider=bootstrap.provider,
        model=bootstrap.model_id,
        tools=bootstrap.tools,
        system_prompt=config.agent.system_prompt,
        project_dir=project_dir,
        temperature=config.agent.temperature,
        max_turns=config.agent.max_turns,
        thinking_level=config.agent.thinking,  # type: ignore[arg-type]
        store=store,
        session_id=session_id,
        auto_compact=config.sessions.auto_compact,
        compact_threshold=config.sessions.compact_threshold,
        context_window=bootstrap.context_window,
        permissions_config=config.permissions,
        permission_callback=permission_callback,
        hooks=bootstrap.hooks,
        small_provider=bootstrap.small_provider,
        small_model=bootstrap.small_model_id,
    )
    session.mcp_runtime = bootstrap.mcp_runtime
    session.lsp_runtime = bootstrap.lsp_runtime
    return session
