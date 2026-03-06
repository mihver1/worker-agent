"""Built-in provider manifests for runtime resolution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Declarative description of a built-in provider integration."""

    id: str
    provider_type: str
    display_name: str
    env_vars: tuple[str, ...] = ()
    default_base_url: str = ""
    requires_api_key: bool = True
    catalog_id: str = ""
    aliases: tuple[str, ...] = ()
    direct_model_discovery: bool = False


_BUILTIN_PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        id="anthropic",
        provider_type="anthropic",
        display_name="Anthropic",
        env_vars=("ANTHROPIC_API_KEY",),
    ),
    "openai": ProviderSpec(
        id="openai",
        provider_type="openai",
        display_name="OpenAI",
        env_vars=("OPENAI_API_KEY",),
    ),
    "google": ProviderSpec(
        id="google",
        provider_type="google",
        display_name="Google",
        env_vars=("GEMINI_API_KEY",),
    ),
    "google_vertex": ProviderSpec(
        id="google_vertex",
        provider_type="google_vertex",
        display_name="Google Vertex AI",
        default_base_url="https://{location}-aiplatform.googleapis.com",
        requires_api_key=False,
        catalog_id="google",
        aliases=("google-vertex",),
    ),
    "vertex_anthropic": ProviderSpec(
        id="vertex_anthropic",
        provider_type="vertex_anthropic",
        display_name="Anthropic on Vertex AI",
        default_base_url="https://{location}-aiplatform.googleapis.com",
        requires_api_key=False,
        aliases=("anthropic_vertex", "google-vertex-anthropic"),
    ),
    "bedrock": ProviderSpec(
        id="bedrock",
        provider_type="bedrock",
        display_name="Amazon Bedrock",
        requires_api_key=False,
    ),
    "azure_openai": ProviderSpec(
        id="azure_openai",
        provider_type="azure_openai",
        display_name="Azure OpenAI",
        env_vars=("AZURE_OPENAI_API_KEY",),
    ),
    "github_copilot": ProviderSpec(
        id="github_copilot",
        provider_type="github_copilot",
        display_name="GitHub Copilot",
        env_vars=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
        default_base_url="https://api.githubcopilot.com",
        aliases=("github-copilot",),
    ),
    "github_copilot_enterprise": ProviderSpec(
        id="github_copilot_enterprise",
        provider_type="github_copilot",
        display_name="GitHub Copilot Enterprise",
        env_vars=("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"),
        default_base_url="https://api.githubcopilot.com",
        aliases=("github-copilot-enterprise",),
    ),
    "kimi": ProviderSpec(
        id="kimi",
        provider_type="kimi",
        display_name="Kimi For Coding",
        env_vars=("MOONSHOT_API_KEY",),
        default_base_url="https://api.kimi.com/coding/v1",
    ),
    "ollama": ProviderSpec(
        id="ollama",
        provider_type="ollama",
        display_name="Ollama",
        env_vars=("OLLAMA_API_KEY",),
        default_base_url="http://localhost:11434/v1",
        requires_api_key=False,
        direct_model_discovery=True,
    ),
    "ollama_cloud": ProviderSpec(
        id="ollama_cloud",
        provider_type="ollama",
        display_name="Ollama Cloud",
        env_vars=("OLLAMA_API_KEY",),
        default_base_url="https://ollama.com/v1",
        aliases=("ollama-cloud",),
        direct_model_discovery=True,
    ),
    "groq": ProviderSpec(
        id="groq",
        provider_type="openai_compat",
        display_name="Groq",
        env_vars=("GROQ_API_KEY",),
        default_base_url="https://api.groq.com/openai/v1",
    ),
    "mistral": ProviderSpec(
        id="mistral",
        provider_type="openai_compat",
        display_name="Mistral",
        env_vars=("MISTRAL_API_KEY",),
        default_base_url="https://api.mistral.ai/v1",
    ),
    "xai": ProviderSpec(
        id="xai",
        provider_type="openai_compat",
        display_name="xAI",
        env_vars=("XAI_API_KEY",),
        default_base_url="https://api.x.ai/v1",
    ),
    "openrouter": ProviderSpec(
        id="openrouter",
        provider_type="openai_compat",
        display_name="OpenRouter",
        env_vars=("OPENROUTER_API_KEY",),
        default_base_url="https://openrouter.ai/api/v1",
    ),
    "together": ProviderSpec(
        id="together",
        provider_type="openai_compat",
        display_name="Together AI",
        env_vars=("TOGETHER_API_KEY",),
        default_base_url="https://api.together.xyz/v1",
        catalog_id="togetherai",
        aliases=("togetherai",),
    ),
    "cerebras": ProviderSpec(
        id="cerebras",
        provider_type="openai_compat",
        display_name="Cerebras",
        env_vars=("CEREBRAS_API_KEY",),
        default_base_url="https://api.cerebras.ai/v1",
    ),
    "deepseek": ProviderSpec(
        id="deepseek",
        provider_type="openai_compat",
        display_name="DeepSeek",
        env_vars=("DEEPSEEK_API_KEY",),
        default_base_url="https://api.deepseek.com/v1",
    ),
    "lmstudio": ProviderSpec(
        id="lmstudio",
        provider_type="lmstudio",
        display_name="LM Studio",
        default_base_url="http://127.0.0.1:1234/v1",
        requires_api_key=False,
        aliases=("lm-studio",),
        direct_model_discovery=True,
    ),
    "302ai": ProviderSpec(
        id="302ai",
        provider_type="openai_compat",
        display_name="302.AI",
        env_vars=("302AI_API_KEY",),
        default_base_url="https://api.302.ai/v1",
        aliases=("302.ai",),
    ),
    "baseten": ProviderSpec(
        id="baseten",
        provider_type="openai_compat",
        display_name="Baseten",
        env_vars=("BASETEN_API_KEY",),
        default_base_url="https://inference.baseten.co/v1",
    ),
    "fireworks": ProviderSpec(
        id="fireworks",
        provider_type="openai_compat",
        display_name="Fireworks AI",
        env_vars=("FIREWORKS_API_KEY",),
        default_base_url="https://api.fireworks.ai/inference/v1",
        catalog_id="fireworks-ai",
        aliases=("fireworks-ai",),
    ),
    "helicone": ProviderSpec(
        id="helicone",
        provider_type="openai_compat",
        display_name="Helicone",
        env_vars=("HELICONE_API_KEY",),
        default_base_url="https://ai-gateway.helicone.ai/v1",
    ),
    "io-net": ProviderSpec(
        id="io-net",
        provider_type="openai_compat",
        display_name="IO.NET",
        env_vars=("IOINTELLIGENCE_API_KEY",),
        default_base_url="https://api.intelligence.io.solutions/api/v1",
        aliases=("io.net", "ionet"),
    ),
    "nebius": ProviderSpec(
        id="nebius",
        provider_type="openai_compat",
        display_name="Nebius",
        env_vars=("NEBIUS_API_KEY",),
        default_base_url="https://api.tokenfactory.nebius.com/v1",
    ),
    "llama.cpp": ProviderSpec(
        id="llama.cpp",
        provider_type="openai_compat",
        display_name="llama.cpp",
        default_base_url="http://localhost:8080/v1",
        requires_api_key=False,
        aliases=("llamacpp",),
    ),
}


def get_provider_spec(provider_id: str) -> ProviderSpec | None:
    """Return the built-in spec for *provider_id*, if one exists."""
    spec = _BUILTIN_PROVIDER_SPECS.get(provider_id)
    if spec is not None:
        return spec
    for candidate in _BUILTIN_PROVIDER_SPECS.values():
        if provider_id in candidate.aliases:
            return candidate
    return None


def iter_provider_specs() -> tuple[ProviderSpec, ...]:
    """Return all built-in provider specs in registration order."""
    return tuple(_BUILTIN_PROVIDER_SPECS.values())
