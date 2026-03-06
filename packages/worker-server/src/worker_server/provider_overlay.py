"""Persistent provider overlay storage for server-imported credentials."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from worker_core.config import CONFIG_DIR, ProviderConfig, WorkerConfig

SERVER_PROVIDER_OVERLAY = CONFIG_DIR / "server-provider-overlay.json"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def load_provider_overlay(
    path: Path = SERVER_PROVIDER_OVERLAY,
) -> dict[str, ProviderConfig]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    providers = payload.get("providers", {})
    if not isinstance(providers, dict):
        return {}
    result: dict[str, ProviderConfig] = {}
    for provider_id, value in providers.items():
        if not isinstance(value, dict):
            continue
        result[provider_id] = ProviderConfig.model_validate(value)
    return result


def save_provider_overlay(
    overlay: dict[str, ProviderConfig],
    path: Path = SERVER_PROVIDER_OVERLAY,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "providers": {
            provider_id: config.model_dump(exclude_defaults=True, exclude_none=True)
            for provider_id, config in overlay.items()
        }
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def merge_provider_overlay(
    config: WorkerConfig,
    overlay: dict[str, ProviderConfig],
) -> None:
    for provider_id, provider_overlay in overlay.items():
        base_config = config.providers.get(provider_id)
        merged = (
            base_config.model_dump()
            if base_config is not None
            else ProviderConfig().model_dump()
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
    merged = (
        base_config.model_dump()
        if base_config is not None
        else ProviderConfig().model_dump()
    )
    _deep_merge(merged, provider_data)
    provider_config = ProviderConfig.model_validate(merged)
    overlay[provider_id] = provider_config
    return provider_config
