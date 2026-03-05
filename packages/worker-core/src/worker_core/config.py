"""Configuration system — Pydantic models, TOML loading, template generation."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w

# ── Config paths ──────────────────────────────────────────────────

CONFIG_DIR = Path(os.environ.get("WORKER_CONFIG_DIR", "~/.config/worker")).expanduser()
GLOBAL_CONFIG = CONFIG_DIR / "config.toml"
AUTH_FILE = CONFIG_DIR / "auth.json"


# ── Pydantic models ──────────────────────────────────────────────


class AgentConfig(BaseModel):
    model: str = "anthropic/claude-sonnet-4-20250514"
    temperature: float = 0.0
    max_turns: int = 50
    system_prompt: str = ""
    thinking: str = "off"  # off | minimal | low | medium | high | xhigh


class ProviderConfig(BaseModel):
    type: str = "anthropic"
    api_key: str = ""
    base_url: str = ""
    api_type: str = ""
    region: str = ""
    profile: str = ""
    api_version: str = ""


class PermissionsConfig(BaseModel):
    edit: Literal["allow", "ask", "deny"] = "allow"
    write: Literal["allow", "ask", "deny"] = "allow"
    bash: Literal["allow", "ask", "deny"] = "ask"
    bash_commands: dict[str, Literal["allow", "ask", "deny"]] = Field(default_factory=dict)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7432
    auth_token: str = ""
    tls_cert: str = ""
    tls_key: str = ""
    max_sessions: int = 10


class ExtensionsConfig(BaseModel):
    dir: str = str(CONFIG_DIR / "extensions")
    enabled: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)
    registry_url: str = (
        "https://raw.githubusercontent.com/worker-agent/registry/main/extensions.json"
    )


class SessionsConfig(BaseModel):
    db_path: str = str(CONFIG_DIR / "sessions.db")
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
    server: ServerConfig = Field(default_factory=ServerConfig)
    extensions: ExtensionsConfig = Field(default_factory=ExtensionsConfig)
    sessions: SessionsConfig = Field(default_factory=SessionsConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    keybindings: KeybindingsConfig = Field(default_factory=KeybindingsConfig)


# ── Load config ───────────────────────────────────────────────────


def load_config(project_dir: str | None = None) -> WorkerConfig:
    """Load config: global → project overlay."""
    config = WorkerConfig()

    # Global config
    if GLOBAL_CONFIG.exists():
        with open(GLOBAL_CONFIG, "rb") as f:
            data = tomllib.load(f)
        config = WorkerConfig.model_validate(data)

    # Project config overlay
    if project_dir:
        project_config = Path(project_dir) / ".worker" / "config.toml"
        if project_config.exists():
            with open(project_config, "rb") as f:
                project_data = tomllib.load(f)
            # Merge: project overrides global
            merged = config.model_dump()
            _deep_merge(merged, project_data)
            config = WorkerConfig.model_validate(merged)

    return config


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
# Worker — глобальная конфигурация
# Документация: https://github.com/worker-agent/worker#config
# ═══════════════════════════════════════════════════════════════

# ── Агент ─────────────────────────────────────────────────────
[agent]
# Модель в формате provider/model-id
# Примеры:
#   "anthropic/claude-sonnet-4-20250514"
#   "openai/gpt-4.1"
#   "kimi/kimi-k2.5"
#   "google/gemini-2.5-pro"
#   "ollama/qwen3:32b"
model = "anthropic/claude-sonnet-4-20250514"

# Температура генерации (0.0 — детерминированный, 1.0 — креативный)
# temperature = 0.0

# Максимум итераций агент-лупа за один запрос
# max_turns = 50

# System prompt (дополнение к встроенному)
# Можно также использовать .worker/AGENTS.md в проекте
# system_prompt = "You are a senior Python developer."

# Extended thinking / reasoning
# off — отключено
# minimal | low | medium | high | xhigh — уровни бюджета
# Anthropic: budget_tokens (1024..16384)
# OpenAI o-series: reasoning_effort (low/medium/high)
# thinking = "off"

# ── Провайдеры
# Каждый провайдер — отдельная секция [providers.<name>]
# type: anthropic | openai | openai_compat | kimi | google
#       | bedrock | azure_openai | ollama | huggingface
#
# Аутентификация: api_key ИЛИ oauth (через `worker login <provider>`)
# Переменные окружения тоже работают:
#   ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY,
#   GROQ_API_KEY, MISTRAL_API_KEY, XAI_API_KEY,
#   TOGETHER_API_KEY, CEREBRAS_API_KEY, DEEPSEEK_API_KEY,
#   OPENROUTER_API_KEY, HF_API_KEY

# [providers.anthropic]
# type = "anthropic"
# api_key = "sk-ant-..."         # или ANTHROPIC_API_KEY env
# # base_url = "https://api.anthropic.com"  # override если нужен proxy

# [providers.openai]
# type = "openai"
# api_key = "sk-..."             # или OPENAI_API_KEY env
# # base_url = "https://api.openai.com/v1"
# # api_type = "chat"            # "chat" (completions) | "responses"

# [providers.kimi]
# type = "kimi"
# api_key = "sk-..."             # или через `worker login kimi` (OAuth)
# # base_url = "https://api.kimi.com/coding/v1"

# [providers.google]
# type = "google"
# api_key = "..."                # или GEMINI_API_KEY env

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

# [providers.openrouter]
# type = "openai_compat"
# api_key = "sk-or-..."
# base_url = "https://openrouter.ai/api/v1"

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
# # base_url = "http://localhost:11434"  # дефолт

# [providers.bedrock]
# type = "bedrock"
# # region = "us-east-1"
# # profile = "default"           # AWS profile из ~/.aws/credentials

# [providers.azure]
# type = "azure_openai"
# api_key = "..."
# base_url = "https://<resource>.openai.azure.com"
# # api_version = "2024-10-21"

# ── Права доступа ─────────────────────────────────────────────
[permissions]
# Политика для каждого инструмента: "allow" | "ask" | "deny"
# "ask" — агент запросит подтверждение перед выполнением

# Редактирование файлов
edit = "allow"

# Создание/перезапись файлов
write = "allow"

# Выполнение shell команд
bash = "ask"

# Права для конкретных bash команд (glob patterns)
# Последнее совпавшее правило побеждает
# [permissions.bash_commands]
# "git *" = "allow"
# "npm *" = "allow"
# "rm *" = "deny"
# "sudo *" = "deny"

# ── Сервер ────────────────────────────────────────────────────
[server]
# Адрес и порт для `worker serve`
# host = "0.0.0.0"
# port = 7432

# Токен для аутентификации клиентов (Bearer)
# Генерируется автоматически при первом `worker serve`
# auth_token = "wkr_..."

# TLS (опционально, рекомендуется reverse proxy)
# tls_cert = "/path/to/cert.pem"
# tls_key = "/path/to/key.pem"

# Максимум одновременных сессий
# max_sessions = 10

# ── Расширения ────────────────────────────────────────────────
[extensions]
# Директория для установленных расширений
# dir = "~/.config/worker/extensions"

# Включённые расширения (по имени)
# enabled = ["worker-ext-websearch", "worker-ext-git"]

# Отключённые расширения
# disabled = []

# URL реестра расширений
# registry_url = "https://raw.githubusercontent.com/worker-agent/registry/main/extensions.json"

# ── Сессии ────────────────────────────────────────────────────
[sessions]
# Путь к базе данных сессий
# db_path = "~/.config/worker/sessions.db"

# Авто-компактинг при приближении к лимиту контекста
# auto_compact = true

# Порог компактинга (процент от context window)
# compact_threshold = 0.8

# ── Интерфейс ─────────────────────────────────────────────────
[ui]
# Тема: "dark" | "light" | "monokai" | "dracula"
# theme = "dark"

# Показывать стоимость токенов
# show_cost = true

# Показывать reasoning/thinking блоки
# show_reasoning = true

# Markdown рендеринг в TUI
# render_markdown = true
"""

