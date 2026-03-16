"""Persistent provider overlay storage for server-imported credentials."""

from __future__ import annotations

import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from worker_core.config import (
    SERVER_PROVIDER_OVERLAY_PATH,
    ProviderConfig,
    WorkerConfig,
    effective_server_provider_overlay_path,
)

SERVER_PROVIDER_OVERLAY = SERVER_PROVIDER_OVERLAY_PATH
_REJECTED_PLACEHOLDER_API_KEYS = frozenset({"sk-remote"})


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def is_rejected_placeholder_api_key(api_key: str | None) -> bool:
    if api_key is None:
        return False
    return api_key.strip() in _REJECTED_PLACEHOLDER_API_KEYS


def _overlay_payload_for_config(config: ProviderConfig) -> dict[str, Any]:
    return config.model_dump(exclude_defaults=True, exclude_none=True)


def _sanitize_provider_config(
    config: ProviderConfig,
) -> tuple[ProviderConfig, bool]:
    if not is_rejected_placeholder_api_key(config.api_key):
        return config, False
    return config.model_copy(update={"api_key": ""}), True


def load_provider_overlay(
    path: Path | None = None,
) -> dict[str, ProviderConfig]:
    resolved_path = path or effective_server_provider_overlay_path()
    if not resolved_path.exists():
        return {}
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    providers = payload.get("providers", {})
    if not isinstance(providers, dict):
        return {}
    result: dict[str, ProviderConfig] = {}
    sanitized = False
    for provider_id, value in providers.items():
        if not isinstance(value, dict):
            continue
        provider_config, was_sanitized = _sanitize_provider_config(
            ProviderConfig.model_validate(value)
        )
        if was_sanitized:
            sanitized = True
        if not _overlay_payload_for_config(provider_config):
            sanitized = True
            continue
        result[provider_id] = provider_config
    if sanitized:
        save_provider_overlay(result, path or SERVER_PROVIDER_OVERLAY)
    return result


def save_provider_overlay(
    overlay: dict[str, ProviderConfig],
    path: Path = SERVER_PROVIDER_OVERLAY,
) -> None:
    providers_payload: dict[str, dict[str, Any]] = {}
    for provider_id, config in overlay.items():
        sanitized_config, _ = _sanitize_provider_config(config)
        payload = _overlay_payload_for_config(sanitized_config)
        if payload:
            providers_payload[provider_id] = payload
    if not providers_payload:
        with suppress(FileNotFoundError):
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"providers": providers_payload}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def merge_provider_overlay(
    config: WorkerConfig,
    overlay: dict[str, ProviderConfig],
) -> None:
    for provider_id, provider_overlay in overlay.items():
        base_config = config.providers.get(provider_id)
        merged = (
            base_config.model_dump() if base_config is not None else ProviderConfig().model_dump()
        )
        _deep_merge(
            merged,
            provider_overlay.model_dump(exclude_defaults=True, exclude_none=True),
        )
        config.providers[provider_id] = ProviderConfig.model_validate(merged)


def upsert_provider_overlay(
    overlay: dict[str, ProviderConfig],
    provider_id: str,
    provider_data: dict[str, Any],
) -> ProviderConfig:
    base_config = overlay.get(provider_id)
    merged = base_config.model_dump() if base_config is not None else ProviderConfig().model_dump()
    _deep_merge(merged, provider_data)
    provider_config = ProviderConfig.model_validate(merged)
    provider_config, _ = _sanitize_provider_config(provider_config)
    if _overlay_payload_for_config(provider_config):
        overlay[provider_id] = provider_config
    else:
        overlay.pop(provider_id, None)
    return provider_config
