"""Built-in LLM providers."""

from worker_ai.provider import ProviderRegistry


def create_default_registry() -> ProviderRegistry:
    """Create a registry pre-populated with all built-in providers."""
    from worker_ai.providers.anthropic import AnthropicProvider
    from worker_ai.providers.openai_compat import OpenAICompatProvider
    from worker_ai.providers.google import GoogleProvider
    from worker_ai.providers.kimi import KimiProvider
    from worker_ai.providers.ollama import OllamaProvider

    registry = ProviderRegistry()
    registry.register("anthropic", AnthropicProvider)
    registry.register("openai", OpenAICompatProvider)
    registry.register("openai_compat", OpenAICompatProvider)
    registry.register("google", GoogleProvider)
    registry.register("kimi", KimiProvider)
    registry.register("ollama", OllamaProvider)
    return registry
