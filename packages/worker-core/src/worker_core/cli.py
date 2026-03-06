"""CLI entry point for Worker."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import click

from worker_core.config import (
    generate_global_config,
    generate_project_config,
    load_config,
    resolve_model,
)


@click.group(invoke_without_command=True)
@click.option("-p", "--prompt", default=None, help="One-shot prompt (print mode)")
@click.option(
    "-c",
    "--continue",
    "continue_session",
    is_flag=True,
    help="Continue the most recent session",
)
@click.option(
    "-r",
    "--resume",
    "resume_id",
    default=None,
    help="Resume a specific session by ID",
)
@click.pass_context
def cli(
    ctx: click.Context,
    prompt: str | None,
    continue_session: bool,
    resume_id: str | None,
) -> None:
    """Worker — extensible Python coding agent."""
    # Check for migrations on startup
    from worker_core.migrations import check_and_migrate

    check_and_migrate()

    if prompt:
        # Support piped stdin: cat file.txt | worker -p "explain this"
        stdin_content = ""
        if not sys.stdin.isatty():
            stdin_content = sys.stdin.read()
        full_prompt = prompt
        if stdin_content:
            full_prompt = f"{stdin_content}\n\n{prompt}"
        asyncio.run(
            _print_mode(
                full_prompt,
                continue_session=continue_session,
                resume_id=resume_id or "",
            )
        )
        return
    if ctx.invoked_subcommand is None:
        # Default: local mode (TUI + agent in-process)
        from worker_tui.app import run_tui

        run_tui(continue_session=continue_session, resume_id=resume_id or "")


@cli.command()
def init() -> None:
    """Initialize Worker config (global + project)."""
    generate_global_config()
    cwd = os.getcwd()
    generate_project_config(cwd)
    click.echo("Initialized Worker config:")
    click.echo("  Global: ~/.config/worker/config.toml")
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


@ext.command("update")
@click.argument("name", required=False, default=None)
def ext_update(name: str | None) -> None:
    """Update an extension (or all if no name given)."""
    import subprocess

    from worker_core.extensions import discover_extensions

    if name:
        click.echo(f"Updating extension '{name}'...")
        result = subprocess.run(
            ["uv", "pip", "install", "--upgrade", name],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            click.echo(f"Extension '{name}' updated.")
        else:
            click.echo(f"Update failed:\n{result.stderr}", err=True)
    else:
        extensions = discover_extensions()
        if not extensions:
            click.echo("No extensions to update.")
            return
        click.echo(f"Updating {len(extensions)} extension(s)...")
        for ext_name in extensions:
            result = subprocess.run(
                ["uv", "pip", "install", "--upgrade", ext_name],
                capture_output=True, text=True,
            )
            status = "\u2713" if result.returncode == 0 else "\u2717"
            click.echo(f"  {status} {ext_name}")


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
def rpc() -> None:
    """Start JSON-RPC server on stdin/stdout (for embedding)."""
    from worker_server.rpc import run_rpc

    asyncio.run(run_rpc())


@cli.command()
@click.argument("provider")
def login(provider: str) -> None:
    """Authenticate with a provider via OAuth (kimi, anthropic, openai)."""
    from worker_ai.oauth import get_oauth_provider

    oauth = get_oauth_provider(provider)
    if oauth is None:
        supported = "kimi, anthropic, openai"
        click.echo(f"OAuth not supported for '{provider}'. Supported: {supported}")
        click.echo("Use an API key instead (config or env variable).")
        return
    try:
        asyncio.run(oauth.login())
    except Exception as e:
        click.echo(f"Login failed: {e}", err=True)


# ── Print mode ────────────────────────────────────────────────────


async def _print_mode(
    prompt: str,
    *,
    continue_session: bool = False,
    resume_id: str = "",
) -> None:
    """One-shot prompt: run agent, print result to stdout."""
    import uuid as _uuid

    from worker_core.agent import AgentEventType
    from worker_core.bootstrap import (
        bootstrap_runtime,
        create_agent_session_from_bootstrap,
    )
    from worker_core.sessions import SessionStore

    config = load_config(os.getcwd())
    provider_name, model_id = resolve_model(config)

    cwd = os.getcwd()
    runtime = await bootstrap_runtime(
        config,
        provider_name,
        model_id,
        project_dir=cwd,
        resolve_api_key=_resolve_api_key,
        include_extensions=True,
    )

    # Session store
    store = SessionStore(config.sessions.db_path)
    await store.open()

    session_id = ""
    prior_messages = None

    if resume_id:
        info = await store.get_session(resume_id)
        if info:
            session_id = info.id
            prior_messages = await store.get_messages(session_id)
    elif continue_session:
        last = await store.get_last_session()
        if last:
            session_id = last.id
            prior_messages = await store.get_messages(session_id)

    if not session_id:
        session_id = str(_uuid.uuid4())
        await store.create_session(session_id, model_id)
    session = create_agent_session_from_bootstrap(
        config,
        runtime,
        project_dir=cwd,
        store=store,
        session_id=session_id,
    )

    if prior_messages:
        session.messages.extend(prior_messages)

    async for event in session.run(prompt):
        if event.type == AgentEventType.TEXT_DELTA:
            print(event.content, end="", flush=True)
        elif event.type == AgentEventType.REASONING_DELTA:
            print(event.content, end="", flush=True, file=sys.stderr)
        elif event.type == AgentEventType.TOOL_CALL:
            print(f"\n[tool: {event.tool_name}]", file=sys.stderr)
        elif event.type == AgentEventType.TOOL_RESULT:
            pass  # Tool results go through the agent loop
        elif event.type == AgentEventType.ERROR:
            print(f"\nError: {event.error}", file=sys.stderr)
        elif event.type == AgentEventType.COMPACT:
            print("\n[compacted]", file=sys.stderr)
        elif event.type == AgentEventType.DONE:
            print()  # Final newline

    await store.close()
    await runtime.provider.close()


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


async def _resolve_api_key(config, provider_name: str) -> tuple[str | None, str]:
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
    # From OAuth token store (auth.json), refreshing expired OAuth tokens when possible.
    try:
        from worker_ai.oauth import TokenStore, get_oauth_provider

        oauth = get_oauth_provider(provider_name)
        if oauth is not None:
            token = await oauth.get_token()
        else:
            token = TokenStore().load(provider_name)
            if token and token.is_expired:
                token = None
        if token:
            return token.access_token, "oauth"
    except Exception:
        pass
    return None, "api"


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
