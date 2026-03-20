# Discovery: ACP-first fit across Artel ACP, ACP protocol, and TUI

## Status

Discovery draft

## Goal

Evaluate how well Artel's current implementation fits an ACP-native architecture where:

- ACP is the canonical external control-plane contract
- local and remote UIs become thinner clients over shared runtime semantics
- transport-specific code remains, but product semantics stop drifting across surfaces

## Sources reviewed

Repository sources:

- `packages/artel-server/src/artel_server/acp.py`
- `packages/artel-server/src/artel_server/acp_commands.py`
- `packages/artel-server/src/artel_server/server.py`
- `packages/artel-core/src/artel_core/control.py`
- `packages/artel-core/src/artel_core/cli.py`
- `packages/artel-tui/src/artel_tui/app.py`
- `packages/artel-tui/src/artel_tui/local_server.py`
- `docs/acp.md`
- `docs/run-modes.md`
- `tests/test_acp_phase7.py`
- `tests/test_acp_protocol_integration.py`

ACP references:

- installed `acp` Python package and schema in the local environment
- public ACP docs snippets from `agentclientprotocol.com`

## Executive summary

Short version:

- **Artel ACP is already a real runtime surface**, not a toy wrapper.
- **ACP matches Artel's core session/turn/tool/approval model well enough** to be a credible canonical control plane.
- **The current TUI architecture is not ACP-shaped today**. Local TUI runs the agent in-process. Remote TUI uses a custom WebSocket event stream plus a parallel REST control plane.
- **Remote TUI already looks conceptually close to ACP**, but it uses a bespoke protocol with similar events instead of ACP messages.
- **The biggest blockers to an ACP-first architecture are parity gaps**, not feasibility gaps.

Most important parity gaps discovered:

1. ACP currently does not expose all TUI-relevant capabilities.
2. TUI image/attachment flows exist locally and remotely, but ACP currently does not advertise or handle image prompt content.
3. Remote/server control flows such as rules, tasks, notes, schedules, MCP, server selection, and extension commands live in REST/slash-command land, not in a coherent ACP control plane.
4. Artel docs claim ACP slash-command advertisement via `available_commands_update`, but the current ACP implementation does not emit that update.
5. Key ACP features used by Artel are still marked unstable in the ACP schema, so an ACP-first strategy currently depends on unstable protocol areas.

Conclusion:

> The concept is viable. The repository already contains enough evidence that ACP can be the canonical runtime contract. But the current implementation is only partway there, and the TUI is not yet a thin ACP client.

## What the ACP protocol gives Artel today

From the ACP docs and installed schema, the stable baseline is strong for interactive agents:

- `initialize`
- `session/new`
- `session/prompt`
- `session/cancel`
- `session/update`

That baseline already maps well to Artel's core loop:

- create session
- submit user turn
- stream turn progress
- cancel turn
- finish turn with a stop reason

The ACP schema also includes higher-level pieces that are highly relevant to Artel:

- content blocks for text, image, resource links, and embedded resources
- tool call lifecycle updates
- permission requests
- usage updates
- mode/config updates
- session info updates
- available command updates
- plan updates

There are also client-side capabilities in ACP for file and terminal operations, but Artel does not currently use them in its ACP implementation.

## Important protocol reality: unstable features matter here

Artel's current ACP implementation relies on protocol areas that the installed schema explicitly labels unstable:

- `session/list`
- `session/resume`
- `session/fork`
- model state in session responses
- usage in `PromptResponse`

Additionally, `run_acp()` currently starts the agent with:

- `run_agent(..., use_unstable_protocol=True)` in `packages/artel-server/src/artel_server/acp.py`

That matters for strategy.

If Artel goes ACP-first, it likely needs one of these stances:

1. accept ACP unstable features as a practical dependency for now
2. constrain the canonical contract to the stable subset and use ACP extension methods for the rest
3. help shape ACP evolution and treat Artel's usage as part of that feedback loop

## Current Artel ACP implementation

Artel's ACP entrypoint is not a thin shim over CLI text output. It exposes meaningful runtime semantics.

### What it already does well

#### 1. Session lifecycle is substantial

Implemented in `packages/artel-server/src/artel_server/acp.py`:

- `initialize`
- `authenticate`
- `new_session`
- `load_session`
- `list_sessions`
- `resume_session`
- `fork_session`
- `cancel`

It also reuses the main Artel session store, so ACP clients participate in the same persisted session history as other surfaces.

#### 2. Workspace-aware session scoping exists

ACP session list/load/resume behavior filters by workspace overlap via:

- `_resolve_cwd()`
- `_workspace_matches()`

