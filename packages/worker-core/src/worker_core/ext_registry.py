"""Extension registry — fetch, search, and cache entries from multiple registries."""

from __future__ import annotations

import hashlib
import json
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from worker_core.config import CONFIG_DIR, RegistryConfig

CACHE_DIR = CONFIG_DIR / "registry_cache"
CACHE_TTL_SECONDS = 3600  # 1 hour


@dataclass(slots=True)
class RegistryEntry:
    """A single extension listed in a registry."""

    name: str
    description: str = ""
    repo: str = ""
    tags: list[str] = field(default_factory=list)
    author: str = ""
    registry_name: str = ""


# ── Fetch ─────────────────────────────────────────────────────────


def _cache_path(url: str) -> Path:
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


def _read_cache(url: str) -> list[dict] | None:
    path = _cache_path(url)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) > CACHE_TTL_SECONDS:
            return None
        entries = data.get("entries")
        return entries if isinstance(entries, list) else None
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(url: str, entries: list[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"ts": time.time(), "entries": entries}
    _cache_path(url).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _parse_registry_response(url: str, content: bytes) -> list[dict]:
    """Parse registry content as TOML (preferred) or JSON (fallback)."""
    if url.endswith(".toml"):
        data = tomllib.loads(content.decode("utf-8"))
        entries = data.get("extensions", [])
    else:
        # Fallback: try JSON for third-party registries
        parsed = json.loads(content)
        entries = parsed if isinstance(parsed, list) else parsed.get("extensions", [])
    return entries if isinstance(entries, list) else []


def fetch_registry(url: str, *, timeout: int = 10, use_cache: bool = True) -> list[dict]:
    """Fetch a registry (TOML or JSON) from *url*, with optional local caching."""
    if use_cache:
        cached = _read_cache(url)
        if cached is not None:
            return cached

    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    entries = _parse_registry_response(url, resp.content)

    if use_cache:
        _write_cache(url, entries)
    return entries


# ── Search ────────────────────────────────────────────────────────


def _matches(entry: dict, query: str) -> bool:
    q = query.lower()
    if q in entry.get("name", "").lower():
        return True
    if q in entry.get("description", "").lower():
        return True
    return any(q in t.lower() for t in entry.get("tags", []))


def search_all(
    registries: list[RegistryConfig],
    query: str,
    *,
    timeout: int = 10,
    use_cache: bool = True,
) -> list[RegistryEntry]:
    """Search across all configured registries, return merged results."""
    results: list[RegistryEntry] = []
    for reg in registries:
        if not reg.url:
            continue
        try:
            raw = fetch_registry(reg.url, timeout=timeout, use_cache=use_cache)
        except Exception:
            continue
        for item in raw:
            if not _matches(item, query):
                continue
            results.append(
                RegistryEntry(
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    repo=item.get("repo", ""),
                    tags=item.get("tags", []),
                    author=item.get("author", ""),
                    registry_name=reg.name,
                )
            )
    return results


def list_all(
    registries: list[RegistryConfig],
    *,
    timeout: int = 10,
    use_cache: bool = True,
) -> list[RegistryEntry]:
    """Fetch all entries from all registries (no filter)."""
    results: list[RegistryEntry] = []
    for reg in registries:
        if not reg.url:
            continue
        try:
            raw = fetch_registry(reg.url, timeout=timeout, use_cache=use_cache)
        except Exception:
            continue
        for item in raw:
            results.append(
                RegistryEntry(
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    repo=item.get("repo", ""),
                    tags=item.get("tags", []),
                    author=item.get("author", ""),
                    registry_name=reg.name,
                )
            )
    return results


def invalidate_cache() -> None:
    """Remove all cached registry data."""
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.json"):
            f.unlink(missing_ok=True)
