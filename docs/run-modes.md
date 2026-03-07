# Run modes

Worker supports four main ways to run the agent, depending on how much UI, isolation, and network separation you need. For embedding and editor integrations, it also exposes RPC and ACP over stdio.

## Print mode

Use print mode for scripting, shell pipelines, and quick one-off prompts.

```bash
worker -p "generate a changelog entry for the latest commit"
```

Useful when:

- integrating Worker into scripts
- piping file content into a prompt
- getting a single response without opening the TUI

## Local TUI mode

Run the interactive TUI and the agent in the same process:

```bash
worker
```

This is the default mode when no subcommand or prompt flag is provided.

## Server mode

Run a headless Worker daemon that accepts remote connections:

```bash
worker serve
```

You can override the bind address and port:

```bash
worker serve --host 0.0.0.0 --port 7432
```

Use server mode when:

- the agent should run on a remote machine
- you want a long-lived daemon
- the client UI and agent runtime should live on different hosts

## Remote TUI mode

Connect the local TUI to a remote Worker server:

```bash
worker connect ws://host:7432
```

Useful flags:

```bash
worker connect ws://host:7432 --token <bearer-token>
worker connect ws://host:7432 --forward-credentials all
worker connect ws://host:7432 --forward-credentials anthropic,openai
```

Remote mode is useful when you want a lightweight local UI while the agent executes elsewhere.

## Continue and resume sessions

Continue the most recent session:

```bash
worker --continue
```

Resume a specific session:

```bash
worker --resume <session-id>
```

These flags apply to both print mode and the default TUI mode.

## RPC mode

If you need to embed Worker in another process, you can run a JSON-RPC server over stdin and stdout:

```bash
worker rpc
```

## ACP mode

If you need an ACP-compatible client to drive Worker over stdin and stdout, run:

```bash
worker acp
```

This mode is intended for editors, IDEs, and other frontends that speak the Agent Client Protocol.
See [ACP integration](acp.md) for the supported session lifecycle, permission modes, and per-session configuration options.
