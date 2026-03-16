"""Configuration system — Pydantic models, TOML loading, template generation."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, Field

# ── Config paths ──────────────────────────────────────────────────

APP_NAME = "artel"
LEGACY_APP_NAME = "worker"
PROJECT_DIR_NAME = ".artel"
LEGACY_PROJECT_DIR_NAME = ".worker"
CONFIG_DIR_ENV = "ARTEL_CONFIG_DIR"
LEGACY_CONFIG_DIR_ENV = "WORKER_CONFIG_DIR"


def _resolve_config_dir(*, env_names: tuple[str, ...], default_name: str) -> Path:
    for env_name in env_names:
        value = os.environ.get(env_name, "").strip()
        if value:
            return Path(value).expanduser()
    return Path(f"~/.config/{default_name}").expanduser()


CONFIG_DIR = _resolve_config_dir(
    env_names=(CONFIG_DIR_ENV, LEGACY_CONFIG_DIR_ENV),
    default_name=APP_NAME,
)
LEGACY_CONFIG_DIR = _resolve_config_dir(
    env_names=(LEGACY_CONFIG_DIR_ENV,),
    default_name=LEGACY_APP_NAME,
)
GLOBAL_CONFIG = CONFIG_DIR / "config.toml"
LEGACY_GLOBAL_CONFIG = LEGACY_CONFIG_DIR / "config.toml"
AUTH_FILE = CONFIG_DIR / "auth.json"
LEGACY_AUTH_FILE = LEGACY_CONFIG_DIR / "auth.json"
SESSIONS_DB = CONFIG_DIR / "sessions.db"
LEGACY_SESSIONS_DB = LEGACY_CONFIG_DIR / "sessions.db"
GLOBAL_AGENTS_FILE = CONFIG_DIR / "AGENTS.md"
LEGACY_GLOBAL_AGENTS_FILE = LEGACY_CONFIG_DIR / "AGENTS.md"
GLOBAL_SYSTEM_OVERRIDE = CONFIG_DIR / "SYSTEM.md"
LEGACY_GLOBAL_SYSTEM_OVERRIDE = LEGACY_CONFIG_DIR / "SYSTEM.md"
GLOBAL_APPEND_SYSTEM = CONFIG_DIR / "APPEND_SYSTEM.md"
LEGACY_GLOBAL_APPEND_SYSTEM = LEGACY_CONFIG_DIR / "APPEND_SYSTEM.md"
PROMPTS_DIR = CONFIG_DIR / "prompts"
LEGACY_PROMPTS_DIR = LEGACY_CONFIG_DIR / "prompts"
SKILLS_DIR = CONFIG_DIR / "skills"
LEGACY_SKILLS_DIR = LEGACY_CONFIG_DIR / "skills"
EXTENSIONS_MANIFEST = CONFIG_DIR / "extensions.lock"
LEGACY_EXTENSIONS_MANIFEST = LEGACY_CONFIG_DIR / "extensions.lock"
REGISTRY_CACHE_DIR = CONFIG_DIR / "registry_cache"
LEGACY_REGISTRY_CACHE_DIR = LEGACY_CONFIG_DIR / "registry_cache"
SERVER_PROVIDER_OVERLAY_PATH = CONFIG_DIR / "server-provider-overlay.json"
LEGACY_SERVER_PROVIDER_OVERLAY_PATH = LEGACY_CONFIG_DIR / "server-provider-overlay.json"
GLOBAL_MCP_PATH = CONFIG_DIR / "mcp.json"
LEGACY_GLOBAL_MCP_PATH = LEGACY_CONFIG_DIR / "mcp.json"
GLOBAL_STATE_FILE = CONFIG_DIR / "state.json"
LEGACY_GLOBAL_STATE_FILE = LEGACY_CONFIG_DIR / "state.json"


def _first_existing_path(*paths: Path) -> Path | None:
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        if path.exists():
            return path
    return None


def _dedupe_paths(*paths: Path) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def project_state_dir(project_dir: str) -> Path:
    return Path(project_dir) / PROJECT_DIR_NAME


def legacy_project_state_dir(project_dir: str) -> Path:
    return Path(project_dir) / LEGACY_PROJECT_DIR_NAME


def project_config_path(project_dir: str) -> Path:
    return project_state_dir(project_dir) / "config.toml"


def legacy_project_config_path(project_dir: str) -> Path:
    return legacy_project_state_dir(project_dir) / "config.toml"


def project_agents_path(project_dir: str) -> Path:
    return project_state_dir(project_dir) / "AGENTS.md"


def legacy_project_agents_path(project_dir: str) -> Path:
    return legacy_project_state_dir(project_dir) / "AGENTS.md"


def project_system_override_path(project_dir: str) -> Path:
    return project_state_dir(project_dir) / "SYSTEM.md"


def legacy_project_system_override_path(project_dir: str) -> Path:
    return legacy_project_state_dir(project_dir) / "SYSTEM.md"


def project_append_system_path(project_dir: str) -> Path:
    return project_state_dir(project_dir) / "APPEND_SYSTEM.md"


def legacy_project_append_system_path(project_dir: str) -> Path:
    return legacy_project_state_dir(project_dir) / "APPEND_SYSTEM.md"


def project_prompts_path(project_dir: str) -> Path:
    return project_state_dir(project_dir) / "prompts"


def legacy_project_prompts_path(project_dir: str) -> Path:
    return legacy_project_state_dir(project_dir) / "prompts"


def project_skills_path(project_dir: str) -> Path:
    return project_state_dir(project_dir) / "skills"


def legacy_project_skills_path(project_dir: str) -> Path:
    return legacy_project_state_dir(project_dir) / "skills"


def project_server_registry_path(project_dir: str) -> Path:
    return project_state_dir(project_dir) / "server.json"


def legacy_project_server_registry_path(project_dir: str) -> Path:
    return legacy_project_state_dir(project_dir) / "server.json"


def project_mcp_path(project_dir: str) -> Path:
    return project_state_dir(project_dir) / "mcp.json"


def legacy_project_mcp_path(project_dir: str) -> Path:
    return legacy_project_state_dir(project_dir) / "mcp.json"


def effective_global_config_path() -> Path:
    return _first_existing_path(GLOBAL_CONFIG, LEGACY_GLOBAL_CONFIG) or GLOBAL_CONFIG


def effective_auth_path() -> Path:
    return _first_existing_path(AUTH_FILE, LEGACY_AUTH_FILE) or AUTH_FILE


def effective_global_agents_path() -> Path:
    return _first_existing_path(GLOBAL_AGENTS_FILE, LEGACY_GLOBAL_AGENTS_FILE) or GLOBAL_AGENTS_FILE


def effective_global_system_override_path() -> Path:
    return (
        _first_existing_path(GLOBAL_SYSTEM_OVERRIDE, LEGACY_GLOBAL_SYSTEM_OVERRIDE)
        or GLOBAL_SYSTEM_OVERRIDE
    )


def effective_global_append_system_path() -> Path:
    return (
        _first_existing_path(GLOBAL_APPEND_SYSTEM, LEGACY_GLOBAL_APPEND_SYSTEM)
        or GLOBAL_APPEND_SYSTEM
    )


def effective_project_config_path(project_dir: str) -> Path:
    return _first_existing_path(
        project_config_path(project_dir),
        legacy_project_config_path(project_dir),
    ) or project_config_path(project_dir)


def effective_project_agents_path(project_dir: str) -> Path:
    return _first_existing_path(
        project_agents_path(project_dir),
        legacy_project_agents_path(project_dir),
    ) or project_agents_path(project_dir)


def effective_project_system_override_path(project_dir: str) -> Path:
    return _first_existing_path(
        project_system_override_path(project_dir),
        legacy_project_system_override_path(project_dir),
    ) or project_system_override_path(project_dir)


def effective_project_append_system_path(project_dir: str) -> Path:
    return _first_existing_path(
        project_append_system_path(project_dir),
        legacy_project_append_system_path(project_dir),
    ) or project_append_system_path(project_dir)


def effective_project_server_registry_path(project_dir: str) -> Path:
    return _first_existing_path(
        project_server_registry_path(project_dir),
        legacy_project_server_registry_path(project_dir),
    ) or project_server_registry_path(project_dir)


def effective_project_mcp_path(project_dir: str) -> Path:
    return _first_existing_path(
        project_mcp_path(project_dir),
        legacy_project_mcp_path(project_dir),
    ) or project_mcp_path(project_dir)


def effective_global_mcp_path() -> Path:
    return _first_existing_path(GLOBAL_MCP_PATH, LEGACY_GLOBAL_MCP_PATH) or GLOBAL_MCP_PATH


def effective_server_provider_overlay_path() -> Path:
    return (
        _first_existing_path(
            SERVER_PROVIDER_OVERLAY_PATH,
            LEGACY_SERVER_PROVIDER_OVERLAY_PATH,
        )
        or SERVER_PROVIDER_OVERLAY_PATH
    )


def prompt_dirs(project_dir: str = "") -> list[Path]:
    paths = [LEGACY_PROMPTS_DIR, PROMPTS_DIR]
    if project_dir:
        paths.extend(
            [
                legacy_project_prompts_path(project_dir),
                project_prompts_path(project_dir),
            ]
        )
    return _dedupe_paths(*paths)


def skill_dirs(project_dir: str = "") -> list[Path]:
    paths = [LEGACY_SKILLS_DIR, SKILLS_DIR]
    if project_dir:
        paths.extend(
            [
                legacy_project_skills_path(project_dir),
                project_skills_path(project_dir),
            ]
        )
    return _dedupe_paths(*paths)


# ── Pydantic models ──────────────────────────────────────────────


class AgentConfig(BaseModel):
    model: str = "anthropic/claude-sonnet-4-20250514"
    small_model: str = ""  # utility model for compaction, auto-title, etc.
    temperature: float = 0.0
    max_turns: int = 50
    system_prompt: str = ""
    thinking: str = "off"  # off | minimal | low | medium | high | xhigh


class ProviderModelConfig(BaseModel):
    name: str | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_reasoning: bool | None = None
    input_price_per_m: float | None = None
    output_price_per_m: float | None = None
    disabled: bool = False
    headers: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    variants: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ProviderConfig(BaseModel):
    type: str = ""
    name: str = ""
    api_key: str = ""
    base_url: str = ""
    api_type: str = ""
    region: str = ""
    profile: str = ""
    api_version: str = ""
    project: str = ""
    location: str = ""
    env: list[str] = Field(default_factory=list)
    headers: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    timeout: int | bool | None = None  # milliseconds; false disables the timeout
    whitelist: list[str] = Field(default_factory=list)
    blacklist: list[str] = Field(default_factory=list)
    models: dict[str, ProviderModelConfig] = Field(default_factory=dict)
    requires_api_key: bool | None = None


class PermissionsConfig(BaseModel):
    edit: Literal["allow", "ask", "deny"] = "allow"
    write: Literal["allow", "ask", "deny"] = "allow"
    bash: Literal["allow", "ask", "deny"] = "ask"
    bash_commands: dict[str, Literal["allow", "ask", "deny"]] = Field(default_factory=dict)


class LspServerConfig(BaseModel):
    command: list[str] = Field(default_factory=list)
    extensions: list[str] = Field(default_factory=list)
    root_markers: list[str] = Field(default_factory=list)
    initialization: dict[str, Any] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    disabled: bool = False


class LspConfig(BaseModel):
    enabled: bool = True
    auto_install: bool = True
    install_dir: str = str(CONFIG_DIR / "lsp")
    servers: dict[str, LspServerConfig] = Field(default_factory=dict)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7432
    auth_token: str = ""
    tls_cert: str = ""
    tls_key: str = ""
    max_sessions: int = 10
    scheduler_enabled: bool = True


OFFICIAL_REGISTRY_URL = (
    "https://raw.githubusercontent.com/mihver1/worker-agent/main/registry/extensions.toml"
)


class RegistryConfig(BaseModel):
    name: str = ""
    url: str = ""


class ExtensionsConfig(BaseModel):
    dir: str = str(CONFIG_DIR / "extensions")
    enabled: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)
    registries: list[RegistryConfig] = Field(
        default_factory=lambda: [
            RegistryConfig(name="official", url=OFFICIAL_REGISTRY_URL),
        ]
    )


class SessionsConfig(BaseModel):
    db_path: str = str(SESSIONS_DB)
    auto_compact: bool = True
    compact_threshold: float = 0.8


class UIConfig(BaseModel):
    theme: str = "dark"
    show_cost: bool = True
    show_reasoning: bool = True
    render_markdown: bool = True


class KeybindingsConfig(BaseModel):
    """Custom keybindings — keys are Textual key strings, values are action names."""

    bindings: dict[str, str] = Field(default_factory=dict)


class WorkerConfig(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    lsp: LspConfig = Field(default_factory=LspConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    extensions: ExtensionsConfig = Field(default_factory=ExtensionsConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    keybindings: KeybindingsConfig = Field(default_factory=KeybindingsConfig)


# Artel-first public alias kept alongside the legacy compatibility name.
ArtelConfig = WorkerConfig


# ── Load config ───────────────────────────────────────────────────


def load_config(project_dir: str | None = None) -> WorkerConfig:
    """Load config: global → project overlay."""
    config = WorkerConfig()

    # Global config
    global_config = effective_global_config_path()
    if global_config.exists():
        with open(global_config, "rb") as f:
            data = tomllib.load(f)
        config = WorkerConfig.model_validate(data)

    # Project config overlay
    if project_dir:
        project_source = effective_project_config_path(project_dir)
        if project_source.exists():
            with open(project_source, "rb") as f:
                project_data = tomllib.load(f)
            # Merge: project overrides global
            merged = config.model_dump()
            _deep_merge(merged, project_data)
            config = WorkerConfig.model_validate(merged)

    return config


def persist_server_auth_token(token: str, project_dir: str | None = None) -> Path:
    """Persist server auth token to the config file that owns the effective setting."""
    target = GLOBAL_CONFIG
    source = effective_global_config_path()
    data: dict[str, Any] = {}

    if source.exists():
        with open(source, "rb") as f:
            data = tomllib.load(f)

    if project_dir:
        project_config = effective_project_config_path(project_dir)
        if project_config.exists():
            with open(project_config, "rb") as f:
                project_data = tomllib.load(f)
            project_server = project_data.get("server")
            if isinstance(project_server, dict) and "auth_token" in project_server:
                target = project_config_path(project_dir)
                data = project_data

    server_data = data.get("server")
    if not isinstance(server_data, dict):
        server_data = {}
        data["server"] = server_data
    server_data["auth_token"] = token

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(tomli_w.dumps(data), encoding="utf-8")
    return target


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ── Resolve provider and model from "provider/model-id" format ───


def resolve_model(config: WorkerConfig) -> tuple[str, str]:
    """Parse 'provider/model-id' into (provider_name, model_id)."""
    model_str = config.agent.model
    if "/" in model_str:
        provider, model = model_str.split("/", 1)
        return provider, model
    return "anthropic", model_str


# ── Template generation ───────────────────────────────────────────

_GLOBAL_TEMPLATE = """\
# ═══════════════════════════════════════════════════════════════
# Artel — global configuration
# Documentation: see the bundled docs site or project README
# ═══════════════════════════════════════════════════════════════

