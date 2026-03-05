"""Centralized model catalog fetched from models.dev (like OpenCode).

Provides a unified list of models across all providers with metadata
(context window, tool support, pricing, etc.). Cached on disk to avoid
re-fetching on every startup.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("worker.models_catalog")

_MODELS_URL = "https://models.dev/api.json"
_CACHE_DIR = Path("~/.config/worker/cache").expanduser()
_CACHE_PATH = _CACHE_DIR / "models.json"
_CACHE_TTL = 3600  # 1 hour


@dataclass
class CatalogModel:
    """A model entry from the catalog."""

    id: str
    provider: str
    name: str
    context_window: int = 0
    max_output_tokens: int = 0
    supports_tools: bool = False
    supports_vision: bool = False
    supports_reasoning: bool = False
    input_price_per_m: float = 0.0
    output_price_per_m: float = 0.0


@dataclass
class CatalogProvider:
    """A provider entry from the catalog."""

    id: str
    name: str
    env: list[str]
    models: list[CatalogModel]


def _parse_provider(pid: str, raw: dict[str, Any]) -> CatalogProvider:
    """Parse a single provider entry from the raw API response."""
    models: list[CatalogModel] = []
    for mid, mraw in raw.get("models", {}).items():
        if not mraw.get("tool_call", False):
            continue  # Skip non-tool-capable models (embeddings, etc.)
        limit = mraw.get("limit", {})
        cost = mraw.get("cost", {})
        modalities = mraw.get("modalities", {})
        has_image = "image" in (modalities.get("input", []) or [])
        models.append(
            CatalogModel(
                id=mid,
                provider=pid,
                name=mraw.get("name", mid),
                context_window=limit.get("context", 0),
                max_output_tokens=limit.get("output", 0),
                supports_tools=mraw.get("tool_call", False),
                supports_vision=has_image,
                supports_reasoning=mraw.get("reasoning", False),
                input_price_per_m=cost.get("input", 0),
                output_price_per_m=cost.get("output", 0),
            )
        )
    return CatalogProvider(
        id=pid,
        name=raw.get("name", pid),
        env=raw.get("env", []),
        models=models,
    )


class ModelsCatalog:
    """Fetches and caches the models.dev catalog."""

    _data: dict[str, CatalogProvider] | None = None

    @classmethod
    def _read_cache(cls) -> dict[str, Any] | None:
        if not _CACHE_PATH.exists():
            return None
        try:
            stat = _CACHE_PATH.stat()
            if time.time() - stat.st_mtime > _CACHE_TTL:
                return None  # Expired
            return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    @classmethod
    def _write_cache(cls, data: dict[str, Any]) -> None:
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            _CACHE_PATH.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass

    @classmethod
    async def _fetch_raw(cls) -> dict[str, Any]:
        """Fetch from models.dev, falling back to cache."""
        # Try cache first
        cached = cls._read_cache()
        if cached is not None:
            return cached

        # Fetch from network
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(_MODELS_URL, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                cls._write_cache(data)
                return data
        except Exception as e:
            logger.warning("Failed to fetch models.dev: %s", e)
            # Try stale cache as last resort
            if _CACHE_PATH.exists():
                try:
                    return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass
            return {}

    @classmethod
    async def load(cls) -> dict[str, CatalogProvider]:
        """Load the full catalog (cached in memory after first call)."""
        if cls._data is not None:
            return cls._data
        raw = await cls._fetch_raw()
        cls._data = {}
        for pid, praw in raw.items():
            if isinstance(praw, dict) and "models" in praw:
                cls._data[pid] = _parse_provider(pid, praw)
        return cls._data

    @classmethod
    async def refresh(cls) -> dict[str, CatalogProvider]:
        """Force re-fetch from network."""
        cls._data = None
        # Invalidate cache
        try:
            _CACHE_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return await cls.load()

    @classmethod
    async def list_providers(cls) -> list[CatalogProvider]:
        """List all providers that have at least one model."""
        catalog = await cls.load()
        return [p for p in catalog.values() if p.models]

    @classmethod
    async def list_models(
        cls, provider_id: str | None = None
    ) -> list[CatalogModel]:
        """List models, optionally filtered by provider."""
        catalog = await cls.load()
        if provider_id:
            prov = catalog.get(provider_id)
            return prov.models if prov else []
        result: list[CatalogModel] = []
        for prov in catalog.values():
            result.extend(prov.models)
        return result

    @classmethod
    async def get_model(
        cls, provider_id: str, model_id: str
    ) -> CatalogModel | None:
        """Get a specific model by provider and model ID."""
        catalog = await cls.load()
        prov = catalog.get(provider_id)
        if not prov:
            return None
        for m in prov.models:
            if m.id == model_id:
                return m
        return None
