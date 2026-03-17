"""Administrative helpers for extension management across CLI/web surfaces."""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from typing import Any

import tomli_w

from artel_core import ext_manifest
from artel_core.builtin_capabilities import load_builtin_capabilities
from artel_core.config import GLOBAL_CONFIG, load_config
from artel_core.ext_registry import list_all, search_all
from artel_core.extensions import discover_extensions


@dataclass(slots=True)
class ExtensionInfo:
    name: str
    version: str
    source: str = ""


@dataclass(slots=True)
class RegistryInfo:
    name: str
    url: str


@dataclass(slots=True)
class RegistrySearchResult:
    name: str
    description: str
    repo: str
    author: str
    registry_name: str
    tags: list[str]


def list_installed_extensions() -> list[ExtensionInfo]:
    manifest_sources = {entry.name: entry.source for entry in ext_manifest.list_entries()}
    discovered = [
        ExtensionInfo(
            name=name,
            version=str(getattr(cls, "version", "?")),
            source=manifest_sources.get(name, ""),
        )
        for name, cls in sorted(discover_extensions().items())
    ]
    bundled = [
        ExtensionInfo(name=name, version="bundled", source="bundled")
        for name in sorted(load_builtin_capabilities())
    ]
    return [*bundled, *discovered]


def install_extension(source: str) -> tuple[bool, str]:
    from artel_core.cli import _parse_installed_package_name, _resolve_install_source

    install_source = _resolve_install_source(source)
    try:
        result = subprocess.run(
            ["uv", "pip", "install", "--no-sources", install_source],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False, "Error: 'uv' not found. Install it first: https://docs.astral.sh/uv/"
    if result.returncode != 0:
        return False, result.stderr.strip() or "Install failed."
    pkg_name = _parse_installed_package_name(result.stdout, install_source)
    ext_manifest.add(pkg_name, install_source)
    return True, f"Extension installed: {pkg_name}"


def remove_extension(name: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["uv", "pip", "uninstall", name],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False, "Error: 'uv' not found."
    if result.returncode != 0:
        return False, result.stderr.strip() or "Remove failed."
    ext_manifest.remove(name)
    return True, f"Extension '{name}' removed."


def update_extension(name: str) -> tuple[bool, str]:
    entry = next((entry for entry in ext_manifest.list_entries() if entry.name == name), None)
    source = entry.source if entry else name
    try:
        result = subprocess.run(
            ["uv", "pip", "install", "--no-sources", "--upgrade", source],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False, "Error: 'uv' not found."
    if result.returncode != 0:
        return False, result.stderr.strip() or "Update failed."
    return True, f"Extension '{name}' updated."


def update_all_extensions() -> list[tuple[str, bool, str]]:
    manifest_entries = {entry.name: entry for entry in ext_manifest.list_entries()}
    results: list[tuple[str, bool, str]] = []
    for ext_name in sorted(discover_extensions()):
        entry = manifest_entries.get(ext_name)
        source = entry.source if entry else ext_name
        try:
            result = subprocess.run(
                ["uv", "pip", "install", "--no-sources", "--upgrade", source],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            results.append((ext_name, False, "Error: 'uv' not found."))
            continue
        if result.returncode == 0:
            results.append((ext_name, True, "updated"))
        else:
            results.append((ext_name, False, result.stderr.strip() or "Update failed."))
    return results


def search_extensions(project_dir: str, query: str) -> list[RegistrySearchResult]:
    config = load_config(project_dir)
    matches = search_all(config.extensions.registries, query)
    return [
        RegistrySearchResult(
            name=item.name,
            description=item.description,
            repo=item.repo,
            author=item.author,
            registry_name=item.registry_name,
            tags=list(item.tags),
        )
        for item in matches
    ]


def list_registry_entries(project_dir: str) -> list[RegistryInfo]:
    config = load_config(project_dir)
    return [RegistryInfo(name=reg.name, url=reg.url) for reg in config.extensions.registries]


def add_registry(name: str, url: str) -> tuple[bool, str]:
    data: dict[str, Any] = {}
    if GLOBAL_CONFIG.exists():
        with open(GLOBAL_CONFIG, "rb") as file:
            data = tomllib.load(file)
    ext_section = data.setdefault("extensions", {})
    registries = ext_section.setdefault("registries", [])
    for registry in registries:
        if registry.get("name") == name:
            return False, f"Registry '{name}' already exists. Remove it first."
    registries.append({"name": name, "url": url})
    GLOBAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_CONFIG.write_text(tomli_w.dumps(data), encoding="utf-8")
    return True, f"Registry '{name}' added: {url}"


def remove_registry(name: str) -> tuple[bool, str]:
    if name == "official":
        return False, "Cannot remove the built-in 'official' registry."
    data: dict[str, Any] = {}
    if GLOBAL_CONFIG.exists():
        with open(GLOBAL_CONFIG, "rb") as file:
            data = tomllib.load(file)
    ext_section = data.get("extensions", {})
    registries = ext_section.get("registries", [])
    new_regs = [registry for registry in registries if registry.get("name") != name]
    if len(new_regs) == len(registries):
        return False, f"Registry '{name}' not found."
    ext_section["registries"] = new_regs
    data["extensions"] = ext_section
    GLOBAL_CONFIG.write_text(tomli_w.dumps(data), encoding="utf-8")
    return True, f"Registry '{name}' removed."


def discover_registry_catalog(project_dir: str) -> list[RegistrySearchResult]:
    config = load_config(project_dir)
    entries = list_all(config.extensions.registries)
    return [
        RegistrySearchResult(
            name=item.name,
            description=item.description,
            repo=item.repo,
            author=item.author,
            registry_name=item.registry_name,
            tags=list(item.tags),
        )
        for item in entries
    ]


__all__ = [
    "ExtensionInfo",
    "RegistryInfo",
    "RegistrySearchResult",
    "add_registry",
    "discover_registry_catalog",
    "install_extension",
    "list_installed_extensions",
    "list_registry_entries",
    "remove_extension",
    "remove_registry",
    "search_extensions",
    "update_all_extensions",
    "update_extension",
]