# ── Agent ─────────────────────────────────────────────────────
[agent]
# Model in provider/model-id format
# Examples:
#   "anthropic/claude-sonnet-4-20250514"
#   "openai/gpt-4.1"
#   "azure_openai/gpt-4.1"
#   "bedrock/anthropic.claude-3-7-sonnet-20250219-v1:0"
#   "kimi/kimi-k2.5"
#   "minimax/MiniMax-M2.5"
#   "google/gemini-2.5-pro"
#   "google_vertex/gemini-2.5-pro"
#   "vertex_anthropic/claude-sonnet-4@20250514"
#   "github_copilot/gpt-4.1"
#   "ollama/qwen3:32b"
#   "zai/glm-5"
model = "anthropic/claude-sonnet-4-20250514"

# Generation temperature (0.0 = deterministic, 1.0 = creative)
# temperature = 0.0

# Maximum number of agent-loop iterations per request
# max_turns = 50

# System prompt (appended to the built-in prompt)
# You can also use .artel/AGENTS.md in the project
# system_prompt = "You are a senior Python developer."

# Small model for utility tasks (compaction, auto-titles)
# Format: provider/model-id  (empty string = use the main model)
# Examples: "anthropic/claude-haiku-3" | "openai/gpt-4.1-mini"
# small_model = ""

