"""Built-in LLM providers."""

from artel_ai.provider import ProviderRegistry


def create_default_registry() -> ProviderRegistry:
    """Create a registry pre-populated with all built-in providers."""
    from artel_ai.providers.anthropic import AnthropicProvider
    from artel_ai.providers.anthropic_vertex import AnthropicVertexProvider
    from artel_ai.providers.azure_openai import AzureOpenAIProvider
    from artel_ai.providers.bedrock import BedrockProvider
    from artel_ai.providers.github_copilot import GitHubCopilotProvider
    from artel_ai.providers.google import GoogleProvider, GoogleVertexProvider
    from artel_ai.providers.kimi import KimiProvider
    from artel_ai.providers.lmstudio import LMStudioProvider
    from artel_ai.providers.ollama import OllamaProvider
    from artel_ai.providers.openai_compat import (
        OpenAICompatibleProvider,
        OpenAIProvider,
    )

    registry = ProviderRegistry()
    registry.register("anthropic", AnthropicProvider)
    registry.register("openai", OpenAIProvider)
    registry.register("openai_compat", OpenAICompatibleProvider)
    registry.register("google", GoogleProvider)
    registry.register("google_vertex", GoogleVertexProvider)
    registry.register("vertex_anthropic", AnthropicVertexProvider)
    registry.register("bedrock", BedrockProvider)
    registry.register("azure_openai", AzureOpenAIProvider)
    registry.register("github_copilot", GitHubCopilotProvider)
    registry.register("kimi", KimiProvider)
    registry.register("lmstudio", LMStudioProvider)
    registry.register("ollama", OllamaProvider)
    return registry
