"""Built-in LLM providers."""

from worker_ai.provider import ProviderRegistry


def create_default_registry() -> ProviderRegistry:
    """Create a registry pre-populated with all built-in providers."""
    from worker_ai.providers.anthropic import AnthropicProvider
    from worker_ai.providers.anthropic_vertex import AnthropicVertexProvider
    from worker_ai.providers.azure_openai import AzureOpenAIProvider
    from worker_ai.providers.bedrock import BedrockProvider
    from worker_ai.providers.github_copilot import GitHubCopilotProvider
    from worker_ai.providers.google import GoogleProvider, GoogleVertexProvider
    from worker_ai.providers.kimi import KimiProvider
    from worker_ai.providers.lmstudio import LMStudioProvider
    from worker_ai.providers.ollama import OllamaProvider
    from worker_ai.providers.openai_compat import (
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