# Extended thinking / reasoning
# off — disabled
# minimal | low | medium | high | xhigh — budget levels
# Anthropic: budget_tokens (1024..16384)
# OpenAI o-series: reasoning_effort (low/medium/high)
# thinking = "off"

# ── Providers
# Each provider is a separate [providers.<name>] section
# type: anthropic | openai | openai_compat | kimi | google | google_vertex
#       | vertex_anthropic
#       | bedrock | azure_openai | github_copilot | ollama | lmstudio | huggingface
#
# Authentication usually uses api_key;
# OAuth is available only for some providers (`artel login <provider>`)
# Environment variables also work:
#   ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, MOONSHOT_API_KEY,
#   MINIMAX_API_KEY, ZHIPU_API_KEY,
#   AZURE_OPENAI_API_KEY, GH_TOKEN, GITHUB_TOKEN,
#   GROQ_API_KEY, MISTRAL_API_KEY, XAI_API_KEY,
#   TOGETHER_API_KEY, CEREBRAS_API_KEY, DEEPSEEK_API_KEY,
#   OPENROUTER_API_KEY, OLLAMA_API_KEY, HF_API_KEY

# [providers.anthropic]
# type = "anthropic"
# api_key = "sk-ant-..."         # or ANTHROPIC_API_KEY env
# # base_url = "https://api.anthropic.com"  # override if you need a proxy
# # [providers.anthropic.options]
# # beta_headers = ["files-api-2025-04-14"]
# # interleaved_thinking = true
# # fine_grained_tool_streaming = true
# # OAuth login for anthropic automatically uses Claude Code-style headers/tool naming

