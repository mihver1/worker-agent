# ACP integration

Artel can expose itself as an ACP agent over stdio. This is the integration point to use when an editor, IDE, or another frontend wants to launch Artel as a subprocess and talk to it through the Agent Client Protocol.

## Start Artel in ACP mode

If `artel` is already installed on your `PATH`:

```bash
artel acp
```

If you are running from a source checkout:

```bash
uv sync
uv run artel acp
```

`artel acp` uses stdin and stdout only. It is meant to be spawned by an ACP-capable client rather than used interactively in a terminal.

## What Artel exposes over ACP

Artel's ACP entrypoint supports the core session flow you need for interactive coding clients:

- initialize and authenticate
- create, load, list, resume, fork, and cancel sessions
- send prompts and receive streamed text and reasoning updates
- track tool calls, tool results, and usage updates
- rename sessions when Artel generates or updates a title

Sessions use the same Artel session store as the rest of the CLI, so ACP clients can continue existing work instead of creating a separate history silo.

## Session scoping

ACP sessions are workspace-aware:

- when a client passes `cwd`, Artel resolves relative paths against the process working directory
- `list`, `load`, and `resume` only surface sessions whose project directory overlaps the requested workspace
- a new ACP session starts with the default Artel model and thinking level from your config

This keeps ACP clients aligned with the same project boundaries that Artel uses in its normal local workflows.

## Permission modes

Artel exposes two ACP session modes:

- `ask` — protected tool calls require approval from the client
- `code` — protected tool calls are auto-approved for the rest of the session

If the client approves a tool call with an “approve for session” style action, Artel switches the session into `code` mode automatically.

## Per-session controls

ACP clients can adjust a few session-scoped settings without editing config files:

- `mode` — switches between `ask` and `code`
- `model` — chooses from the models currently available in your Artel config and credentials
- `thinking` — sets the reasoning budget to one of `off`, `minimal`, `low`, `medium`, `high`, or `xhigh`

These controls are exposed as ACP session config options, so a compatible client can present them directly in its UI.

## Prompt and tool-call behavior

During a prompt, Artel streams:

- assistant text deltas
- reasoning deltas
- tool call starts, including file locations when they can be inferred
- tool call completion or failure output
- usage updates after the turn completes

This lets ACP clients render an experience close to the built-in Artel UI while still keeping the agent in a separate process.

## Example: connect Artel to Zed

Zed can run custom ACP agents from its `settings.json` file. Add Artel under `agent_servers`:

```json
{
  "agent_servers": {
    "artel": {
      "type": "custom",
      "command": "artel",
      "args": ["acp"],
      "env": {}
    }
  }
}
```

If you launch Artel with a different command on your machine, keep the same structure but replace `command` and `args` with whatever you normally use to run `artel acp`.

After saving the settings:

1. open Zed's agent panel
2. create a new external agent thread
3. select `artel` from the agent list

If the integration does not start cleanly, open Zed's ACP log view and inspect the handshake and tool-call traffic before debugging Artel itself.

## Example: use Artel from VS Code

VS Code does not currently have native ACP support. Its built-in custom agents are a different feature: they are GitHub Copilot agent definitions stored as `.agent.md` files, not external ACP subprocesses.

If you want to experiment with ACP in VS Code today, the relevant option is the preview community extension `VSCode ACP`.

Current caveat: the released extension auto-detects supported agents from `PATH`, and its Marketplace page currently lists only these commands:

- `opencode`
- `claude`

That means `artel acp` is not yet a plug-and-play choice in the released VS Code ACP flow.

For now, the practical guidance is:

1. use Zed if you want a native ACP editor integration today
2. run `artel acp` directly in a terminal when you want to verify the ACP entrypoint itself
3. use VS Code custom agents plus MCP if your goal is a first-party VS Code workflow rather than ACP specifically

Once the VS Code ACP extension adds generic custom command registration or built-in Artel detection, the Artel side of the setup should still be the same command:

```bash
artel acp
```

## Notes and limitations

- `artel acp` currently exposes ACP over stdio only
- unknown ACP extension methods are rejected
- if startup fails with `ACP support requires the 'agent-client-protocol' package`, install dependencies first with `uv sync`
