# Worker

Extensible Python coding agent with client-server architecture.

## Features

- **4 built-in tools**: read, write, edit, bash
- **15+ LLM providers**: Anthropic, OpenAI, Google, Kimi, Groq, Mistral, xAI, Ollama, and more
- **Client-server architecture**: run headless on a remote server, connect via WebSocket
- **Native Python extensions**: add tools, hooks, UI widgets
- **4 operating modes**: print, local TUI, server daemon, remote TUI

## Quick Start

```bash
# Install
uv sync

# Initialize config
worker init

# One-shot prompt
worker -p "explain this codebase"

# Interactive TUI (local mode)
worker

# Start server daemon
worker serve

# Connect to remote server
worker connect ws://host:7432
```

## Configuration

All config is in `~/.config/worker/config.toml` (global) and `.worker/config.toml` (project).
Run `worker init` to generate fully-commented templates.

## Extensions

```bash
worker ext install git+https://github.com/user/worker-ext-foo.git
worker ext list
worker ext remove worker-ext-foo
```
