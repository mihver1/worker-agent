"""CLI entry point for Worker."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
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
        # Default: managed local server-backed TUI.
        from worker_tui.app import run_tui
        from worker_tui.local_server import ensure_managed_local_server
        handle = asyncio.run(ensure_managed_local_server(os.getcwd()))
        run_tui(
            remote_url=handle.remote_url,
            auth_token=handle.auth_token,
            continue_session=continue_session,
            resume_id=resume_id or "",
        )


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
@click.option("--token", default="", hidden=True)
def serve(host: str | None, port: int | None, token: str) -> None:
    """Start the headless server daemon."""
    from worker_server.server import run_server

    kwargs: dict[str, Any] = {}
    if host:
        kwargs["host"] = host
    if port:
        kwargs["port"] = port
    if token:
        kwargs["auth_token"] = token
    kwargs["announce"] = click.echo
    asyncio.run(run_server(**kwargs))


@cli.command()
@click.argument("url")
@click.option("--token", default="", help="Bearer auth token")
@click.option(
    "--forward-credentials",
    default="",
    help="Forward local credentials to the remote server (all or comma-separated providers)",
)
def connect(url: str, token: str, forward_credentials: str) -> None:
    """Connect TUI to a remote Worker server."""
    from worker_tui.app import run_tui
    run_tui(
        remote_url=url,
        auth_token=token,
        forward_credentials=forward_credentials,
    )


@cli.group()
def ext() -> None:
    """Manage extensions."""


@ext.command("install")
@click.argument("source")
def ext_install(source: str) -> None:
    """Install an extension by name, git URL, or local path.

    If SOURCE is a plain package name (no '/', ':', '.'), it is looked up
    in the configured registries first.
    """
    import subprocess

    from worker_core import ext_manifest

    install_source = _resolve_install_source(source)
    click.echo(f"Installing extension from {install_source}...")
    try:
        result = subprocess.run(
            ["uv", "pip", "install", "--no-sources", install_source],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            pkg_name = _parse_installed_package_name(result.stdout, install_source)
            ext_manifest.add(pkg_name, install_source)
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

    from worker_core import ext_manifest

    click.echo(f"Removing extension {name}...")
    try:
        result = subprocess.run(
            ["uv", "pip", "uninstall", name],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            ext_manifest.remove(name)
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

    from worker_core import ext_manifest
    from worker_core.extensions import discover_extensions

    if name:
        # Prefer the original source from manifest so VCS extensions update correctly
        entry = next((e for e in ext_manifest.list_entries() if e.name == name), None)
        source = entry.source if entry else name
        click.echo(f"Updating extension '{name}'...")
        result = subprocess.run(
            ["uv", "pip", "install", "--no-sources", "--upgrade", source],
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
        manifest_entries = {e.name: e for e in ext_manifest.list_entries()}
        for ext_name in extensions:
            entry = manifest_entries.get(ext_name)
            source = entry.source if entry else ext_name
            result = subprocess.run(
                ["uv", "pip", "install", "--no-sources", "--upgrade", source],
                capture_output=True, text=True,
            )
            status = "\u2713" if result.returncode == 0 else "\u2717"
            click.echo(f"  {status} {ext_name}")


@ext.command("search")
@click.argument("query")
def ext_search(query: str) -> None:
    """Search across all configured extension registries."""
    from worker_core.ext_registry import search_all

    config = load_config(os.getcwd())
    click.echo(f"Searching for '{query}'...")
    try:
        matches = search_all(config.extensions.registries, query)
    except Exception as e:
        click.echo(f"Search failed: {e}", err=True)
        return
    if not matches:
        click.echo("No extensions found.")
        return
    for m in matches:
        label = f"  {m.name}"
        if m.registry_name:
            label += f"  [{m.registry_name}]"
        click.echo(f"{label} — {m.description}")
        click.echo(f"    install: worker ext install {m.repo or m.name}")


# ── ext registry subgroup ─────────────────────────────────────────


@ext.group("registry")
def ext_registry_group() -> None:
    """Manage extension registries."""


@ext_registry_group.command("list")
def ext_registry_list() -> None:
    """List configured extension registries."""
    config = load_config(os.getcwd())
    regs = config.extensions.registries
    if not regs:
        click.echo("No registries configured.")
        return
    for r in regs:
        click.echo(f"  {r.name}: {r.url}")


@ext_registry_group.command("add")
@click.argument("name")
@click.argument("url")
def ext_registry_add(name: str, url: str) -> None:
    """Add a custom extension registry."""
    import tomllib

    import tomli_w

    from worker_core.config import GLOBAL_CONFIG

    # Read current config
    data: dict = {}
    if GLOBAL_CONFIG.exists():
        with open(GLOBAL_CONFIG, "rb") as f:
            data = tomllib.load(f)

    ext_section = data.setdefault("extensions", {})
    registries = ext_section.setdefault("registries", [])

    # Check for duplicate name
    for r in registries:
        if r.get("name") == name:
            click.echo(f"Registry '{name}' already exists. Remove it first.")
            return

    registries.append({"name": name, "url": url})
    GLOBAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_CONFIG.write_text(tomli_w.dumps(data), encoding="utf-8")
    click.echo(f"Registry '{name}' added: {url}")


@ext_registry_group.command("remove")
@click.argument("name")
def ext_registry_remove(name: str) -> None:
    """Remove a custom extension registry (cannot remove 'official')."""
    import tomllib

    import tomli_w

    from worker_core.config import GLOBAL_CONFIG

    if name == "official":
        click.echo("Cannot remove the built-in 'official' registry.")
        return

    data: dict = {}
    if GLOBAL_CONFIG.exists():
        with open(GLOBAL_CONFIG, "rb") as f:
            data = tomllib.load(f)

    ext_section = data.get("extensions", {})
    registries = ext_section.get("registries", [])
    new_regs = [r for r in registries if r.get("name") != name]
    if len(new_regs) == len(registries):
        click.echo(f"Registry '{name}' not found.")
        return

    ext_section["registries"] = new_regs
    data["extensions"] = ext_section
    GLOBAL_CONFIG.write_text(tomli_w.dumps(data), encoding="utf-8")
    click.echo(f"Registry '{name}' removed.")


@cli.group(invoke_without_command=True)
@click.option("--global", "show_global", is_flag=True, help="Show global config path only")
@click.option("--project", "show_project", is_flag=True, help="Show project config path only")
@click.pass_context
def config(ctx: click.Context, show_global: bool, show_project: bool) -> None:
    """Show config file paths and merged configuration."""
    from worker_core.config import GLOBAL_CONFIG

    cwd = os.getcwd()
    project_config = Path(cwd) / ".worker" / "config.toml"

    if show_global:
        click.echo(str(GLOBAL_CONFIG))
        return
    if show_project:
        click.echo(str(project_config))
        return
    if ctx.invoked_subcommand is not None:
        return

    # Default: list all config files with existence status
    _print_config_path("Global", GLOBAL_CONFIG)
    _print_config_path("Project", project_config)


def _print_config_path(label: str, path: Path) -> None:
    exists = "✓" if path.exists() else "✗"
    click.echo(f"  {exists} {label}: {path}")


@config.command("print")
def config_print() -> None:
    """Print the merged (effective) configuration as TOML."""
    import tomli_w

    cwd = os.getcwd()
    merged = load_config(cwd)
    data = merged.model_dump(exclude_none=True)
    click.echo(tomli_w.dumps(data))


@cli.command()
def rpc() -> None:
    """Start JSON-RPC server on stdin/stdout (for embedding)."""
    from worker_server.rpc import run_rpc

    asyncio.run(run_rpc())


@cli.command()
def acp() -> None:
    """Start ACP agent on stdin/stdout."""
    from worker_server.acp import run_acp

    asyncio.run(run_acp())


@cli.command()
@click.argument("provider")
def login(provider: str) -> None:
    """Authenticate with a provider via OAuth."""
    from worker_ai.oauth import get_oauth_provider, list_oauth_provider_names
    from worker_ai.provider_specs import get_provider_spec

    from worker_core.provider_resolver import get_provider_env_vars
    config = load_config(os.getcwd())
    oauth = get_oauth_provider(provider, config=config)
    if oauth is None:
        spec = get_provider_spec(provider)
        provider_id = spec.id if spec is not None else provider
        env_vars = tuple(get_provider_env_vars(config, provider))
        supported = ", ".join(list_oauth_provider_names())
        if env_vars:
            click.echo(
                f"OAuth not supported for '{provider}'. "
                f"Use {env_vars[0]} or [providers.{provider_id}].api_key."
            )
        else:
            click.echo(
                f"OAuth not supported for '{provider}'. "
                f"Configure [providers.{provider_id}] instead."
            )
        click.echo(f"Supported OAuth providers: {supported}")
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
        runtime="local",
    )

    # Session store
    store = SessionStore(config.sessions.db_path)
    await store.open()

    session_id = ""
    prior_messages = None
    resumed_info = None

    if resume_id:
        info = await store.get_session(resume_id)
        if info:
            session_id = info.id
            prior_messages = await store.get_messages(session_id)
            resumed_info = info
    elif continue_session:
        last = await store.get_last_session()
        if last:
            session_id = last.id
            prior_messages = await store.get_messages(session_id)
            resumed_info = last

    if not session_id:
        session_id = str(_uuid.uuid4())
        await store.create_session(session_id, model_id, thinking_level=config.agent.thinking)
    session = create_agent_session_from_bootstrap(
        config,
        runtime,
        project_dir=cwd,
        store=store,
        session_id=session_id,
    )
    if resumed_info and resumed_info.thinking_level:
        session.thinking_level = resumed_info.thinking_level  # type: ignore[assignment]

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




async def _resolve_api_key(config, provider_name: str) -> tuple[str | None, str]:
    """Resolve API key: config → env → OAuth token → None.

    Returns (key, auth_type) where auth_type is "api" or "oauth".
    """
    from worker_ai.oauth import (
        get_oauth_provider,
        is_github_copilot_provider,
        resolve_github_copilot_token,
    )

    from worker_core.provider_resolver import get_provider_config, get_provider_env_vars
    # From config
    prov_cfg = get_provider_config(config, provider_name)
    if prov_cfg and prov_cfg.api_key:
        return prov_cfg.api_key, "api"
    # From env
    for env_var in get_provider_env_vars(config, provider_name):
        val = os.environ.get(env_var)
        if val:
            return val, "api"
    # From provider-specific OAuth flows, refreshing expired tokens when possible.
    try:
        oauth = get_oauth_provider(provider_name, config=config)
        if oauth is not None:
            token = await oauth.get_token()
        else:
            token = None
        if token:
            return token.access_token, "oauth"
    except Exception:
        pass
    if is_github_copilot_provider(provider_name):
        token = await resolve_github_copilot_token(config, provider_name)
        if token:
            return token, "api"
    return None, "api"


def _resolve_install_source(source: str) -> str:
    """If *source* looks like a plain package name, resolve it via registries.

    URLs, paths and VCS prefixes are returned as-is.
    """
    # Heuristic: plain name has no path separators, no URL scheme, no VCS prefix
    if any(ch in source for ch in (":", "/", "@", ".")):
        return source

    from worker_core.ext_registry import list_all

    config = load_config(os.getcwd())
    entries = list_all(config.extensions.registries)
    for entry in entries:
        if entry.name == source and entry.repo:
            click.echo(f"Resolved '{source}' → {entry.repo}  [{entry.registry_name}]")
            return entry.repo
    # Not found — return as-is, pip will try PyPI
    return source


def _parse_installed_package_name(pip_stdout: str, source: str) -> str:
    """Extract the canonical package name from uv pip install output or source.

    uv outputs lines like "Installed 1 package ... worker-ext-foo v0.1.0".
    Falls back to the source string (basename without VCS prefix).
    """
    import re

    # Try to parse from "Installed ... <name>" in uv output
    for line in pip_stdout.splitlines():
        # uv format: " + package-name==version"
        m = re.match(r"^\s*\+\s+([a-zA-Z0-9_.-]+)", line)
        if m:
            return m.group(1)

    # Fallback: derive from source
    # Strip VCS prefixes like git+https://...
    clean = re.sub(r"^(git|hg|svn|bzr)\+", "", source)
    # SCP-style: git@github.com:org/repo.git  (no ://)
    scp_match = re.match(r"^[^@]+@[^:]+:(.+)$", clean)
    if scp_match:
        path_part = scp_match.group(1)
    elif "://" in clean:
        # Take the path part after the authority (handles user@host correctly)
        path_part = clean.split("://", 1)[1]
    else:
        # Non-URL: strip @branch/tag suffix then take basename
        clean = clean.split("@")[0]
        return clean.rstrip("/").rsplit("/", 1)[-1]

    # Strip query/fragment
    path_part = re.split(r"[?#]", path_part)[0]
    # The repo name is the last slash-separated segment
    name = path_part.rstrip("/").rsplit("/", 1)[-1]
    # Strip .git suffix and @branch/commit suffix on the segment
    name = re.sub(r"\.git(@.*)?$", "", name)
    name = name.split("@")[0] if "@" in name else name
    return name


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