# [providers.openai]
# type = "openai"
# api_key = "sk-..."             # or OPENAI_API_KEY env
# # base_url = "https://api.openai.com/v1"
# # api_type = "chat"            # "chat" (completions) | "responses"

# [providers.kimi]
# type = "kimi"
# api_key = "sk-..."             # or MOONSHOT_API_KEY env
# # base_url = "https://api.kimi.com/coding/v1"
# # Kimi For Coding uses an Anthropic-compatible messages endpoint

# [providers.minimax]
# type = "anthropic"
# api_key = "..."                # or MINIMAX_API_KEY env
# base_url = "https://api.minimax.io/anthropic/v1"
# # MiniMax uses an Anthropic-compatible messages endpoint

# [providers.google]
# type = "google"
# api_key = "..."                # or GEMINI_API_KEY env

# [providers.google_vertex]
# type = "google_vertex"
# # project = "my-gcp-project"   # or GOOGLE_VERTEX_PROJECT / GOOGLE_CLOUD_PROJECT
# # location = "us-central1"     # default: global; also GOOGLE_VERTEX_LOCATION
# # [providers.google_vertex.options]
# # credentials_path = "/path/to/service-account.json"  # otherwise ADC is used
# # scopes = ["https://www.googleapis.com/auth/cloud-platform"]

