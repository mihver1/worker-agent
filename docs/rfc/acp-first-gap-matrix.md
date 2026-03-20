# ACP-first gap matrix

## Status

Draft

## Purpose

Turn the ACP-first discovery into an implementation-oriented comparison matrix.

This document compares major Artel capabilities across:

- local TUI
- remote TUI / server flows
- ACP today

For each area it identifies:

- current state
- practical gap
- migration note toward an ACP-native architecture

## Reading guide

Status labels used below:

- **Full** — implemented and meaningfully usable today
- **Partial** — some support exists, but parity or shape is incomplete
- **None** — no meaningful support in that surface

## Matrix

| Capability | Local TUI | Remote TUI / server | ACP today | Gap | Migration note |
|---|---|---|---|---|---|
| Session creation | Full | Full | Full | Low | Already a strong ACP fit. |
| Session load / resume | Full | Full | Full | Low | Already implemented in ACP; depends partly on unstable ACP areas. |
| Session list | Full | Full | Full | Low | Good ACP fit already. |
| Session fork / rewind | Full | Full | Full | Low | ACP supports it today in Artel; watch unstable protocol status. |
| Session cancel | Full | Full | Full | Low | Already aligned well with ACP. |
| Workspace-aware session scoping | Full | Full | Full | Low | ACP implementation already has explicit workspace matching. |
| Assistant text streaming | Full | Full | Full | Low | Strong existing fit. |
| Reasoning streaming | Full | Full | Full | Low | Strong existing fit. |
| Tool-call lifecycle | Full | Full | Full | Low | ACP mapping is already meaningful and tested. |
| Permission requests / approval | Full | Full | Full | Low | ACP request/response model fits very well. |
| Approve-for-session / mode switching | Full | Full | Full | Low | ACP already exposes ask/code semantics. |
| Session mode control | Full | Full | Full | Low | ACP config option is already present. |
| Session model control | Full | Full | Full | Low | ACP config option is already present. |
| Session thinking control | Full | Full | Full | Low | ACP config option is already present. |
| Session title update | Full | Full | Full | Low | ACP emits session info updates. |
| Usage / context reporting | Full | Partial | Full | Medium | ACP reports usage; remote TUI uses custom status/done events and local context logic. |
| Persisted session history reuse | Full | Full | Full | Low | Shared store is already a big ACP-native win. |
| Replay history on load/resume | Full | Full | Full | Low | ACP load/resume already replays history via updates. |
| Image attachments / vision input | Full | Full | Partial | High | ACP path needs real image content handling and capability advertisement. |
| Embedded resource prompt content | None/UI-local | None/UI-local | Partial | Medium | ACP can accept embedded/resource blocks, but TUI flows do not yet revolve around that capability. |
| Slash command execution | Full | Full | Partial | Medium | ACP can execute slash commands through prompt text, but coverage is intentionally limited. |
| Slash command advertisement | Full | Full | None in practice | High | Docs claim `available_commands_update`, but current ACP implementation does not emit it. |
| Built-in worktree helpers | Full | Full | Partial | Medium | Works through ACP slash text, but not as richer ACP-native command/control surface. |
| Git status/diff/rollback helpers | Full | Full | Partial | Medium | Same as above; usable via slash-command path. |
| Tasks board read/update | Full | Full | Partial | Medium | Available through ACP slash command flow, not through dedicated ACP operations. |
| Operator notes read/append | Full | Full | Partial | Medium | Same limitation as tasks. |
| Rules list/edit/override | Full | Full | None | High | ACP lacks coherent rule-management surface today. |
| MCP status / reload / config flows | Full | Full | Partial | High | Some slash coverage exists; no full ACP-native control plane. |
| Schedules control | Full | Full | Partial | High | Some slash coverage exists; no structured ACP control-plane contract. |
| Delegation / orchestration inspection | Full | Full | Partial | Medium | ACP slash-command path exists, but not a first-class streaming/status model. |
| Extension command discovery | Full | Full | None | High | Remote TUI uses REST for session commands; ACP has no parity path yet. |
| Extension command invocation | Full | Full | None | High | Needs ACP-native command discovery/invoke strategy. |
| OAuth / provider login helpers | Full | Full | None | High | Currently highly surface-specific; ACP story not defined. |
| Credential forwarding | None local | Full | None | High | Remote TUI helper has no ACP equivalent. Probably remains outside session ACP unless intentionally designed. |
| Project/cwd switching | Full | Full | Partial | Medium | ACP session creation/load are cwd-aware, but live project switching is not a first-class ACP workflow. |
| Mid-run steering | Full | Full | None | High | Requires ACP extension or explicit product decision not to preserve parity. |
| Local run loop abstraction | Direct | N/A | None | High | Local TUI still consumes `AgentSession` directly instead of ACP-shaped events. |
| Remote run loop abstraction | N/A | Custom WS | None | High | Remote stream is ACP-like but bespoke. |
| Shared cross-surface control plane | Partial | Partial | Partial | High | Currently fragmented across direct calls, REST, custom WS, and ACP. |
| Server dock / multi-server browser | N/A | Full | None | Low / UI-local | Mostly UI concern; does not need to be ACP-native unless multi-server control becomes protocol scope. |
| TUI-specific panes, focus, keybindings | Full | Full | None | Low / UI-local | Should remain UI-local, not ACP scope. |
| cmux-only helpers | Full | Partial | None | Low / UI-local | Keep surface-specific unless there is a strong ACP reason. |

