"""CLI entry point for Artel."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import click

from worker_core.config import (
    GLOBAL_CONFIG,
    effective_global_config_path,
    effective_project_config_path,
    generate_global_config,
    generate_project_config,
    load_config,
    project_agents_path,
    project_config_path,
    resolve_model,
)
from worker_core.rules import add_rule, delete_rule, list_rules, move_rule, update_rule
from worker_core.schedules import (
    add_schedule,
    delete_schedule,
    load_schedules,
    serialize_schedule,
    update_schedule,
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
    """Artel — extensible Python coding agent."""
    from worker_core.artel_bootstrap import bootstrap_artel

    bootstrap = bootstrap_artel(
        os.getcwd(),
        command_name=ctx.invoked_subcommand,
        prompt=prompt,
    )
    project_dir = bootstrap.project_dir

    if prompt:
        # Support piped stdin: cat file.txt | artel -p "explain this"
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
        # Default: local TUI with managed local server.
        from worker_tui.app import run_tui
        from worker_tui.local_server import ensure_managed_local_server

        handle = asyncio.run(ensure_managed_local_server(project_dir))
        run_tui(
            remote_url=handle.remote_url,
            auth_token=handle.auth_token,
            continue_session=continue_session,
            resume_id=resume_id or "",
        )


@cli.command()
def init() -> None:
    """Initialize Artel config (global + project)."""
    generate_global_config()
    cwd = os.getcwd()
    generate_project_config(cwd)
    click.echo("Initialized Artel config:")
    click.echo(f"  Global: {GLOBAL_CONFIG}")
    click.echo(f"  Project: {project_config_path(cwd)}")
    click.echo(f"  Project: {project_agents_path(cwd)}")


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


@cli.command("server-tray")
@click.option("--project-dir", default="", help="Project directory for the managed local server")
def server_tray(project_dir: str) -> None:
    """Start the macOS menu-bar companion for the managed local server."""
    from worker_tui.server_tray import run_server_tray

    run_server_tray(project_dir)


@cli.command()
@click.argument("url")
@click.option("--token", default="", help="Bearer auth token")
@click.option(
    "--forward-credentials",
    default="",
    help="Forward local credentials to the remote server (all or comma-separated providers)",
)
def connect(url: str, token: str, forward_credentials: str) -> None:
    """Connect TUI to a remote Artel server."""
    from worker_tui.app import run_tui

    run_tui(
        remote_url=url,
        auth_token=token,
        forward_credentials=forward_credentials,
    )


@cli.command(
    help="Run the reserved web command (web UI unavailable in this checkout).",
    short_help="Reserved web command; unavailable in this checkout.",
)
@click.option("--host", default="127.0.0.1", help="Bind address for the web UI")
@click.option("--port", default=8743, type=int, help="Bind port for the web UI")
@click.option("--remote-url", default="", help="Connect web UI to a remote Artel server")
@click.option("--token", default="", help="Bearer auth token for --remote-url")
@click.option("--native", is_flag=True, help="Run the web UI in native desktop mode")
@click.option(
    "--no-open-browser",
    is_flag=True,
    help="Do not open the browser automatically",
)
def web(
    host: str,
    port: int,
    remote_url: str,
    token: str,
    native: bool,
    no_open_browser: bool,
) -> None:
    """Run the reserved web command (web UI unavailable in this checkout)."""
    from worker_tui.local_server import ensure_managed_local_server
    from worker_web.app import run_web

    project_dir = os.getcwd()
    resolved_remote_url = remote_url
    resolved_auth_token = token
    if not resolved_remote_url:
        handle = asyncio.run(ensure_managed_local_server(project_dir))
        resolved_remote_url = handle.remote_url
        resolved_auth_token = handle.auth_token

    run_web(
        host=host,
        port=port,
        remote_url=resolved_remote_url,
        auth_token=resolved_auth_token,
        native=native,
        open_browser=not no_open_browser,
        project_dir=project_dir,
    )


@cli.group()
def mcp() -> None:
    """Manage first-party MCP configuration."""


@cli.group()
def lsp() -> None:
    """Inspect first-party LSP runtime status."""


@lsp.command("status")
@click.option(
    "--json-output",
    "json_output",
    is_flag=True,
    help="Print structured LSP state payload",
)
def lsp_status(json_output: bool) -> None:
    """Show current LSP runtime status."""
    from worker_core.extensions import ExtensionContext
    from worker_core.lsp_runtime import LspRuntimeManager

    runtime = LspRuntimeManager()
    try:
        asyncio.run(
            runtime.load(
                ExtensionContext(
                    project_dir=os.getcwd(),
                    runtime="local",
                    config=load_config(os.getcwd()),
                )
            )
        )
        if json_output:
            click.echo(json.dumps(runtime.status_payload(), indent=2, sort_keys=True))
        else:
            click.echo(runtime.status_text())
    finally:
        asyncio.run(runtime.close())


@mcp.command("show")
@click.option("--scope", type=click.Choice(["global", "project", "effective"]), default="effective")
def mcp_show(scope: str) -> None:
    """Show MCP configuration for one scope."""
    from worker_core.mcp import MCPRegistry

    registry = MCPRegistry()
    if scope == "global":
        config = registry.load_global_config()
        click.echo(
            json.dumps(
                {"servers": [asdict(server) for server in config.servers]}, indent=2, sort_keys=True
            )
        )
        return
    if scope == "project":
        config = registry.load_project_config(os.getcwd())
        click.echo(
            json.dumps(
                {"servers": [asdict(server) for server in config.servers]}, indent=2, sort_keys=True
            )
        )
        return
    loaded = registry.load_merged_config(os.getcwd())
    click.echo(
        json.dumps(
            {
                "sources": [str(path) for path in loaded.sources],
                "servers": {name: asdict(server) for name, server in loaded.servers.items()},
            },
            indent=2,
            sort_keys=True,
        )
    )


@mcp.command("status")
@click.option(
    "--json-output", "json_output", is_flag=True, help="Print structured MCP state payload"
)
def mcp_status(json_output: bool) -> None:
    """Show current MCP runtime/config status."""
    from worker_core.config import load_config
    from worker_core.extensions import ExtensionContext
    from worker_core.mcp_runtime import McpRuntimeManager

    runtime = McpRuntimeManager()
    try:
        asyncio.run(
            runtime.load(
                ExtensionContext(
                    project_dir=os.getcwd(), runtime="local", config=load_config(os.getcwd())
                )
            )
        )
        if json_output:
            click.echo(json.dumps(runtime.status_payload(), indent=2, sort_keys=True))
        else:
            click.echo(runtime.status_text())
    finally:
        if runtime.available:
            with contextlib.suppress(Exception):
                asyncio.run(runtime.close())


@mcp.command("reload")
@click.option(
    "--json-output", "json_output", is_flag=True, help="Print structured MCP state payload"
)
def mcp_reload(json_output: bool) -> None:
    """Reload MCP runtime and print status."""
    from worker_core.config import load_config
    from worker_core.extensions import ExtensionContext
    from worker_core.mcp_runtime import McpRuntimeManager

    runtime = McpRuntimeManager()
    try:
        asyncio.run(
            runtime.load(
                ExtensionContext(
                    project_dir=os.getcwd(), runtime="local", config=load_config(os.getcwd())
                )
            )
        )
        asyncio.run(runtime.reload())
        if json_output:
            click.echo(json.dumps(runtime.status_payload(), indent=2, sort_keys=True))
        else:
            click.echo(runtime.status_text())
    finally:
        if runtime.available:
            with contextlib.suppress(Exception):
                asyncio.run(runtime.close())


@mcp.command("set")
@click.argument("name")
@click.option("--scope", type=click.Choice(["global", "project"]), default="project")
@click.option("--transport", default="stdio")
@click.option("--command", default="")
@click.option("--url", default="")
@click.option("--arg", "args", multiple=True)
@click.option("--tool-prefix", default="")
def mcp_set(
    name: str,
    scope: str,
    transport: str,
    command: str,
    url: str,
    args: tuple[str, ...],
    tool_prefix: str,
) -> None:
    """Create or update one MCP server entry in global or project scope."""
    from worker_core.mcp import MCPConfig, MCPRegistry, MCPServerConfig

    registry = MCPRegistry()
    if scope == "global":
        current = registry.load_global_config()
    else:
        current = registry.load_project_config(os.getcwd())
    servers = {server.name: server for server in current.servers}
    servers[name] = MCPServerConfig(
        name=name,
        transport=transport,
        command=command,
        url=url,
        args=list(args),
        tool_prefix=tool_prefix,
    )
    config = MCPConfig(servers=sorted(servers.values(), key=lambda item: item.name))
    if scope == "global":
        written = registry.write_global_config(config)
    else:
        written = registry.write_project_config(os.getcwd(), config)
    click.echo(f"Saved MCP config: {written}")


@mcp.command("remove")
@click.argument("name")
@click.option("--scope", type=click.Choice(["global", "project"]), default="project")
def mcp_remove(name: str, scope: str) -> None:
    """Remove one MCP server entry from global or project scope."""
    from worker_core.mcp import MCPConfig, MCPRegistry

    registry = MCPRegistry()
    if scope == "global":
        current = registry.load_global_config()
        kept = [server for server in current.servers if server.name != name]
        written = registry.write_global_config(MCPConfig(servers=kept))
    else:
        current = registry.load_project_config(os.getcwd())
        kept = [server for server in current.servers if server.name != name]
        written = registry.write_project_config(os.getcwd(), MCPConfig(servers=kept))
    click.echo(f"Saved MCP config: {written}")


@cli.group("schedule")
def schedule_group() -> None:
    """Manage scheduled prompts/tasks."""


@schedule_group.command("list")
def schedule_list_command() -> None:
    """List configured schedules."""
    schedules = load_schedules(os.getcwd())
    if not schedules:
        click.echo("No schedules configured.")
        return
    for record in schedules:
        target = record.prompt_name or "<inline>"
        trigger = f"every {record.every_seconds}s" if record.kind == "interval" else record.cron
        click.echo(
            f"{record.id} [{record.scope}] [{'enabled' if record.enabled else 'disabled'}] "
            f"{record.kind}={trigger} prompt={target} "
            f"mode={record.execution_mode}/{record.session_mode} "
            f"run_missed={record.run_missed}"
        )


@schedule_group.command("add")
@click.argument("schedule_id")
@click.option("--scope", type=click.Choice(["project", "global"]), default="project")
@click.option("--kind", type=click.Choice(["interval", "cron"]), default="interval")
@click.option("--every", "every_seconds", type=int, default=0, help="Interval in seconds")
@click.option("--cron", default="", help="Cron expression: minute hour day month weekday")
@click.option("--timezone", default="UTC")
@click.option("--prompt", default="", help="Inline prompt text")
@click.option("--prompt-name", default="", help="Named prompt template")
@click.option("--arg", default="", help="Prompt variables/input")
@click.option(
    "--project-dir", "target_project_dir", default="", help="Project directory for the run"
)
@click.option("--model", default="", help="Override model (provider/model-id)")
@click.option("--session-mode", type=click.Choice(["reuse", "new"]), default="reuse")
@click.option("--execution-mode", type=click.Choice(["readonly", "inherit"]), default="readonly")
@click.option(
    "--overlap",
    "overlap_policy",
    type=click.Choice(["skip", "allow", "cancel_previous"]),
    default="skip",
)
@click.option("--max-runtime", "max_runtime_seconds", type=int, default=0)
@click.option("--run-missed", type=click.Choice(["none", "latest", "all"]), default="none")
@click.option("--disabled", is_flag=True)
def schedule_add_command(
    schedule_id: str,
    scope: str,
    kind: str,
    every_seconds: int,
    cron: str,
    timezone: str,
    prompt: str,
    prompt_name: str,
    arg: str,
    target_project_dir: str,
    model: str,
    session_mode: str,
    execution_mode: str,
    overlap_policy: str,
    max_runtime_seconds: int,
    run_missed: str,
    disabled: bool,
) -> None:
    """Add a scheduled task."""
    try:
        record = add_schedule(
            scope=scope,
            schedule_id=schedule_id,
            project_dir=os.getcwd(),
            enabled=not disabled,
            kind=kind,
            every_seconds=every_seconds,
            cron=cron,
            timezone=timezone,
            prompt=prompt,
            prompt_name=prompt_name,
            arg=arg,
            target_project_dir=target_project_dir,
            model=model,
            session_mode=session_mode,
            execution_mode=execution_mode,
            overlap_policy=overlap_policy,
            max_runtime_seconds=max_runtime_seconds,
            run_missed=run_missed,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Schedule added: {record.id}")


@schedule_group.command("edit")
@click.argument("schedule_id")
@click.option("--scope", type=click.Choice(["project", "global"]), default=None)
@click.option("--enable/--disable", "enabled", default=None)
@click.option("--kind", type=click.Choice(["interval", "cron"]), default=None)
@click.option("--every", "every_seconds", type=int, default=None)
@click.option("--cron", default=None)
@click.option("--timezone", default=None)
@click.option("--prompt", default=None)
@click.option("--prompt-name", default=None)
@click.option("--arg", default=None)
@click.option("--project-dir", "target_project_dir", default=None)
@click.option("--model", default=None)
@click.option("--session-mode", type=click.Choice(["reuse", "new"]), default=None)
@click.option("--execution-mode", type=click.Choice(["readonly", "inherit"]), default=None)
@click.option(
    "--overlap",
    "overlap_policy",
    type=click.Choice(["skip", "allow", "cancel_previous"]),
    default=None,
)
@click.option("--max-runtime", "max_runtime_seconds", type=int, default=None)
@click.option("--run-missed", type=click.Choice(["none", "latest", "all"]), default=None)
def schedule_edit_command(
    schedule_id: str,
    scope: str | None,
    enabled: bool | None,
    kind: str | None,
    every_seconds: int | None,
    cron: str | None,
    timezone: str | None,
    prompt: str | None,
    prompt_name: str | None,
    arg: str | None,
    target_project_dir: str | None,
    model: str | None,
    session_mode: str | None,
    execution_mode: str | None,
    overlap_policy: str | None,
    max_runtime_seconds: int | None,
    run_missed: str | None,
) -> None:
    """Edit a scheduled task."""
    try:
        record = update_schedule(
            schedule_id,
            project_dir=os.getcwd(),
            scope=scope,
            enabled=enabled,
            kind=kind,
            every_seconds=every_seconds,
            cron=cron,
            timezone=timezone,
            prompt=prompt,
            prompt_name=prompt_name,
            arg=arg,
            target_project_dir=target_project_dir,
            model=model,
            session_mode=session_mode,
            execution_mode=execution_mode,
            overlap_policy=overlap_policy,
            max_runtime_seconds=max_runtime_seconds,
            run_missed=run_missed,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Schedule updated: {record.id}")


@schedule_group.command("delete")
@click.argument("schedule_id")
def schedule_delete_command(schedule_id: str) -> None:
    """Delete a schedule."""
    record = delete_schedule(schedule_id, os.getcwd())
    if record is None:
        raise click.ClickException(f"Schedule '{schedule_id}' not found")
    click.echo(f"Schedule deleted: {record.id}")


@schedule_group.command("show")
@click.argument("schedule_id")
def schedule_show_command(schedule_id: str) -> None:
    """Show one schedule as JSON."""
    schedules = {record.id: record for record in load_schedules(os.getcwd())}
    record = schedules.get(schedule_id)
    if record is None:
        raise click.ClickException(f"Schedule '{schedule_id}' not found")
    click.echo(json.dumps(serialize_schedule(record), indent=2, sort_keys=True))


@schedule_group.command("enable")
@click.argument("schedule_id")
def schedule_enable_command(schedule_id: str) -> None:
    """Enable a schedule."""
    try:
        record = update_schedule(schedule_id, project_dir=os.getcwd(), enabled=True)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Schedule enabled: {record.id}")


@schedule_group.command("disable")
@click.argument("schedule_id")
def schedule_disable_command(schedule_id: str) -> None:
    """Disable a schedule."""
    try:
        record = update_schedule(schedule_id, project_dir=os.getcwd(), enabled=False)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Schedule disabled: {record.id}")


@schedule_group.command("run")
@click.argument("schedule_id")
@click.option("--remote-url", default="", help="Run schedule via remote Artel server")
@click.option("--token", default="", help="Bearer auth token for --remote-url")
def schedule_run_command(schedule_id: str, remote_url: str, token: str) -> None:
    """Trigger a schedule immediately."""
    from worker_core.control import RemoteWorkerControl

    async def _run() -> dict[str, Any]:
        resolved_remote_url = remote_url
        resolved_token = token
        if not resolved_remote_url:
            from worker_tui.local_server import ensure_managed_local_server

            handle = await ensure_managed_local_server(os.getcwd())
            resolved_remote_url = handle.remote_url
            resolved_token = handle.auth_token
        return await RemoteWorkerControl(resolved_remote_url, resolved_token).run_schedule(
            schedule_id
        )

    try:
        payload = asyncio.run(_run())
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(json.dumps(payload, indent=2, sort_keys=True))


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
    from worker_core.extensions_admin import install_extension

    install_source = _resolve_install_source(source)
    click.echo(f"Installing extension from {install_source}...")
    ok, message = install_extension(source)
    stream = click.echo if ok else (lambda value: click.echo(value, err=True))
    stream(message)


@ext.command("list")
def ext_list() -> None:
    """List installed extensions."""
    from worker_core.extensions_admin import list_installed_extensions

    extensions = list_installed_extensions()
    if not extensions:
        click.echo("No extensions installed.")
        return
    for ext in extensions:
        click.echo(f"  {ext.name} v{ext.version}")


@ext.command("remove")
@click.argument("name")
def ext_remove(name: str) -> None:
    """Remove an installed extension."""
    from worker_core.extensions_admin import remove_extension

    click.echo(f"Removing extension {name}...")
    ok, message = remove_extension(name)
    stream = click.echo if ok else (lambda value: click.echo(value, err=True))
    stream(message)


@ext.command("update")
@click.argument("name", required=False, default=None)
def ext_update(name: str | None) -> None:
    """Update an extension (or all if no name given)."""
    from worker_core.extensions_admin import update_all_extensions, update_extension

    if name:
        click.echo(f"Updating extension '{name}'...")
        ok, message = update_extension(name)
        stream = click.echo if ok else (lambda value: click.echo(value, err=True))
        stream(message)
    else:
        results = update_all_extensions()
        if not results:
            click.echo("No extensions to update.")
            return
        click.echo(f"Updating {len(results)} extension(s)...")
        for ext_name, ok, _message in results:
            status = "\u2713" if ok else "\u2717"
            click.echo(f"  {status} {ext_name}")


@ext.command("search")
@click.argument("query")
def ext_search(query: str) -> None:
    """Search across all configured extension registries."""
    from worker_core.extensions_admin import search_extensions

    click.echo(f"Searching for '{query}'...")
    try:
        matches = search_extensions(os.getcwd(), query)
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
        click.echo(f"    install: artel ext install {m.repo or m.name}")


# ── ext registry subgroup ─────────────────────────────────────────


@ext.group("registry")
def ext_registry_group() -> None:
    """Manage extension registries."""


@ext_registry_group.command("list")
def ext_registry_list() -> None:
    """List configured extension registries."""
    from worker_core.extensions_admin import list_registry_entries

    regs = list_registry_entries(os.getcwd())
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
    from worker_core.extensions_admin import add_registry

    ok, message = add_registry(name, url)
    stream = click.echo if ok else (lambda value: click.echo(value, err=True))
    stream(message)


@ext_registry_group.command("remove")
@click.argument("name")
def ext_registry_remove(name: str) -> None:
    """Remove a custom extension registry (cannot remove 'official')."""
    from worker_core.extensions_admin import remove_registry

    ok, message = remove_registry(name)
    stream = click.echo if ok else (lambda value: click.echo(value, err=True))
    stream(message)


@cli.group(invoke_without_command=True)
@click.option("--global", "show_global", is_flag=True, help="Show global config path only")
@click.option("--project", "show_project", is_flag=True, help="Show project config path only")
@click.pass_context
def config(ctx: click.Context, show_global: bool, show_project: bool) -> None:
    """Show config file paths and merged configuration."""

    cwd = os.getcwd()
    global_config = effective_global_config_path()
    project_config = effective_project_config_path(cwd)

    if show_global:
        click.echo(str(global_config))
        return
    if show_project:
        click.echo(str(project_config))
        return
    if ctx.invoked_subcommand is not None:
        return

    # Default: list all config files with existence status
    _print_config_path("Global", global_config)
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


@cli.command("rules")
def rules_list_command() -> None:
    """List global and project rules."""
    rules = list_rules(os.getcwd())
    if not rules:
        click.echo("No rules configured.")
        return
    for rule in rules:
        status = "enabled" if rule.enabled else "disabled"
        click.echo(f"{rule.order}. {rule.id} [{rule.scope}] [{status}] {rule.text}")


@cli.group("rule")
def rule_group() -> None:
    """Manage Artel rules."""


@rule_group.command("add")
@click.option("--scope", type=click.Choice(["project", "global"]), required=True, help="Rule scope")
@click.option("--text", required=True, help="Rule text")
@click.option("--disabled", is_flag=True, help="Create the rule as disabled")
def rule_add_command(scope: str, text: str, disabled: bool) -> None:
    """Add a rule."""
    try:
        rule = add_rule(scope=scope, text=text, project_dir=os.getcwd(), enabled=not disabled)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Rule added: {rule.id} [{rule.scope}] {'enabled' if rule.enabled else 'disabled'}")


@rule_group.command("edit")
@click.argument("rule_id")
@click.option("--text", default=None, help="Updated rule text")
@click.option(
    "--scope", type=click.Choice(["project", "global"]), default=None, help="Updated rule scope"
)
@click.option("--enable/--disable", "enabled", default=None, help="Updated enabled state")
def rule_edit_command(
    rule_id: str, text: str | None, scope: str | None, enabled: bool | None
) -> None:
    """Edit a rule."""
    try:
        rule = update_rule(
            rule_id, project_dir=os.getcwd(), text=text, scope=scope, enabled=enabled
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Rule updated: {rule.id} [{rule.scope}] {'enabled' if rule.enabled else 'disabled'}"
    )


def _delete_rule_or_raise(rule_id: str) -> str:
    rule = delete_rule(rule_id, os.getcwd())
    if rule is None:
        raise click.ClickException(f"Rule '{rule_id}' not found")
    return rule.id


@rule_group.command("delete")
@click.argument("rule_id")
def rule_delete_command(rule_id: str) -> None:
    """Delete a rule."""
    deleted_id = _delete_rule_or_raise(rule_id)
    click.echo(f"Rule deleted: {deleted_id}")


@rule_group.command("remove")
@click.argument("rule_id", nargs=1)
def rule_remove_command(rule_id: str) -> None:
    """Remove a rule."""
    deleted_id = _delete_rule_or_raise(rule_id)
    click.echo(f"Rule deleted: {deleted_id}")


@rule_group.command("rm")
@click.argument("rule_id", nargs=1)
def rule_rm_command(rule_id: str) -> None:
    """Remove a rule."""
    deleted_id = _delete_rule_or_raise(rule_id)
    click.echo(f"Rule deleted: {deleted_id}")


@rule_group.command("enable")
@click.argument("rule_id")
def rule_enable_command(rule_id: str) -> None:
    """Enable a rule."""
    try:
        rule = update_rule(rule_id, project_dir=os.getcwd(), enabled=True)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Rule enabled: {rule.id}")


@rule_group.command("disable")
@click.argument("rule_id")
def rule_disable_command(rule_id: str) -> None:
    """Disable a rule."""
    try:
        rule = update_rule(rule_id, project_dir=os.getcwd(), enabled=False)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Rule disabled: {rule.id}")


@rule_group.command("move")
@click.argument("rule_id")
@click.option("--to", "position", type=int, default=None, help="1-based target position")
@click.option("--up", is_flag=True, help="Move rule up by one")
@click.option("--down", is_flag=True, help="Move rule down by one")
def rule_move_command(rule_id: str, position: int | None, up: bool, down: bool) -> None:
    """Move a rule to control precedence/order."""
    offset = None
    if up and down:
        raise click.ClickException("Choose only one of --up or --down")
    if up:
        offset = -1
    elif down:
        offset = 1
    if position is None and offset is None:
        raise click.ClickException("Provide --to, --up, or --down")
    try:
        rule = move_rule(rule_id, project_dir=os.getcwd(), position=position, offset=offset)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Rule moved: {rule.id} -> position {rule.order}")


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
    if runtime.mcp_runtime is not None:
        await runtime.mcp_runtime.close()
    if runtime.lsp_runtime is not None:
        await runtime.lsp_runtime.close()


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

    uv outputs lines like "Installed 1 package ... artel-ext-foo v0.1.0".
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