This is exactly the kind of cross-surface session semantics an ACP-first design wants.

#### 3. Turn streaming maps cleanly

During `prompt()` Artel emits ACP `session/update` notifications for:

- assistant text deltas
- reasoning deltas
- tool call start
- tool call completion/failure
- session title updates
- usage updates

The mapping from internal `AgentEventType` to ACP updates is clear and direct.

#### 4. Tool approval flow is real and structured

Artel uses ACP permission requests through:

- `PermissionBroker`
- `ToolCallTracker`
- `Client.request_permission()`

It keeps stable tracked ACP tool-call IDs and updates status across:

- pending
- in_progress
- completed / failed

This is one of the strongest signs that ACP is a good fit for Artel's runtime.

#### 5. Session-scoped runtime controls are exposed

Artel ACP exposes:

- mode (`ask` / `code`)
- model
- thinking level

through ACP session config options and update notifications.

That is exactly the sort of surface-neutral runtime control an ACP-native design should centralize.

### Verified test coverage

The ACP tests provide good evidence that the implementation is meaningful, not aspirational.

Examples from `tests/test_acp_protocol_integration.py` validate:

- mode/config update notifications
- workspace-aware session persistence and resume after restart
- absolute file locations for tool calls
- permission flow using tracked tool-call IDs

### What is missing or partial in ACP right now

#### 1. ACP command advertisement is documented, but not implemented

`docs/acp.md` says Artel advertises slash commands via `available_commands_update`.

But in `packages/artel-server/src/artel_server/acp.py` there is currently no emission of:

- `AvailableCommandsUpdate`
- `update_available_commands(...)`

Searches in the file show no actual use of that update path.

So there is a docs/implementation gap here.

#### 2. ACP prompt input is effectively text-only today

Artel ACP advertises:

- `PromptCapabilities(embedded_context=True)`

But it does **not** advertise image support, and the prompt ingestion path converts incoming content through `_prompt_to_text()` and `_extract_block_text()`.

That extraction logic handles:

- text blocks
- resource/embedded-resource text
- resource URIs

It does **not** handle image content as a first-class prompt input.

This creates a clear gap versus TUI local/remote image attachment support.

#### 3. ACP does not expose the broader remote control plane

Artel ACP focuses on session-centric interaction. It does not currently provide a coherent ACP-native surface for:

- rules management
- MCP control/config
- schedules control
- server registry / server selection
- extension admin flows
- remote credential import/login orchestration
- rich server diagnostics

Some of these may belong outside core session ACP. But if the goal is "all Artel capabilities through ACP", they need a plan.

#### 4. No ACP-native steering equivalent is present

TUI remote mode supports mid-run steering over the custom WebSocket protocol with `type: "steer"`.

ACP's standard agent interface, as used here, exposes `cancel` but no Artel-specific steer request.

That means one of two things would be needed for ACP-first parity:

- define an ACP extension method for steering
- or drop / narrow steering semantics in ACP-backed surfaces

#### 5. No plan updates are emitted

The ACP schema supports `AgentPlanUpdate`, but Artel ACP does not currently emit plan updates.

This is not a blocker, but it is a potentially useful fit for Artel task planning workflows.

## Current Artel TUI architecture

The TUI has two materially different runtime paths.

## Local TUI path

In `packages/artel-tui/src/artel_tui/app.py`:

- `on_mount()` calls `_init_local_session()` when not in remote mode
- `_init_local_session()` bootstraps runtime directly with `bootstrap_runtime(...)`
- it creates an in-process `AgentSession` via `create_agent_session_from_bootstrap(...)`
- `_run_local()` consumes `self._session.run(...)` directly and renders internal `AgentEventType` events

So the local TUI today is **not** using ACP semantics as an adapter layer.

It is directly coupled to:

- bootstrap/runtime construction
- session store access
- `AgentSession`
- internal event types
- local permission callback wiring

That is efficient and straightforward, but not ACP-first.

## Remote TUI path

The remote TUI path is split across:

- custom WebSocket streaming in `packages/artel-server/src/artel_server/server.py`
- REST control APIs consumed through `packages/artel-core/src/artel_core/control.py`
- TUI logic in `_run_remote()`, `_remote_control()`, `_resume_remote_session()`, `_sync_remote_session_state()`, etc.

### Remote streaming path

Remote turns use custom JSON messages over WebSocket such as:

- `message`
- `cancel`
- `steer`
- `approve_tool`

and receive streamed event payloads such as:

- `reasoning_delta`
- `text_delta`
- `tool_call`
- `tool_result`
- `permission_request`
- `session_updated`
- `board_event`
- `status`
- `done`
- `error`

