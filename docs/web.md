# Web status

`artel web` remains exposed as a compatibility command surface.

The current checkout does not include the full web UI runtime, so the command is unavailable at runtime and exits with an explanatory error.

Use these supported surfaces instead:

- local TUI via `artel`
- one-shot mode via `artel -p`
- headless server via `artel serve`
- remote TUI via `artel connect`
- ACP via `artel acp`
- JSON-RPC via `artel rpc`