# [providers.vertex_anthropic]
# type = "vertex_anthropic"
# # project = "my-gcp-project"
# # location = "us-east5"
# # [providers.vertex_anthropic.options]
# # credentials_path = "/path/to/service-account.json"
# # beta_headers = ["files-api-2025-04-14"]

# [providers.groq]
# type = "openai_compat"
# api_key = "gsk_..."
# base_url = "https://api.groq.com/openai/v1"

# [providers.mistral]
# type = "openai_compat"
# api_key = "..."
# base_url = "https://api.mistral.ai/v1"

# [providers.xai]
# type = "openai_compat"
# api_key = "xai-..."
# base_url = "https://api.x.ai/v1"

# [providers.zai]
# type = "openai_compat"
# api_key = "..."                # or ZHIPU_API_KEY env
# base_url = "https://api.z.ai/api/paas/v4"
# # Use GLM models such as zai/glm-5 or z.ai/glm-5

# [providers.openrouter]
# type = "openai_compat"
# api_key = "sk-or-..."
# base_url = "https://openrouter.ai/api/v1"
# # timeout = 300000               # milliseconds; false = no timeout
# # [providers.openrouter.headers]
# # "HTTP-Referer" = "https://example.com"
# # "X-Title" = "artel"

# [providers.together]
# type = "openai_compat"
# api_key = "..."
# base_url = "https://api.together.xyz/v1"