This is conceptually very close to ACP.

But it is not ACP.

### Remote control plane path

The TUI also depends on REST for many control operations:

- get session state
- list sessions
- get session messages
- rules CRUD and session overrides
- tasks and notes
- prompts and skills
- schedules
- MCP
- extension command discovery/invocation
- server info and diagnostics

`packages/artel-core/src/artel_core/control.py` even says this module is:

> the first step towards a shared control layer used by TUI, Web UI, and future desktop surfaces

and explicitly notes that streaming and local-runtime control will be added later.

That is useful evidence: the repo already recognizes the need for a shared control plane. It is just currently REST/WebSocket-shaped rather than ACP-shaped.

## Fit analysis: where ACP maps well

## 1. Core conversational runtime

Strong fit.

Artel's core runtime concepts map naturally to ACP:

- session
- turn
- streamed assistant output
- reasoning output
- tool calls
- tool results
- permission requests
- cancellation
- title / session metadata updates
- usage updates

This is the clearest argument in favor of ACP-native architecture.

## 2. Resume/list/fork workflows

Good fit, but presently leaning on unstable ACP areas.

Artel already implements these over ACP and tests them. The concept is validated. The main caveat is protocol maturity.

## 3. Mode/model/thinking controls

Strong fit.

These are exactly the sort of session-scoped knobs that work better as a shared protocol contract than as ad hoc per-surface UI logic.

## 4. Remote streaming semantics

Very strong fit.

Remote TUI WebSocket events already resemble an ACP event stream closely enough that migration should be evolutionary, not revolutionary.

In other words:

- the remote server already knows how to surface runtime semantics
- the TUI already knows how to render them
- the current mismatch is mostly protocol shape, not product shape

## Fit analysis: where ACP does not yet cover Artel well enough

## 1. Images / attachments

This is the most obvious functional gap.

TUI local supports:

- `/image <path>`
- `/image-paste`
- pending attachment UI
- passing image attachments into `session.run(...)`

Remote TUI supports attachments over the custom WebSocket payload too.

ACP in Artel currently does not advertise image prompt capability and does not treat image content blocks as first-class input.

If Artel wants ACP-first honestly, image/session attachment semantics need to exist in ACP usage too.

## 2. Shared control-plane features beyond the turn loop

The TUI exposes much more than conversation turns:

- task board
- operator notes
- rules editing
- MCP inspection/reload
- schedules control
- saved server registry and dock
- extension commands
- OAuth / credential forwarding helpers
- project switching

Some of these are UI-only and need not become ACP semantics.
But many are real product capabilities.

Today they are split across:

- local direct calls
- remote REST calls
- slash commands
- bespoke UI behavior

That fragmentation is the biggest obstacle to a clean ACP-native story.

## 3. Local TUI still owns too much runtime logic

The local TUI is currently a first-party client plus runtime host in one process.

That means local mode owns logic for:

- bootstrapping provider/runtime/session
- restoring local session history
- switching models directly
- local permission callbacks
- direct command execution and extension command dispatch

An ACP-first design would likely move much of that behind a local ACP adapter or in-process ACP bridge.

## 4. Extension command parity is not solved

Remote TUI can discover and run session extension commands via REST.

ACP currently documents built-in slash commands only, and the code does not actually advertise them yet.

There is no unified ACP-native answer for extension commands today.

## 5. Steering is an open design problem

Remote TUI has a useful mid-run steer capability. ACP as used here does not.

This is exactly the kind of feature that will force a decision in an ACP-first architecture:

- standardize an ACP extension
- redesign the UX around cancel-and-reprompt
- keep steering as a surface-specific side channel

## Capability comparison

| Area | Local TUI | Remote TUI | ACP today | Fit to ACP-first |
|---|---|---|---|---|
| Session create/load/resume | Yes | Yes | Yes | Strong |
| Turn streaming | Yes | Yes | Yes | Strong |
| Tool lifecycle | Yes | Yes | Yes | Strong |
| Permission requests | Yes | Yes | Yes | Strong |
| Cancel | Yes | Yes | Yes | Strong |
| Fork/rewind | Yes | Yes | Yes | Good |
| Model/thinking controls | Yes | Yes | Yes | Strong |
| Image input / attachments | Yes | Yes | Partial / no real parity | Weak today |
| Slash command surfacing | Yes | Yes | Partial, docs ahead of code | Partial |
| Rules management | Yes | Yes | No coherent ACP surface | Gap |
| Tasks / notes | Yes | Yes | Only via slash-command execution path | Partial |
| MCP / schedules / delegation controls | Yes | Yes | Mostly not ACP-native | Gap |
| Extension command discovery | Yes | Yes | No parity | Gap |
| Server dock / multi-server UX | N/A local | Yes | No | UI-local / separate concern |
| Mid-run steering | Yes local | Yes remote | No standard ACP path | Gap |

