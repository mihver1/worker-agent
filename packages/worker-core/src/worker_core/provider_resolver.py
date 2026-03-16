"""Provider manifest resolution and effective catalog composition."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from worker_ai.models import ModelInfo
from worker_ai.models_catalog import ModelsCatalog
from worker_ai.provider_specs import (
    ProviderSpec,
    get_provider_spec,
    iter_provider_specs,
)
from worker_ai.providers import create_default_registry

from worker_core.config import ProviderConfig, ProviderModelConfig, WorkerConfig

_SUPPORTED_PROVIDER_TYPES = frozenset(create_default_registry().available)


@dataclass(frozen=True, slots=True)
class EffectiveProviderCatalog:
    """Provider entry after merging manifests, models.dev data, and config overrides."""

    id: str
    name: str
    env: tuple[str, ...]
    models: tuple[ModelInfo, ...]


def get_provider_config(config: WorkerConfig, provider_name: str) -> ProviderConfig | None:
    """Return provider config for *provider_name*, resolving aliases to canonical IDs."""
    provider_config = config.providers.get(provider_name)
    if provider_config is not None:
        return provider_config
    spec = get_provider_spec(provider_name)
    if spec is not None and spec.id != provider_name:
        return config.providers.get(spec.id)
    return None


def _catalog_provider_name(provider_name: str, spec: ProviderSpec | None) -> str:
    if spec is not None and spec.catalog_id:
        return spec.catalog_id
    if spec is not None:
        return spec.id
    return provider_name


def _resolve_provider_type(
    provider_name: str,
    provider_config: ProviderConfig | None,
    spec: ProviderSpec | None,
) -> str:
    if provider_config and provider_config.type:
        return provider_config.type
    if spec is not None:
        return spec.provider_type
    if provider_config and provider_config.base_url:
        return "openai_compat"
    return provider_name


def get_provider_env_vars(config: WorkerConfig, provider_name: str) -> list[str]:
    """Return configured env vars for provider credential lookup."""
    provider_config = get_provider_config(config, provider_name)
    if provider_config and provider_config.env:
        return list(provider_config.env)
    spec = get_provider_spec(provider_name)
    if spec is not None and spec.env_vars:
        return list(spec.env_vars)
    return []


def resolve_provider_runtime_config(
    config: WorkerConfig,
    provider_name: str,
) -> tuple[str, dict[str, Any]]:
    """Resolve the effective runtime provider type and constructor kwargs."""
    provider_config = get_provider_config(config, provider_name)
    spec = get_provider_spec(provider_name)
    provider_type = _resolve_provider_type(provider_name, provider_config, spec)
    kwargs: dict[str, Any] = dict(provider_config.options) if provider_config else {}

    if provider_config:
        if provider_config.headers:
            existing_headers = kwargs.get("headers")
            merged_headers = dict(existing_headers) if isinstance(existing_headers, dict) else {}
            merged_headers.update(provider_config.headers)
            kwargs["headers"] = merged_headers
        if provider_config.timeout is not None:
            kwargs["timeout"] = provider_config.timeout
        for field_name in (
            "api_type",
            "region",
            "profile",
            "api_version",
            "project",
            "location",
        ):
            value = getattr(provider_config, field_name, "")
            if value:
                kwargs[field_name] = value

    base_url = ""
    if provider_config and provider_config.base_url:
        base_url = provider_config.base_url
    elif spec is not None and spec.default_base_url:
        base_url = spec.default_base_url
    if base_url:
        kwargs["base_url"] = base_url

    return provider_type, kwargs


def provider_requires_api_key(config: WorkerConfig, provider_name: str) -> bool:
    """Return whether the effective provider requires explicit credentials."""
    provider_config = get_provider_config(config, provider_name)
    if provider_config and provider_config.requires_api_key is not None:
        return provider_config.requires_api_key

    spec = get_provider_spec(provider_name)
    if spec is not None:
        return spec.requires_api_key

    provider_type, _ = resolve_provider_runtime_config(config, provider_name)
    return provider_type != "ollama"


def provider_is_supported(config: WorkerConfig, provider_name: str) -> bool:
    """Return whether the effective provider type can be created today."""
    provider_type, _ = resolve_provider_runtime_config(config, provider_name)
    return provider_type in _SUPPORTED_PROVIDER_TYPES


def _catalog_model_to_model_info(provider_id: str, model: Any) -> ModelInfo:
    return ModelInfo(
        id=model.id,
        provider=provider_id,
        name=model.name,
        context_window=model.context_window,
        max_output_tokens=model.max_output_tokens,
        supports_tools=model.supports_tools,
        supports_vision=model.supports_vision,
        supports_reasoning=model.supports_reasoning,
        input_price_per_m=model.input_price_per_m,
        output_price_per_m=model.output_price_per_m,
    )


def _canonical_provider_id(config: WorkerConfig, provider_name: str) -> str:
    normalized = str(provider_name or "").strip()
    if not normalized:
        return normalized
    if normalized in config.providers:
        return normalized
    spec = get_provider_spec(normalized)
    if spec is not None:
        return spec.id
    return normalized


async def _builtin_models_from_provider(
    provider_type: str,
    provider_id: str,
    kwargs: dict[str, Any],
    *,
    api_key: str | None = None,
    direct_discovery: bool = False,
) -> dict[str, ModelInfo]:
    registry = create_default_registry()
    if provider_type not in registry.available:
        return {}
    provider = registry.create(provider_type, api_key=api_key, **kwargs)
    try:
        if direct_discovery:
            models = await provider.list_models_direct()
            return {
                model.id: model.model_copy(update={"provider": provider_id}) for model in models
            }
        return {
            model.id: model.model_copy(update={"provider": provider_id})
            for model in provider.list_models()
        }
    finally:
        await provider.close()


def _resolve_api_key_for_discovery(config: WorkerConfig, provider_name: str) -> str | None:
    provider_config = get_provider_config(config, provider_name)
    if provider_config and provider_config.api_key:
        return provider_config.api_key
    for env_var in get_provider_env_vars(config, provider_name):
        value = os.environ.get(env_var)
        if value:
            return value
    return None


def _supports_direct_model_discovery(provider_type: str, spec: ProviderSpec | None) -> bool:
    if spec is not None and spec.direct_model_discovery:
        return True
    return provider_type == "azure_openai"


def _apply_model_override(
    base: ModelInfo,
    override: ProviderModelConfig,
    provider_id: str,
) -> ModelInfo:
    return base.model_copy(
        update={
            "provider": provider_id,
            "name": override.name if override.name is not None else base.name,
            "context_window": (
                override.context_window
                if override.context_window is not None
                else base.context_window
            ),
            "max_output_tokens": (
                override.max_output_tokens
                if override.max_output_tokens is not None
                else base.max_output_tokens
            ),
            "supports_tools": (
                override.supports_tools
                if override.supports_tools is not None
                else base.supports_tools
            ),
            "supports_vision": (
                override.supports_vision
                if override.supports_vision is not None
                else base.supports_vision
            ),
            "supports_reasoning": (
                override.supports_reasoning
                if override.supports_reasoning is not None
                else base.supports_reasoning
            ),
            "input_price_per_m": (
                override.input_price_per_m
                if override.input_price_per_m is not None
                else base.input_price_per_m
            ),
            "output_price_per_m": (
                override.output_price_per_m
                if override.output_price_per_m is not None
                else base.output_price_per_m
            ),
        }
    )


def _model_from_override(
    provider_id: str,
    model_id: str,
    override: ProviderModelConfig,
) -> ModelInfo:
    return ModelInfo(
        id=model_id,
        provider=provider_id,
        name=override.name or model_id,
        context_window=override.context_window or 0,
        max_output_tokens=override.max_output_tokens or 0,
        supports_tools=(override.supports_tools if override.supports_tools is not None else True),
        supports_vision=(
            override.supports_vision if override.supports_vision is not None else False
        ),
        supports_reasoning=(
            override.supports_reasoning if override.supports_reasoning is not None else False
        ),
        input_price_per_m=override.input_price_per_m or 0.0,
        output_price_per_m=override.output_price_per_m or 0.0,
    )


async def get_effective_provider_catalog(
    config: WorkerConfig,
) -> dict[str, EffectiveProviderCatalog]:
    """Return the merged provider catalog for currently supported/configured providers."""
    raw_catalog = await ModelsCatalog.load()
    provider_order = [spec.id for spec in iter_provider_specs()]
    for provider_id in config.providers:
        if provider_id not in provider_order:
            provider_order.append(provider_id)

    result: dict[str, EffectiveProviderCatalog] = {}
    for provider_id in provider_order:
        if not provider_is_supported(config, provider_id):
            continue

        provider_config = config.providers.get(provider_id)
        spec = get_provider_spec(provider_id)
        catalog_provider = raw_catalog.get(_catalog_provider_name(provider_id, spec))
        models_by_id: dict[str, ModelInfo] = {}
        provider_type, kwargs = resolve_provider_runtime_config(config, provider_id)

        direct_models: dict[str, ModelInfo] = {}
        if _supports_direct_model_discovery(provider_type, spec):
            api_key = _resolve_api_key_for_discovery(config, provider_id)
            if api_key is not None or not provider_requires_api_key(config, provider_id):
                direct_models = await _builtin_models_from_provider(
                    provider_type,
                    provider_id,
                    kwargs,
                    api_key=api_key,
                    direct_discovery=True,
                )

        if direct_models:
            models_by_id.update(direct_models)
        elif catalog_provider:
            for model in catalog_provider.models:
                models_by_id[model.id] = _catalog_model_to_model_info(provider_id, model)
        else:
            models_by_id.update(
                await _builtin_models_from_provider(provider_type, provider_id, kwargs)
            )

        if provider_config:
            for model_id, override in provider_config.models.items():
                if override.disabled:
                    models_by_id.pop(model_id, None)
                    continue
                base_model = models_by_id.get(model_id)
                if base_model is None:
                    models_by_id[model_id] = _model_from_override(provider_id, model_id, override)
                else:
                    models_by_id[model_id] = _apply_model_override(
                        base_model,
                        override,
                        provider_id,
                    )

            if provider_config.whitelist:
                allowed = set(provider_config.whitelist)
                models_by_id = {
                    model_id: model
                    for model_id, model in models_by_id.items()
                    if model_id in allowed
                }
            if provider_config.blacklist:
                blocked = set(provider_config.blacklist)
                models_by_id = {
                    model_id: model
                    for model_id, model in models_by_id.items()
                    if model_id not in blocked
                }

        if not models_by_id:
            continue

        name = (
            provider_config.name
            if provider_config and provider_config.name
            else (
                catalog_provider.name
                if catalog_provider
                else (spec.display_name if spec is not None else provider_id)
            )
        )
        env = tuple(get_provider_env_vars(config, provider_id))
        result[provider_id] = EffectiveProviderCatalog(
            id=provider_id,
            name=name,
            env=env,
            models=tuple(sorted(models_by_id.values(), key=lambda model: model.id)),
        )

    return result


async def get_effective_model_info(
    config: WorkerConfig,
    provider_name: str,
    model_id: str,
) -> ModelInfo | None:
    """Return merged model metadata for *provider_name/model_id*."""
    canonical_provider = _canonical_provider_id(config, provider_name)
    provider_config = get_provider_config(config, canonical_provider)
    spec = get_provider_spec(canonical_provider)
    provider_type, kwargs = resolve_provider_runtime_config(config, canonical_provider)

    if _supports_direct_model_discovery(provider_type, spec):
        api_key = _resolve_api_key_for_discovery(config, canonical_provider)
        if api_key is not None or not provider_requires_api_key(config, canonical_provider):
            direct_models = await _builtin_models_from_provider(
                provider_type,
                canonical_provider,
                kwargs,
                api_key=api_key,
                direct_discovery=True,
            )
            direct_model = direct_models.get(model_id)
            if direct_model is not None:
                override = provider_config.models.get(model_id) if provider_config else None
                if override is None:
                    return direct_model
                if override.disabled:
                    return None
                return _apply_model_override(direct_model, override, canonical_provider)

    raw_catalog = await ModelsCatalog.load()
    catalog_provider = raw_catalog.get(_catalog_provider_name(canonical_provider, spec))
    if catalog_provider is not None:
        for model in catalog_provider.models:
            if model.id == model_id:
                base_model = _catalog_model_to_model_info(canonical_provider, model)
                override = provider_config.models.get(model_id) if provider_config else None
                if override is None:
                    return base_model
                if override.disabled:
                    return None
                return _apply_model_override(base_model, override, canonical_provider)

    builtin_models = await _builtin_models_from_provider(
        provider_type,
        canonical_provider,
        kwargs,
    )
    base_model = builtin_models.get(model_id)
    if base_model is not None:
        override = provider_config.models.get(model_id) if provider_config else None
        if override is None:
            return base_model
        if override.disabled:
            return None
        return _apply_model_override(base_model, override, canonical_provider)

    if provider_config is None:
        return None
    override = provider_config.models.get(model_id)
    if override is None or override.disabled:
        return None
    return _model_from_override(canonical_provider, model_id, override)
