# Worker

Worker is an extensible Python coding agent built for local development, remote execution, and server-backed workflows.

It combines a CLI, a Textual-based terminal UI, a headless server mode, and an extension system in one Python-native stack.

## What Worker gives you

- A fast one-shot CLI for automation and scripting
- An interactive TUI for day-to-day coding sessions
- A server mode for running the agent remotely
- A remote client mode for connecting to a headless Worker instance
- Native Python extensions for tools, hooks, and UI widgets
- A flexible provider layer for hosted, cloud, and local LLM backends

## Core workflow

```bash
uv sync
worker init
worker -p "explain this repository"
worker
worker serve
worker connect ws://host:7432
```

## Documentation map

- [Installation](installation.md) explains how to install Worker from source or with the bootstrap script.
- [Quick start](quickstart.md) walks through first-run setup and the most important commands.
- [Configuration](configuration.md) covers global and project overrides, provider setup, permissions, and UI settings.
- [Run modes](run-modes.md) explains local, print, server, and remote usage.
- [ACP integration](acp.md) shows how to expose Worker as an ACP agent for editor and client integrations.
- [Providers](providers.md) summarizes how to configure hosted, cloud, and local models.
- [Extensions](extensions.md) shows how to install, update, and manage extension registries.
- [CLI reference](cli.md) gives you a concise command-by-command reference.

## Best place to start

If you are new to the project, read [Installation](installation.md) and then [Quick start](quickstart.md). If you already have Worker running, jump straight to [Configuration](configuration.md) or [CLI reference](cli.md).
