# Control-plane capability classification

## Status

Draft

## Purpose

Classify major Artel capabilities into intentional control-plane buckets for an ACP-first architecture.

This document answers a practical question:

> For each capability, should Artel model it as first-class ACP semantics, ACP-advertised commands, or surface-local behavior?

## Buckets

## Bucket A — First-class ACP operations or ACP extensions

Use this bucket when a capability is:

- part of core runtime/session behavior
- needed consistently across multiple clients
- likely to require structured requests/responses or async updates
- too important to hide behind freeform prompt text

## Bucket B — ACP-advertised commands over normal prompt text

Use this bucket when a capability is:

- useful across clients
- naturally command-shaped
- mostly synchronous or text-result oriented
- not worth the complexity of a dedicated protocol method yet

## Bucket C — Surface-local or separate admin control plane

Use this bucket when a capability is:

- mostly UI interaction logic
- host/device integration logic
- server-administration behavior rather than session runtime behavior
- too surface-specific to force into the canonical session contract

## Classification table

| Capability | Classification | Why | Notes |
|---|---|---|---|
| Session create/load/list/resume/fork/cancel | Bucket A | Core runtime/session lifecycle | Already mostly in ACP today |
| Session model/mode/thinking control | Bucket A | Canonical session state | Already in ACP today |
| Text/reasoning/tool/permission runtime events | Bucket A | Core runtime semantics | Already strong ACP fit |
| Image-bearing prompts / attachments | Bucket A | Part of normal turn model | Should not be a side-channel feature |
| Mid-run steering | Bucket A | Real run-control capability across clients | Likely ACP extension |
| Extension command discovery | Bucket A | Needed for parity across remote/ACP clients | Probably ACP extension or richer available command contract |
| Extension command invocation | Bucket A | Structured runtime capability | Avoid REST-only divergence |
| Rules session overrides and mutation | Bucket A | Real runtime/policy control | Likely ACP extension methods |
| Project/cwd switching for active session | Bucket A | Session runtime state | Could be ACP extension or config update |
| Schedules detailed control | Bucket A / B split | Listing may fit commands; mutation/runs may need structure | Split by complexity |
| Delegation/orchestration status streaming | Bucket A | Async multi-actor runtime state | Good candidate for structured events |
| MCP runtime status/config | Bucket A / B split | Inspect/reload may start as commands; richer config needs structure | Phase gradually |
| Worktree helpers | Bucket B | Naturally command-shaped text workflows | Keep as advertised commands initially |
| Git status/diff/rollback helpers | Bucket B | Command-oriented and text-result heavy | Fine as commands initially |
| Task board shortcuts | Bucket B | Useful in many clients but command-shaped | Structured API optional later |
| Operator notes shortcuts | Bucket B | Same as tasks | Structured API optional later |
| Simple schedule list/show/run/reload convenience | Bucket B | Good quick-command UX | May coexist with richer Bucket A surface later |
| Simple delegation inspection commands | Bucket B | Good command/menu fit | May coexist with richer Bucket A events later |
| Server dock / saved server browser | Bucket C | UI composition feature | Does not need ACP semantics |
| cmux split/browser helpers | Bucket C | Environment-specific UX | Keep local/surface-specific |
| Clipboard image paste affordance | Bucket C | Device/UI integration | Normalize result into Bucket A image turns |
| TUI panes, focus, keybindings | Bucket C | Pure UI concerns | Never force into ACP |
| Local credential forwarding UX | Bucket C | Surface-specific host integration | Separate from canonical session contract |
| Server management / diagnostics admin flows | Bucket C | Administrative, not session-runtime by default | Keep REST/admin unless needed in clients |

## Recommended treatment by bucket

## Bucket A recommendations

### Preferred implementation forms

- standard ACP methods when protocol already covers the need
- ACP extension methods when Artel needs richer behavior not covered by the standard
- structured `session/update` notifications for async state changes

### Good candidates for near-term design work

- steering
- extension command discovery/invocation
- rules control
- project switching
- orchestration/delegation updates

## Bucket B recommendations

### Preferred implementation forms

- `available_commands_update`
- command descriptors with human-readable descriptions and hints
- execution through normal prompt turns or a thin command execution wrapper

### Good candidates to keep command-oriented initially

- `/wt`
- `/git`, `/status`, `/diff`, `/rollback`
- `/tasks`, `/task-add`, `/task-done`
- `/notes`, `/note-add`
- lightweight MCP / schedules / delegates inspection commands

## Bucket C recommendations

### Preferred implementation forms

- keep local to TUI or specific client surfaces
- use separate admin/control APIs when truly needed
- normalize outputs back into Bucket A semantics when they affect runtime state

Example:

- `/image-paste` itself is Bucket C
- the resulting image-bearing user turn is Bucket A

## Specific decisions suggested now

## Decision 1 — Steering belongs in Bucket A

Reason:

- it changes active run behavior
- it should not remain a bespoke remote-only side channel if ACP-first is the direction

## Decision 2 — Extension command discovery must move toward Bucket A

Reason:

- remote TUI already treats extension commands as meaningful runtime capabilities
- ACP without an extension-command story will remain second-class for extensibility

## Decision 3 — Worktree/git/task/notes helpers can remain Bucket B initially

Reason:

- they already fit a command UX well
- forcing them into bespoke structured methods too early would slow more important convergence work

## Decision 4 — UI affordances should stay in Bucket C

Reason:

- ACP-first is about canonical runtime semantics, not UI monoculture

## Near-term implementation implications

Based on this classification, the next technical design work should focus on:

1. Bucket A definition for steering
2. Bucket A definition for extension command discovery/invocation
3. Bucket A definition for rules/session policy control
4. Bucket A or A/B split decision for MCP, schedules, and delegation
5. Better Bucket B advertisement for built-in commands across ACP clients

## Boundary rule of thumb

A useful heuristic:

- if the capability changes or reflects **session runtime state**, lean Bucket A
- if the capability is a **portable command-shaped convenience**, lean Bucket B
- if the capability is mainly **interaction design or host integration**, lean Bucket C

## Bottom line

This classification gives Artel a way to become ACP-first without falling into either extreme:

- not everything must become a dedicated protocol method
- but important runtime capabilities should stop living only in bespoke per-surface logic

That balance is probably the healthiest path to ACP-native architecture.
