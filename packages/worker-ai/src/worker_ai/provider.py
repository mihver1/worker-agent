"""Base provider interface and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from worker_ai.models import Message, ModelInfo, StreamEvent, ToolDef


class Provider(ABC):
    """Abstract base for all LLM providers."""

    name: str  # e.g. "anthropic", "openai", "kimi"

    def __init__(self, api_key: str | None = None, base_url: str | None = None, **kwargs: Any):
        self.api_key = api_key
        self.base_url = base_url

    @abstractmethod
    async def stream_chat(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[ToolDef] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion from the provider."""
        ...  # pragma: no cover
        # Needed so that the abstract method is recognized as an async generator.
        # Without a yield the type checker complains.
        if False:  # type: ignore[unreachable]  # noqa: SIM108
            yield  # type: ignore[misc]

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Return the list of known models for this provider."""
        ...

    async def close(self) -> None:
        """Release any resources (HTTP clients, etc.)."""


class ProviderRegistry:
    """Registry of all available providers.

    Usage:
        registry = ProviderRegistry()
        registry.register("anthropic", AnthropicProvider)
        provider = registry.create("anthropic", api_key="sk-...")
    """

    def __init__(self) -> None:
        self._factories: dict[str, type[Provider]] = {}

    def register(self, name: str, cls: type[Provider]) -> None:
        self._factories[name] = cls

    def create(self, name: str, **kwargs: Any) -> Provider:
        if name not in self._factories:
            available = ", ".join(sorted(self._factories)) or "(none)"
            msg = f"Unknown provider {name!r}. Available: {available}"
            raise ValueError(msg)
        return self._factories[name](**kwargs)

    @property
    def available(self) -> list[str]:
        return sorted(self._factories)
