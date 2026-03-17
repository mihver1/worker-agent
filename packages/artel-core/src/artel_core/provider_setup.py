"""Helpers for presenting provider setup state across local and remote UIs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from artel_core.bootstrap import provider_requires_api_key
from artel_core.provider_resolver import get_provider_config, get_provider_env_vars


@dataclass(frozen=True, slots=True)
class ProviderSetupEntry:
    """A provider entry shown by provider setup/status commands."""

    id: str
    name: str
    status: str
    hint: str


def provider_ids_for_listing(config: Any) -> list[str]:
    from artel_ai.provider_specs import iter_provider_specs

    provider_ids = [spec.id for spec in iter_provider_specs()]
    for provider_id in config.providers:
        if provider_id not in provider_ids:
            provider_ids.append(provider_id)
    return provider_ids


def _looks_local_base_url(base_url: str) -> bool:
    return base_url.startswith("http://localhost") or base_url.startswith("http://127.0.0.1")


def provider_setup_hint(
    provider_id: str,
    *,
    env_vars: tuple[str, ...],
    oauth_supported: bool,
    requires_api_key: bool,
    base_url: str,
) -> str:
    config_path = f"[providers.{provider_id}]"
    if oauth_supported and env_vars:
        return f"run /connect {provider_id} or set {env_vars[0]}"
    if oauth_supported:
        return f"run /connect {provider_id}"
    if not requires_api_key:
        if provider_id == "bedrock":
            return f"configure AWS credentials or {config_path}"
        if provider_id in {"google_vertex", "vertex_anthropic"}:
            return f"set {config_path}.project / .location or use ADC"
        if _looks_local_base_url(base_url) or provider_id in {
            "ollama",
            "lmstudio",
            "llama.cpp",
        }:
            return f"start the service or set {config_path}.base_url"
        return f"configure {config_path}"
    if env_vars:
        return f"set {env_vars[0]} or {config_path}.api_key"
    return f"configure {config_path}"


def provider_setup_hint_for_config(config: Any, provider_id: str) -> str:
    from artel_ai.oauth import list_oauth_provider_names
    from artel_ai.provider_specs import get_provider_spec

    spec = get_provider_spec(provider_id)
    canonical_id = spec.id if spec is not None else provider_id
    provider_config = get_provider_config(config, provider_id)
    runtime_base_url = (
        provider_config.base_url
        if provider_config and provider_config.base_url
        else (spec.default_base_url if spec is not None else "")
    )
    oauth_supported = canonical_id in set(list_oauth_provider_names())
    return provider_setup_hint(
        canonical_id,
        env_vars=tuple(get_provider_env_vars(config, provider_id)),
        oauth_supported=oauth_supported,
        requires_api_key=provider_requires_api_key(config, provider_id),
        base_url=runtime_base_url,
    )


async def collect_provider_setup_entries(
    config: Any,
    resolve_api_key: Callable[[Any, str], Awaitable[tuple[str | None, str]]],
) -> list[ProviderSetupEntry]:
    from artel_ai.oauth import list_oauth_provider_names
    from artel_ai.provider_specs import get_provider_spec

    oauth_providers = set(list_oauth_provider_names())
    entries: list[ProviderSetupEntry] = []
    for provider_id in provider_ids_for_listing(config):
        provider_config = get_provider_config(config, provider_id)
        spec = get_provider_spec(provider_id)
        canonical_id = spec.id if spec is not None else provider_id
        display_name = (
            provider_config.name
            if provider_config and provider_config.name
            else (spec.display_name if spec is not None else provider_id)
        )
        env_vars = tuple(get_provider_env_vars(config, provider_id))
        requires_key = provider_requires_api_key(config, provider_id)
        runtime_base_url = (
            provider_config.base_url
            if provider_config and provider_config.base_url
            else (spec.default_base_url if spec is not None else "")
        )
        api_key, auth_type = await resolve_api_key(config, provider_id)

        if api_key:
            status = "connected (oauth)" if auth_type == "oauth" else "configured"
            hint = "use /models"
        elif provider_config is not None and requires_key:
            status = "partially configured"
            hint = provider_setup_hint(
                canonical_id,
                env_vars=env_vars,
                oauth_supported=canonical_id in oauth_providers,
                requires_api_key=requires_key,
                base_url=runtime_base_url,
            )
        elif not requires_key:
            status = "keyless"
            hint = provider_setup_hint(
                canonical_id,
                env_vars=env_vars,
                oauth_supported=canonical_id in oauth_providers,
                requires_api_key=requires_key,
                base_url=runtime_base_url,
            )
        else:
            status = "needs setup"
            hint = provider_setup_hint(
                canonical_id,
                env_vars=env_vars,
                oauth_supported=canonical_id in oauth_providers,
                requires_api_key=requires_key,
                base_url=runtime_base_url,
            )

        entries.append(
            ProviderSetupEntry(
                id=canonical_id,
                name=display_name,
                status=status,
                hint=hint,
            )
        )
    return entries


def format_provider_setup_entries(entries: list[ProviderSetupEntry]) -> str:
    if not entries:
        return "No supported providers found."

    lines = ["Supported providers:"]
    for entry in entries:
        lines.append(f"  {entry.id} ({entry.name}) — {entry.status}; {entry.hint}")
    lines.append("")
    lines.append("Use /models to browse models after a provider is configured.")
    return "\n".join(lines)
