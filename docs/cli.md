# CLI reference

This page summarizes the main commands exposed by the `worker` CLI.

## Top-level usage

```bash
worker [OPTIONS] [COMMAND]
```

Top-level options:

- `-p, --prompt TEXT` — run a one-shot prompt in print mode
- `-c, --continue` — continue the most recent session
- `-r, --resume TEXT` — resume a specific session by ID

## Core commands

### `worker`

Starts the local TUI when you do not provide a subcommand or `--prompt`.

### `worker init`

Creates:

- `~/.config/worker/config.toml`
- `.worker/config.toml`
- `.worker/AGENTS.md`

### `worker serve`

Starts the headless server daemon.

Options:

- `--host TEXT`
- `--port INTEGER`

### `worker connect URL`

Connects the TUI to a remote Worker server.

Options:

- `--token TEXT`
- `--forward-credentials TEXT`

### `worker config`

Shows config file paths.

Options:

- `--global`
- `--project`

Subcommands:

- `worker config print` — print merged effective config as TOML

### `worker rpc`

Starts a JSON-RPC server on stdin and stdout for embedding scenarios.

### `worker acp`

Starts an ACP agent on stdin and stdout for ACP-compatible clients.
See [ACP integration](acp.md) for session behavior, supported controls, and permission flow details.

### `worker login PROVIDER`

Attempts OAuth login for a supported provider.

## Extension commands

### `worker ext install SOURCE`

Install an extension from a name, URL, or local path.

### `worker ext list`

List installed extensions.

### `worker ext remove NAME`

Remove an installed extension.

### `worker ext update [NAME]`

Update one extension or all installed extensions.

### `worker ext search QUERY`

Search configured extension registries.

### `worker ext registry list`

List configured extension registries.

### `worker ext registry add NAME URL`

Add a custom registry.

### `worker ext registry remove NAME`

Remove a custom registry.

## Useful examples

```bash
worker -p "review the latest changes"
worker --continue
worker --resume 7f1f7f80-0000-0000-0000-000000000000
worker serve --host 0.0.0.0 --port 7432
worker connect ws://example.com:7432 --token wkr_example
worker config print
worker ext search git
```