# [providers.cerebras]
# type = "openai_compat"
# api_key = "..."
# base_url = "https://api.cerebras.ai/v1"

# [providers.deepseek]
# type = "openai_compat"
# api_key = "sk-..."
# base_url = "https://api.deepseek.com/v1"

# [providers.huggingface]
# type = "huggingface"
# api_key = "hf_..."

# [providers.ollama]
# type = "ollama"
# # base_url = "http://localhost:11434/v1"  # default OpenAI-compatible endpoint
# # requires_api_key = false
# # `/models` fetches the model list directly from the Ollama API.
# # Models can also be referenced directly as ollama/<model-id>; to show them in /models,
# # define them in the section below.
# # [providers.ollama.models."qwen3:32b"]
# # name = "Qwen3 32B"
# # context_window = 131072

# [providers.ollama_cloud]
# type = "ollama"
# # api_key = "ollama_..."         # or OLLAMA_API_KEY env
# # base_url = "https://ollama.com/v1"
# # `/models` fetches the model list directly from the Ollama Cloud API.
# # Hosted Ollama uses the same OpenAI-compatible runtime as local Ollama.
# # [providers.ollama_cloud.models."gpt-oss:20b"]
# # name = "gpt-oss 20B via Ollama Cloud"
# # context_window = 200000

# [providers.lmstudio]
# type = "lmstudio"
# # base_url = "http://127.0.0.1:1234/v1"
# # requires_api_key = false        # if auth is enabled in LM Studio, you can set api_key
# # `/models` fetches the model list directly from the LM Studio API.
# # Models can also be referenced directly as lmstudio/<model-id>; to show them in /models,
# # define them in the section below.
# # [providers.lmstudio.models."openai/gpt-oss-20b"]
# # name = "LM Studio gpt-oss-20b"
# # context_window = 131072

# [providers.bedrock]
# type = "bedrock"
# # region = "us-east-1"
# # profile = "default"           # AWS profile from ~/.aws/credentials
# # base_url = "https://bedrock-runtime.us-east-1.amazonaws.com"  # optional custom endpoint
# # Credentials can come from the AWS credential chain (env/shared config/SSO/etc.)
# # [providers.bedrock.options]
# # access_key_id = "AKIA..."
# # secret_access_key = "..."
# # session_token = "..."          # optional STS session token

# [providers.azure_openai]
# type = "azure_openai"
# api_key = "..."
# base_url = "https://<resource>.openai.azure.com"
# # api_version = "2024-10-21"
# # api_type = "chat"            # "chat" for deployment path, "responses" for /openai/v1/responses

# [providers.github_copilot]
# type = "github_copilot"
# # api_key = "github_pat_..."    # or `artel login github_copilot`
# #                              # or COPILOT_GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN
# # interactive login requires `gh` (`brew install gh` on macOS/Homebrew)
# # base_url = "https://api.githubcopilot.com"

# [providers.github_copilot_enterprise]
# type = "github_copilot"
# # api_key = "github_pat_..."    # or `artel login github_copilot_enterprise`
# # interactive login requires `gh` (`brew install gh` on macOS/Homebrew)
# # base_url = "https://api.githubcopilot.com"
# # [providers.github_copilot_enterprise.options]
# # github_host = "SUBDOMAIN.ghe.com"  # or GH_HOST for enterprise auth lookup via gh

# ── Permissions ───────────────────────────────────────────────
[permissions]
# Policy for each tool: "allow" | "ask" | "deny"
# "ask" — the agent requests confirmation before executing

# File editing
edit = "allow"

# File creation/overwrite
write = "allow"

# Shell command execution
bash = "ask"

# Rules for specific bash commands (glob patterns)
# The last matching rule wins
# [permissions.bash_commands]
# "git *" = "allow"
# "npm *" = "allow"
# "rm *" = "deny"
# "sudo *" = "deny"

# ── LSP / Code Intelligence ───────────────────────────────────
[lsp]
# Enable first-party LSP-backed code intelligence tools when a compatible
# language server is available on PATH, configured below, or can be installed
# automatically by Artel on first use.
# enabled = true
# auto_install = true
# install_dir = "~/.config/artel/lsp"

