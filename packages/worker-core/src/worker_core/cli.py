"""CLI entry point for Worker."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from typing import Any

from worker_core.config import (
    generate_global_config,
    generate_project_config,
    load_config,
    resolve_model,
)


@click.group(invoke_without_command=True)
@click.option("-p", "--prompt", default=None, help="One-shot prompt (print mode)")
@click.pass_context
def cli(ctx: click.Context, prompt: str | None) -> None:
    """Worker — extensible Python coding agent."""
    if prompt:
        asyncio.run(_print_mode(prompt))
        return
    if ctx.invoked_subcommand is None:
        # Default: local mode (TUI + agent in-process)
        from worker_tui.app import run_tui

        run_tui()


@cli.command()
def init() -> None:
    """Initialize Worker config (global + project)."""
    generate_global_config()
    cwd = os.getcwd()
    generate_project_config(cwd)
    click.echo(f"Initialized Worker config:")
    click.echo(f"  Global: ~/.config/worker/config.toml")
    click.echo(f"  Project: {cwd}/.worker/config.toml")
    click.echo(f"  Project: {cwd}/.worker/AGENTS.md")


@cli.command()
@click.option("--host", default=None, help="Bind address")
@click.option("--port", default=None, type=int, help="Bind port")
def serve(host: str | None, port: int | None) -> None:
    """Start the headless server daemon."""
    from worker_server.server import run_server

    kwargs: dict[str, Any] = {}
    if host:
        kwargs["host"] = host
    if port:
        kwargs["port"] = port
    asyncio.run(run_server(**kwargs))


@cli.command()
@click.argument("url")
@click.option("--token", default="", help="Bearer auth token")
def connect(url: str, token: str) -> None:
    """Connect TUI to a remote Worker server."""
    from worker_tui.app import run_tui

    run_tui(remote_url=url, auth_token=token)


@cli.group()
def ext() -> None:
    """Manage extensions."""


@ext.command("install")
@click.argument("source")
def ext_install(source: str) -> None:
    """Install an extension from git+ssh://, git+https://, or local path."""
    import subprocess

    click.echo(f"Installing extension from {source}...")
    try:
        result = subprocess.run(
            ["uv", "pip", "install", source],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            click.echo("Extension installed.")
        else:
            click.echo(f"Install failed:\n{result.stderr}", err=True)
    except FileNotFoundError:
        click.echo("Error: 'uv' not found. Install it first: https://docs.astral.sh/uv/", err=True)


@ext.command("list")
def ext_list() -> None:
    """List installed extensions."""
    from worker_core.extensions import discover_extensions

    extensions = discover_extensions()
    if not extensions:
        click.echo("No extensions installed.")
        return
    for name, cls in extensions.items():
        ver = getattr(cls, "version", "?")
        click.echo(f"  {name} v{ver}")


@ext.command("remove")
@click.argument("name")
def ext_remove(name: str) -> None:
    """Remove an installed extension."""
    import subprocess

    click.echo(f"Removing extension {name}...")
    try:
        result = subprocess.run(
            ["uv", "pip", "uninstall", name],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            click.echo(f"Extension '{name}' removed.")
        else:
            click.echo(f"Remove failed:\n{result.stderr}", err=True)
    except FileNotFoundError:
        click.echo("Error: 'uv' not found.", err=True)


@ext.command("search")
@click.argument("query")
def ext_search(query: str) -> None:
    """Search the extension registry."""
    import httpx as _httpx

    config = load_config(os.getcwd())
    registry_url = config.extensions.registry_url
    click.echo(f"Searching for '{query}'...")
    try:
        resp = _httpx.get(registry_url, timeout=10)
        resp.raise_for_status()
        entries = resp.json()
        matches = [
            e for e in entries
            if query.lower() in e.get("name", "").lower()
            or query.lower() in e.get("description", "").lower()
            or any(query.lower() in t.lower() for t in e.get("tags", []))
        ]
        if not matches:
            click.echo("No extensions found.")
            return
        for m in matches:
            click.echo(f"  {m['name']} — {m.get('description', '')}")
            click.echo(f"    install: worker ext install {m.get('repo', m['name'])}")
    except Exception as e:
        click.echo(f"Search failed: {e}", err=True)


@cli.command()
@click.argument("provider")
def login(provider: str) -> None:
    """Authenticate with a provider via OAuth (kimi, anthropic, openai)."""
    from worker_ai.oauth import get_oauth_provider

    oauth = get_oauth_provider(provider)
    if oauth is None:
        supported = "kimi, anthropic, openai"
        click.echo(f"OAuth not supported for '{provider}'. Supported: {supported}")
        click.echo(f"Use an API key instead (config or env variable).")
        return
    try:
        asyncio.run(oauth.login())
    except Exception as e:
        click.echo(f"Login failed: {e}", err=True)


# ── Print mode ────────────────────────────────────────────────────


async def _print_mode(prompt: str) -> None:
    """One-shot prompt: run agent, print result to stdout."""
    from worker_ai.providers import create_default_registry
    from worker_core.agent import AgentEventType, AgentSession
    from worker_core.tools.builtins import create_builtin_tools

    config = load_config(os.getcwd())
    provider_name, model_id = resolve_model(config)

    registry = create_default_registry()

    # Resolve API key from config or env
    api_key, auth_type = _resolve_api_key(config, provider_name)

    provider_config = config.providers.get(provider_name)
    kwargs: dict[str, Any] = {}
    if provider_config and provider_config.base_url:
        kwargs["base_url"] = provider_config.base_url
    if auth_type == "oauth":
        kwargs["auth_type"] = "oauth"

    provider = registry.create(provider_name, api_key=api_key, **kwargs)

    cwd = os.getcwd()
    tools = create_builtin_tools(cwd)
    session = AgentSession(
        provider=provider,
        model=model_id,
        tools=tools,
        system_prompt=config.agent.system_prompt,
        project_dir=cwd,
        temperature=config.agent.temperature,
        max_turns=config.agent.max_turns,
    )

    async for event in session.run(prompt):
        if event.type == AgentEventType.TEXT_DELTA:
            print(event.content, end="", flush=True)
        elif event.type == AgentEventType.TOOL_CALL:
            print(f"\n[tool: {event.tool_name}]", file=sys.stderr)
        elif event.type == AgentEventType.TOOL_RESULT:
            pass  # Tool results go through the agent loop
        elif event.type == AgentEventType.ERROR:
            print(f"\nError: {event.error}", file=sys.stderr)
        elif event.type == AgentEventType.DONE:
            print()  # Final newline

    await provider.close()


# ── API key resolution ────────────────────────────────────────────

_ENV_KEY_MAP = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GEMINI_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "xai": "XAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "together": "TOGETHER_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "huggingface": "HF_API_KEY",
}


def _resolve_api_key(config, provider_name: str) -> tuple[str | None, str]:
    """Resolve API key: config → env → OAuth token → None.

    Returns (key, auth_type) where auth_type is "api" or "oauth".
    """
    # From config
    prov_cfg = config.providers.get(provider_name)
    if prov_cfg and prov_cfg.api_key:
        return prov_cfg.api_key, "api"
    # From env
    env_var = _ENV_KEY_MAP.get(provider_name)
    if env_var:
        val = os.environ.get(env_var)
        if val:
            return val, "api"
    # From OAuth token store (auth.json)
    try:
        from worker_ai.oauth import TokenStore

        store = TokenStore()
        token = store.load(provider_name)
        if token and not token.is_expired:
            return token.access_token, "oauth"
    except Exception:
        pass
    return None, "api"


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
