"""Extension manifest — persists installed extension sources.

The manifest lives in CONFIG_DIR/extensions.lock (JSON) so that it
survives destructive updates of the install directory (rm -rf + uv sync).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from artel_core.config import CONFIG_DIR

MANIFEST_PATH = CONFIG_DIR / "extensions.lock"


@dataclass(slots=True)
class ManifestEntry:
    """Single installed extension record."""

    name: str
    source: str  # original install source (git URL, PyPI name, local path, …)


def _read_raw() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    try:
        data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _write_raw(entries: list[dict]) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def list_entries() -> list[ManifestEntry]:
    """Return all manifest entries."""
    return [
        ManifestEntry(name=e["name"], source=e["source"])
        for e in _read_raw()
        if "name" in e and "source" in e
    ]


def add(name: str, source: str) -> None:
    """Add or update an extension in the manifest."""
    entries = _read_raw()
    # Replace existing entry with the same name
    entries = [e for e in entries if e.get("name") != name]
    entries.append(asdict(ManifestEntry(name=name, source=source)))
    _write_raw(entries)


def remove(name: str) -> bool:
    """Remove an extension from the manifest. Returns True if it was present."""
    entries = _read_raw()
    filtered = [e for e in entries if e.get("name") != name]
    if len(filtered) == len(entries):
        return False
    _write_raw(filtered)
    return True
