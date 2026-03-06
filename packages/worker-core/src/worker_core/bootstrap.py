"""Shared runtime bootstrap helpers for Worker modes (CLI/TUI/RPC/server)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any

from worker_ai.providers import create_default_registry

from worker_core.agent import AgentSession
from worker_core.config import WorkerConfig
from worker_core.extensions import Extension, HookDispatcher, load_extensions_async
from worker_core.tools import Tool
from worker_core.tools.builtins import create_builtin_tools

ResolveApiKeyResult = tuple[str | None, str]
ResolveApiKey = Callable[
    [WorkerConfig, str],
    ResolveApiKeyResult | Awaitable[ResolveApiKeyResult],
]
_OPENAI_COMPAT_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "xai": "https://api.x.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "together": "https://api.together.xyz/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "deepseek": "https://api.deepseek.com/v1",
}
_KEYLESS_PROVIDER_TYPES = {"ollama"}


def resolve_provider_runtime_config(
    config: WorkerConfig, provider_name: str,
) -> tuple[str, dict[str, Any]]:
    """Resolve the effective runtime provider type and constructor kwargs."""
    provider_config = config.providers.get(provider_name)
    provider_type = "openai_compat" if provider_name in _OPENAI_COMPAT_BASE_URLS else provider_name
    base_url = _OPENAI_COMPAT_BASE_URLS.get(provider_name, "")
    kwargs: dict[str, Any] = {}

    if provider_config:
        if provider_config.type:
            provider_type = provider_config.type
        if provider_config.base_url:
            base_url = provider_config.base_url
        for field_name in ("api_type", "region", "profile", "api_version"):
            value = getattr(provider_config, field_name, "")
            if value:
                kwargs[field_name] = value

    if base_url:
        kwargs["base_url"] = base_url

    return provider_type, kwargs


def provider_requires_api_key(config: WorkerConfig, provider_name: str) -> bool:
    """Return whether the effective provider type requires explicit credentials."""
    provider_type, _ = resolve_provider_runtime_config(config, provider_name)
    return provider_type not in _KEYLESS_PROVIDER_TYPES


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


async def fetch_model_runtime_info(
    provider_name: str, model_id: str,
) -> tuple[int, float, float]:
    """Return (context_window, input_price_per_m, output_price_per_m)."""
    try:
        from worker_ai.models_catalog import ModelsCatalog

        model = await ModelsCatalog.get_model(provider_name, model_id)
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
    config: WorkerConfig,
    provider_name: str,
    model_id: str,
    *,
    project_dir: str,
    resolve_api_key: ResolveApiKey,
    include_extensions: bool = True,
) -> RuntimeBootstrap:
    """Create provider/tools/hooks/extensions and model metadata for a session."""
    registry = create_default_registry()
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

    extensions: list[Extension] = []
    hooks = HookDispatcher()
    if include_extensions:
        extensions, hooks = await load_extensions_async()
        for ext in extensions:
            tools.extend(ext.get_tools())

    context_window, input_price_per_m, output_price_per_m = await fetch_model_runtime_info(
        provider_name, model_id
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
    )


def create_agent_session_from_bootstrap(
    config: WorkerConfig,
    bootstrap: RuntimeBootstrap,
    *,
    project_dir: str,
    store: Any | None = None,
    session_id: str = "",
    permission_callback: Any | None = None,
) -> AgentSession:
    """Create an AgentSession with consistent defaults across all modes."""
    return AgentSession(
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
