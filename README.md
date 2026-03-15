# Artel

Extensible Python coding agent for local development, remote execution, and server-backed integrations.

## Documentation

User-facing documentation is published on GitHub Pages:

- [Artel documentation](https://mihver1.github.io/artel/)

Preview the docs locally:

```bash
uv sync --dev
uv run mkdocs serve
```

## Current capabilities

- **CLI + TUI workflows**: one-shot print mode, local interactive TUI, session continue/resume
- **Project/global rules**: define mandatory coding rules, inject active rules into the system prompt, manage them via `/rules` and `/rule ...`, and refuse tool actions that conflict with active rules
- **Remote execution**: headless server mode plus remote TUI over WebSocket
- **Scheduled tasks**: `artel serve` can execute prompt-based jobs on cron/interval timers while the server is running, with overlap policy, manual run, persisted state, and per-job execution/session modes
- **Integration modes**: JSON-RPC over stdio and ACP over stdio for editor/client integrations
- **Built-in tools**: file editing, shell, shared task board/operator notes, git worktree management, filesystem globbing, optional `ag`/`ripgrep` search helpers when those binaries are installed, plus web search and public page fetch with untrusted-content prompt-injection guards, safe summary/strict modes, optional domain allow/deny filtering, and mini-model-backed utility summarization
- **First-party MCP stores**: global MCP config in `~/.config/artel/mcp.json`, project MCP config in `.artel/mcp.json`, with project entries merged over global definitions by server name
- **Python-native extensions**: install tools, hooks, and UI widgets from registries, Git URLs, or local paths
- **Broad provider support**: hosted APIs, OpenAI-compatible backends, cloud platforms, and local runtimes

## Quick start

```bash
# Install from source
uv sync

# Or use the bootstrap installer
curl -fsSL https://raw.githubusercontent.com/mihver1/artel/main/install.sh | bash

# Initialize config
artel init

# Configure a default model in ~/.config/artel/config.toml or .artel/config.toml
# Example:
# [agent]
# model = "anthropic/claude-sonnet-4-20250514"

# One-shot prompt
artel -p "explain this codebase"

# Interactive TUI (local mode; no cmux required)
artel

# Resume the last session
artel --continue

# Manage git worktrees from the TUI via /wt
# or from the agent via the built-in `worktree` tool

# Start server daemon
artel serve

# Add a scheduled job in project scope
artel schedule add morning-review --kind cron --cron "0 9 * * 1-5" --prompt-name daily-review --execution-mode readonly

# Connect to remote server
artel connect ws://host:7432

# Start ACP mode for editor integrations
artel acp
```

## Run modes

Artel currently supports these primary modes:

- `artel -p "..."` — print mode for scripts and shell pipelines
- `artel` — local TUI mode (no cmux workspace bootstrap)
- `artel serve` — headless server daemon
- `artel connect ws://host:7432` — remote TUI connected to a server
- `artel rpc` — JSON-RPC over stdio
- `artel acp` — ACP agent over stdio

## Product scope

Supported now:
- local TUI (`artel`)
- print mode (`artel -p`)
- continue/resume session flow
- headless server (`artel serve`)
- remote TUI (`artel connect`)
- JSON-RPC (`artel rpc`)
- ACP (`artel acp`)
- rules and rule enforcement
- MCP config/runtime basics
- schedules
- built-in worktree and search tools
- orchestration/delegation tools
- Python-native extensions

Unavailable in this checkout:
- full web UI runtime behind `artel web`

The `artel web` command remains present as a compatibility/placeholder surface, but the full web UI source is not included in this checkout.

## Configuration

Artel uses layered configuration:

- global config: `~/.config/artel/config.toml`
- project config: `.artel/config.toml`
- project instructions: `.artel/AGENTS.md`
- global rules: `~/.config/artel/rules.json`
- project rules: `.artel/rules.json`

Run `artel init` to generate commented templates.
Use `artel config` to inspect config locations and `artel config print` to see the merged effective configuration.
On first run, Artel also migrates legacy Worker config and project state when it finds them.

Rules can be managed from the CLI and TUI:

```bash
artel rules
artel rule add --scope project --text "Do not use bash in this repo"
artel rule edit <rule-id> --text "Use pytest for tests"
artel rule enable <rule-id>
artel rule disable <rule-id>
artel rule delete <rule-id>
```

Inside the TUI, use `/rules`, `/rule add`, and `/rule edit <id>`. Rule add/edit opens a dialog instead of using the composer.

Rule toggles inside the TUI are session-scoped:
- `/rule enable <id>` — enable for the current session only
- `/rule disable <id>` — disable for the current session only
- `/rule reset <id>` — remove the session override for one rule
- `/rule reset all` — clear all session overrides

To change the persisted rule state in storage, use:
- `/rule persist enable <id>`
- `/rule persist disable <id>`

To control precedence / ordering, use:
- `artel rule move <id> --up`
- `artel rule move <id> --down`
- `artel rule move <id> --to <position>`
- `/rule move <id> up`
- `/rule move <id> down`
- `/rule move <id> to <position>`

Rules are evaluated in order. Earlier rules have higher precedence.

The `/rules` view shows three states for each rule:
- `persisted=...` — state in storage
- `session=...` — current session override, if any
- `effective=...` — final state used by prompt injection and enforcement

When running against a managed/remote Artel server, rules can also be managed through the control plane REST API:

- `GET /api/rules`
- `POST /api/rules`
- `PUT /api/rules/{rule_id}`
- `DELETE /api/rules/{rule_id}`
- `GET /api/sessions/{session_id}/rules`
- `PUT /api/sessions/{session_id}/rules/{rule_id}`

## Providers

Models are selected with `provider/model-id` strings such as:

- `anthropic/claude-sonnet-4-20250514`
- `openai/gpt-4.1`
- `zai/glm-5`
- `ollama/qwen2.5-coder`

The project currently supports:

- hosted API providers like Anthropic, OpenAI, Google, Kimi, MiniMax, and Azure OpenAI
- OpenAI-compatible providers like Groq, Mistral, xAI, OpenRouter, Together, Cerebras, DeepSeek, Fireworks, and others
- cloud backends like Bedrock, Google Vertex, and Vertex Anthropic
- local/self-hosted runtimes like Ollama, LM Studio, and llama.cpp
- OAuth login for supported providers via `artel login <provider>`

## MCP

Artel ships with first-party MCP configuration and runtime support.

Configuration stores:
- global: `~/.config/artel/mcp.json`
- project: `.artel/mcp.json`

Merge behavior:
- Artel loads global MCP config first
- then overlays project MCP config by server name
- project entries win over global entries for the same server

CLI examples:

```bash
artel mcp show --scope global
artel mcp show --scope project
artel mcp show --scope effective
artel mcp status
artel mcp status --json-output
artel mcp reload
artel mcp reload --json-output
artel mcp set context7 --scope global --transport streamable_http --url https://context7.liam.sh/mcp --tool-prefix ctx7__
artel mcp remove context7 --scope global

# normalized states include: connected, disabled, failed, needs_auth,
# timeout, unavailable
```

TUI commands:

```text
/mcp
/mcp reload
/delegates
/delegates list
/delegates show <run_id>
/delegates cancel <run_id>
# legacy alias: /agents
```

## Scheduled tasks

Artel server can run scheduled prompt jobs while `artel serve` is alive.

Storage:
- global: `~/.config/artel/schedules.json`
- project: `.artel/schedules.json`
- persisted runtime state: `schedules-state.json` in the same scope

Supported features:
- `interval` schedules via `--every <seconds>`
- `cron` schedules via 5-field cron expressions
- named prompts via `--prompt-name` plus `--arg`
- inline prompts via `--prompt`
- `readonly` or `inherit` execution mode
- `reuse` or `new` session mode
- overlap policies: `skip`, `allow`, `cancel_previous`
- missed-run policy: `none`, `latest`, `all`
- manual run and scheduler reload via REST
- persisted run history in `.artel/schedules-history.json`

CLI examples:

```bash
artel schedule list
artel schedule add heartbeat --every 300 --prompt "Summarize repo health"
artel schedule add morning-review --kind cron --cron "0 9 * * 1-5" --prompt-name daily-review --arg "repo=backend" --run-missed latest
artel schedule edit morning-review --execution-mode inherit --overlap cancel_previous --run-missed all
artel schedule enable morning-review
artel schedule disable morning-review
artel schedule show morning-review
artel schedule delete morning-review

# run now against the managed local server for the current project
artel schedule run morning-review

# or against an explicit remote server
artel schedule run morning-review --remote-url ws://127.0.0.1:7432 --token <token>
```

REST endpoints:
- `GET /api/schedules`
- `POST /api/schedules`
- `PUT /api/schedules/{schedule_id}`
- `DELETE /api/schedules/{schedule_id}`
- `POST /api/schedules/{schedule_id}/run`
- `POST /api/schedules/reload`

## Orchestration

Artel supports single-window in-process orchestration runs. Delegated work executes in the same Artel process and window rather than spawning a separate Artel instance per task.

Built-in orchestration tools:
- `delegate_task`
- `list_delegates`
- `get_delegate`
- `cancel_delegate`

## Extensions

```bash
artel ext install artel-ext-foo
artel ext install git+https://github.com/user/artel-ext-foo.git
artel ext install ../artel-ext-foo
artel ext list
artel ext update
artel ext search browser
artel ext remove artel-ext-foo
```

## Web UI status

`artel web` is still exposed in the CLI, but the current checkout does not include the full web UI implementation. Running it will raise a runtime error explaining that the web surface is unavailable in this checkout.