## Grouped analysis

## 1. Areas already close to ACP-native

These areas are strong candidates for canonical ACP semantics right now:

- session lifecycle
- turn lifecycle
- text and reasoning streaming
- tool lifecycle
- permission flow
- session mode/model/thinking controls
- session title updates
- persisted history reuse

These capabilities already provide the strongest foundation for an ACP-first direction.

## 2. Areas that work through ACP, but only indirectly

These areas are available through ACP mostly via slash-command-as-text patterns rather than a dedicated control-plane model:

- worktree helpers
- git helpers
- tasks board
- operator notes
- MCP inspection/reload
- schedules inspection/control
- delegation inspection

This means they are usable, but not yet ideal as a canonical protocol contract.

The architecture question here is:

- should these stay prompt/command-oriented,
- or should some of them become structured ACP extension methods or richer command descriptors?

## 3. Major parity gaps

These are the most important gaps if Artel wants to become ACP-native in a serious sense.

### Images / attachments

TUI local and remote support image attachment flows. ACP currently does not provide Artel parity for that path.

### Command advertisement

Artel docs say ACP advertises commands, but implementation currently does not emit `available_commands_update`.

### Rules / MCP / schedules / extensions as control plane

These capabilities are real product features, but they are not exposed as a coherent ACP-native control surface.

### Mid-run steering

Remote and local TUI support steering. ACP currently has no Artel path for it.

### Local and remote client architecture

Even where semantics match ACP well, the clients are not structured around ACP today:

- local TUI is direct/in-process over `AgentSession`
- remote TUI is custom WebSocket + REST

## Prioritization recommendation

If implementation work starts from this matrix, the most leverage likely comes from this order:

### Priority 1 — fix obvious ACP contract gaps

- implement actual `available_commands_update`
- decide and document ACP image/attachment handling
- document unstable ACP dependency explicitly

### Priority 2 — reduce semantic duplication in remote mode

- align remote WebSocket event vocabulary with ACP update semantics
- identify which REST session operations should become ACP-native first

### Priority 3 — close core feature parity gaps

- extension command discovery/invoke story
- steering story
- structured control-plane story for rules / MCP / schedules / delegation

### Priority 4 — prototype local ACP-backed TUI mode

- replace direct local `AgentSession` consumption with ACP-shaped events over local transport
- measure complexity and performance impact before broader migration

## Success criteria for closing the highest-value gaps

A credible ACP-first posture would look much stronger once these conditions are true:

1. ACP can represent normal text turns, reasoning, tool calls, approvals, resume, and fork reliably.
2. ACP can carry image/attachment turns with clear capability signaling.
3. ACP actually advertises available built-in commands if docs claim it does.
4. Remote interactive clients can consume ACP-shaped runtime events instead of a bespoke near-duplicate protocol.
5. At least one first-party client path uses ACP semantics end-to-end rather than direct internal runtime APIs.

## Bottom line

This matrix reinforces the discovery conclusion:

- **the session runtime is already close to ACP-native**
- **the product control plane is not yet ACP-native**
- **the TUI architecture is not yet a thin ACP client**

So the strategic question is no longer whether ACP can fit Artel.

It can.

The real question is which gaps Artel wants to close first to make ACP the canonical interface without sacrificing existing capability breadth.
