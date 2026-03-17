"""Minimal backend-store models for the Artel web surface."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class WebBackendEntry:
    name: str = ""
    url: str = ""
    kind: str = ""


__all__ = ["WebBackendEntry"]