## What already looks reusable for an ACP-native migration

## 1. ACP and WebSocket event models are already cousins

The custom remote stream and ACP both revolve around the same domain events:

- text delta
- reasoning delta
- tool call
- tool result
- permission request
- session metadata update
- usage/done

This is excellent migration terrain.

## 2. Session persistence semantics are already shared

ACP and the rest of Artel already use the same session store.

That is exactly the kind of shared runtime behavior that keeps multi-surface systems coherent.

## 3. The TUI renderer mostly wants semantic events, not transport details

The TUI code in `_run_local()` and `_run_remote()` mostly translates runtime events into widgets:

- message widgets
- reasoning blocks
- tool cards
- permission panel state
- footer usage/context updates

That suggests the TUI could consume a more unified ACP-shaped event stream without dramatic UI redesign.

## 4. The repository already has a concept of a shared control layer

`artel_core.control` is early and REST-based, but it is evidence that the architecture already wants convergence.

## Main conceptual conclusion

ACP-first is plausible for Artel **if the target is defined carefully**.

The strongest version of the idea is not:

> every line of code must go through ACP

It is:

> Artel should have one canonical external runtime/control-plane contract, and ACP is the leading candidate.

The discovery work supports that claim for:

- session lifecycle
- turn lifecycle
- streaming
- tool execution
- permissions
- resume/fork/list
- session-scoped controls

The discovery work does **not** yet support the stronger claim that:

- the TUI is already a thin ACP client
- all major Artel capabilities are already exposed through ACP
- switching TUI/web/gui to ACP would be trivial today

## Risks specific to an ACP-first migration

## 1. Overcommitting to unstable ACP features

Artel already depends on unstable ACP features for important workflows.

This is manageable, but it means "ACP canonical" currently also means "accept protocol churn risk".

## 2. Losing capabilities during convergence

If Artel re-platforms around ACP too aggressively before adding parity for:

- images
- extension commands
- rules / MCP / schedules control
- steering

then ACP-first could become a regression in practice.

## 3. Confusing session ACP with product control plane ACP

The turn loop maps beautifully to ACP.
The broader product control plane is messier.

It may be correct to split the design into:

- core session ACP contract
- ACP extension methods for Artel-specific control-plane operations

rather than trying to cram every admin/control feature into prompt text or ad hoc slash commands.

## Recommended next steps

## 1. Define the target ACP contract explicitly

Before migrating clients, decide which capabilities must be first-class in ACP:

- images / attachments
- steering or replacement behavior
- extension commands
- rules / MCP / schedules / delegation controls
- task board / notes semantics

## 2. Normalize one internal event model against ACP semantics

The current remote WebSocket stream should be treated as proof that the domain model exists.

Next step:

- define a canonical session event vocabulary aligned with ACP updates
- make both ACP stdio and remote transports emit that same semantic model

## 3. Add ACP parity for images and command advertisement

These are the most obvious near-term gaps:

- actually emit `available_commands_update`
- support or explicitly reject image prompt content in a principled way

## 4. Decide how Artel-specific control-plane features map into ACP

Likely options:

- ACP extension methods
- richer available commands with structured inputs
- some combination of both

## 5. Prototype a local ACP adapter for TUI

A good architecture spike would be:

- keep the TUI UI exactly as-is
- replace direct local `AgentSession` driving with an in-process or local-IPC ACP adapter
- compare complexity, performance, and feature loss

That would test the ACP-first thesis directly.

## 6. Consider a remote ACP transport strategy

The remote TUI currently uses custom WebSocket plus REST.

A future direction could be one of:

- ACP-over-WebSocket for remote interactive clients
- ACP-over-local IPC for local TUI
- ACP stdio for editor subprocess integrations
- REST retained only for non-session administrative surfaces until ACP extensions catch up

## Final assessment

The repository discovery supports this statement:

> ACP can realistically become Artel's canonical session/runtime control plane.

The repository discovery does **not** support this stronger statement yet:

> all current TUI/remote/product capabilities are already close enough to ACP that UI surfaces become trivial wrappers now.

The current reality is better described as:

- **session runtime:** already close to ACP-native
- **remote event transport:** structurally similar to ACP, but bespoke
- **local TUI runtime:** still direct and non-ACP
- **broader control plane:** fragmented across REST, slash commands, and UI-specific logic

That is a promising starting point for an ACP-first architecture, but it is still a migration project rather than a rename.
