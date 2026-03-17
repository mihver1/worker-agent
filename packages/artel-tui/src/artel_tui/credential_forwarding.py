"""Portable credential forwarding helpers for remote connect mode."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

from artel_ai.oauth import (
    get_oauth_provider,
    is_github_copilot_provider,
    resolve_github_copilot_token,
)
from artel_ai.provider_specs import get_provider_spec
from artel_core.bootstrap import provider_requires_api_key
from artel_core.provider_resolver import get_provider_config, get_provider_env_vars
from artel_core.provider_setup import provider_ids_for_listing


def _canonical_provider_id(name: str) -> str:
    spec = get_provider_spec(name)
    if spec is not None:
        return spec.id
    return name


def _looks_local_base_url(base_url: str) -> bool:
    return base_url.startswith("http://localhost") or base_url.startswith("http://127.0.0.1")


def parse_forward_credentials_spec(spec: str, config: Any) -> list[str]:
    raw = spec.strip()
    if not raw:
        return []
    if raw == "all":
        return provider_ids_for_listing(config)

    provider_ids: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        provider_id = _canonical_provider_id(item.strip())
        if not provider_id or provider_id in seen:
            continue
        seen.add(provider_id)
        provider_ids.append(provider_id)
    return provider_ids


def _exportable_provider_settings(config: Any, provider_name: str) -> dict[str, Any]:
    provider_config = get_provider_config(config, provider_name)
    if provider_config is None:
        return {}
    data = provider_config.model_dump(exclude_defaults=True, exclude_none=True)
    data.pop("api_key", None)
    data.pop("env", None)
    return data


def _effective_base_url(config: Any, provider_name: str) -> str:
    provider_config = get_provider_config(config, provider_name)
    if provider_config and provider_config.base_url:
        return provider_config.base_url
    spec = get_provider_spec(provider_name)
    if spec is not None:
        return spec.default_base_url
    return ""


@dataclass(frozen=True, slots=True)
class ForwardCredentialSkip:
    provider: str
    reason: str


async def collect_forward_credentials(
    spec: str,
    config: Any,
) -> tuple[list[dict[str, Any]], list[ForwardCredentialSkip]]:
    exports: list[dict[str, Any]] = []
    skipped: list[ForwardCredentialSkip] = []

    for provider_id in parse_forward_credentials_spec(spec, config):
        export = await _collect_provider_export(config, provider_id)
        if export is None:
            skipped.append(
                ForwardCredentialSkip(
                    provider=provider_id,
                    reason="No portable credentials found for this provider.",
                )
            )
            continue
        if isinstance(export, ForwardCredentialSkip):
            skipped.append(export)
            continue
        exports.append(export)
    return exports, skipped


async def _collect_provider_export(
    config: Any,
    provider_name: str,
) -> dict[str, Any] | ForwardCredentialSkip | None:
    provider_id = _canonical_provider_id(provider_name)
    settings = _exportable_provider_settings(config, provider_id)
    base_url = _effective_base_url(config, provider_id)
    if base_url and _looks_local_base_url(base_url):
        return ForwardCredentialSkip(
            provider=provider_id,
            reason="Local base_url cannot be forwarded to a remote server.",
        )

    provider_config = get_provider_config(config, provider_id)
    if provider_config and provider_config.api_key:
        return {
            "provider": provider_id,
            "settings": settings,
            "auth": {"kind": "api_key", "api_key": provider_config.api_key},
        }

    for env_var in get_provider_env_vars(config, provider_id):
        value = os.environ.get(env_var)
        if value:
            return {
                "provider": provider_id,
                "settings": settings,
                "auth": {"kind": "api_key", "api_key": value},
            }

    oauth = get_oauth_provider(provider_id, config=config)
    if oauth is not None:
        token = await oauth.get_token()
        if token is not None:
            return {
                "provider": provider_id,
                "settings": settings,
                "auth": {"kind": "oauth_token", "token": asdict(token)},
            }

    if is_github_copilot_provider(provider_id):
        token = await resolve_github_copilot_token(config, provider_id)
        if token:
            return {
                "provider": provider_id,
                "settings": settings,
                "auth": {"kind": "api_key", "api_key": token},
            }

    if not provider_requires_api_key(config, provider_id):
        return ForwardCredentialSkip(
            provider=provider_id,
            reason="This provider relies on host-local services or ambient credentials.",
        )
    return None