_PROJECT_TEMPLATE = """\
# ═══════════════════════════════════════════════════════════════
# Worker — проектная конфигурация (перезаписывает глобальную)
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
"""

_AGENTS_MD_TEMPLATE = """\
# Project Instructions

<!-- Worker loads this file as additional system prompt context. -->
<!-- Add project-specific instructions, conventions, common commands here. -->

## Project Overview

<!-- Describe what this project does -->

## Conventions

<!-- Code style, naming, testing approach, etc. -->

## Common Commands

<!-- Build, test, lint commands for this project -->
"""


def generate_global_config() -> None:
    """Create ~/.config/worker/config.toml with the fully-commented template."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not GLOBAL_CONFIG.exists():
        GLOBAL_CONFIG.write_text(_GLOBAL_TEMPLATE, encoding="utf-8")


def generate_project_config(project_dir: str) -> None:
    """Create .worker/config.toml and .worker/AGENTS.md in the project."""
    worker_dir = Path(project_dir) / ".worker"
    worker_dir.mkdir(parents=True, exist_ok=True)

    config_path = worker_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text(_PROJECT_TEMPLATE, encoding="utf-8")

    agents_path = worker_dir / "AGENTS.md"
    if not agents_path.exists():
        agents_path.write_text(_AGENTS_MD_TEMPLATE, encoding="utf-8")