# Override or add server definitions by logical id.
# Common built-ins:
#   python      -> basedpyright-langserver | pyright-langserver | pylsp
#   typescript  -> typescript-language-server --stdio
#   go          -> gopls
#   rust        -> rust-analyzer
#
# [lsp.servers.python]
# # disabled = true
# # command = ["basedpyright-langserver", "--stdio"]
# # extensions = [".py"]
# # root_markers = ["pyproject.toml", ".git"]
# # [lsp.servers.python.env]
# # PYRIGHT_PYTHON_FORCE_VERSION = "latest"
# # [lsp.servers.python.initialization]
# # diagnosticMode = "workspace"

# ── Server ────────────────────────────────────────────────────
[server]
# Host and port for `artel serve`
# host = "0.0.0.0"
# port = 7432

# Bearer token for client authentication
# Generated automatically on the first `artel serve`
# auth_token = "artel_..."

# TLS (optional, reverse proxy recommended)
# tls_cert = "/path/to/cert.pem"
# tls_key = "/path/to/key.pem"

# Maximum concurrent sessions
# max_sessions = 10

# Enable built-in scheduled tasks runner inside `artel serve`
# scheduler_enabled = true

# ── Extensions ────────────────────────────────────────────────
[extensions]
# Directory for installed extensions
# dir = "~/.config/artel/extensions"

# Enabled extensions (by name)
# enabled = ["artel-ext-websearch", "artel-ext-git"]

# Disabled extensions
# disabled = []

# Extension registries (official is enabled by default)
# Add a company or community registry:
#   artel ext registry add mycompany https://example.com/extensions.toml
# [[extensions.registries]]
# name = "official"
# url = "https://example.com/extensions.toml"

# ── Sessions ──────────────────────────────────────────────────
[sessions]
# Path to the sessions database
# db_path = "~/.config/artel/sessions.db"

# Auto-compaction near the context limit
# auto_compact = true

# Compaction threshold (fraction of the context window)
# compact_threshold = 0.8

# ── UI ────────────────────────────────────────────────────────
[ui]
# Theme: "dark" | "light" | "monokai" | "dracula"
# theme = "dark"

# Show token cost
# show_cost = true

# Show reasoning/thinking blocks
# show_reasoning = true

# Markdown rendering in the TUI
# render_markdown = true
"""

_PROJECT_TEMPLATE = """\
# ═══════════════════════════════════════════════════════════════
# Artel — project configuration (overrides global settings)
# ═══════════════════════════════════════════════════════════════

# [agent]
# model = "anthropic/claude-sonnet-4-20250514"
# temperature = 0.0
# max_turns = 50

# [permissions]
# edit = "allow"
# write = "allow"
# bash = "ask"
# [permissions.bash_commands]
# "git *" = "allow"
# "make *" = "allow"
#
# [lsp]
# enabled = true
# auto_install = true
# [lsp.servers.python]
# command = ["basedpyright-langserver", "--stdio"]
#
# [server]
# scheduler_enabled = true
"""

_AGENTS_MD_TEMPLATE = """\
# Project Instructions

<!-- Artel loads this file as additional system prompt context. -->
<!-- Add project-specific instructions, conventions, common commands here. -->

## Project Overview

<!-- Describe what this project does -->

## Conventions

<!-- Code style, naming, testing approach, etc. -->

## Common Commands

<!-- Build, test, lint commands for this project -->
"""


def generate_global_config() -> None:
    """Create ~/.config/artel/config.toml with the fully-commented template."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not GLOBAL_CONFIG.exists():
        GLOBAL_CONFIG.write_text(_GLOBAL_TEMPLATE, encoding="utf-8")


def generate_project_config(project_dir: str) -> None:
    """Create .artel/config.toml and .artel/AGENTS.md in the project."""
    artel_dir = project_state_dir(project_dir)
    artel_dir.mkdir(parents=True, exist_ok=True)

    config_path = artel_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text(_PROJECT_TEMPLATE, encoding="utf-8")
    agents_path = artel_dir / "AGENTS.md"
    if not agents_path.exists():
        agents_path.write_text(_AGENTS_MD_TEMPLATE, encoding="utf-8")
