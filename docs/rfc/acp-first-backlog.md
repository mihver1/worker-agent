# ACP-first implementation backlog

## Status

Draft

## Purpose

Convert the ACP-first architecture RFC, discovery findings, and gap matrix into a practical implementation backlog.

This backlog is ordered by architectural leverage, not by estimated ease.

## Principles

1. Preserve current Artel product scope while reducing semantic duplication.
2. Prefer convergence on one canonical runtime contract over adding another parallel surface.
3. Do not break strong existing workflows in TUI, remote mode, or ACP clients while migrating.
4. Close docs/implementation gaps early so the product narrative stays trustworthy.
5. Use ACP where it fits naturally; use ACP extensions deliberately where core protocol coverage is insufficient.

## Workstream 1 — Fix current ACP/docs mismatches

### Goal

Make the existing ACP surface honest, testable, and internally coherent before larger migration work.

### Tasks

- Emit `available_commands_update` from the ACP implementation if docs continue to claim command advertisement.
- If command advertisement is intentionally deferred, update `docs/acp.md` to remove or narrow the claim.
- Audit `docs/acp.md` against actual ACP behavior for:
  - image support
  - embedded context behavior
  - slash-command coverage
  - unstable session capabilities
- Document ACP unstable-protocol reliance explicitly in docs and/or code comments.
- Add tests that verify command advertisement behavior if implemented.

### Exit criteria

- ACP docs match real runtime behavior.
- Any advertised ACP command/capability has automated test coverage.

## Workstream 2 — Define the canonical Artel session event model

### Goal

Normalize Artel runtime semantics around a single event vocabulary that ACP can express directly.

### Tasks

- Document the canonical event model for:
  - user turn start
  - assistant text delta
  - reasoning delta
  - tool call start
  - tool call progress/update
  - tool call completion/failure
  - permission request
  - session metadata update
  - usage/context update
  - cancellation / terminal stop reason
- Map current internal `AgentEventType` plus server-side status events onto that vocabulary.
- Identify which current remote WebSocket events are:
  - equivalent to ACP updates
  - Artel-specific extensions
  - UI-local concerns that should not become protocol semantics
- Decide whether plan/task-progress semantics should use ACP `AgentPlanUpdate`, Artel extension events, or remain local.

### Exit criteria

- One written canonical event vocabulary exists.
- ACP and remote server transports can both be described as projections of the same event model.

## Workstream 3 — Close core ACP parity gaps for normal turns

### Goal

Bring ACP to parity with the most important first-party conversational capabilities.

### Tasks

- Add first-class ACP handling for image prompt input if Artel wants ACP parity with TUI image flows.
- Advertise ACP prompt capabilities accurately for:
  - image
  - embedded context
  - any other supported content types
- Decide how attachments should be represented in ACP-backed sessions for persistence and replay.
- Ensure resumed/replayed ACP sessions reflect attachments consistently if supported.
- Validate tool-call metadata quality, including file locations and raw input/output.
- Decide whether usage/context updates should include richer context window information or remain current minimal form.

### Exit criteria

- ACP supports the intended Artel turn model, including any chosen attachment/image behavior.
- ACP prompt capability flags reflect reality.

## Workstream 4 — Define ACP-native control-plane strategy for non-turn capabilities

### Goal

Stop scattering real product capabilities across REST-only, slash-only, and UI-only paths without a clear ACP story.

### Tasks

Categorize capabilities into three buckets:

#### Bucket A — should become first-class ACP operations or ACP extensions

Candidates:

- rules inspection and mutation
- session/project switching
- extension command discovery and invocation
- steering, if Artel wants to preserve it across clients
- maybe schedules/delegation status if they are considered session/runtime behavior

#### Bucket B — can remain ACP-advertised commands over prompt text

Candidates:

- lightweight git/status helpers
- worktree helpers
- task board shortcuts
- notes shortcuts

#### Bucket C — should remain surface-local or separate admin control plane

Candidates:

- server dock UI
- cmux-specific helpers
- local clipboard affordances
- pure layout/focus/navigation behavior

Then, for Bucket A:

- define extension method names or ACP command descriptors
- define request/response shapes
- define session/update notifications if state changes asynchronously
- define compatibility behavior when a client does not understand an extension

### Exit criteria

- Each major capability is assigned to a control-plane strategy intentionally.
- Artel has a clear answer for rules, extensions, and steering.

## Workstream 5 — Align remote interactive transport with ACP semantics

### Goal

Reduce duplication between the bespoke remote WebSocket protocol and ACP.

### Tasks

- Inventory current remote WebSocket messages and map each to ACP-equivalent semantics.
- Reduce naming drift between remote event types and ACP session updates.
- Decide whether remote interactive mode should evolve toward:
  - ACP-over-WebSocket
  - ACP-over-adapter with server-side translation
  - or a bespoke transport carrying the same canonical event model
- Migrate remote TUI rendering code to consume a normalized event stream abstraction instead of protocol-specific cases where feasible.
- Minimize REST dependence for session-runtime concerns that ACP can already represent.

### Exit criteria

- Remote interactive transport no longer defines a semantically separate turn/runtime model.
- Remote TUI event handling is visibly closer to ACP semantics than today.

## Workstream 6 — Prototype a local ACP-backed TUI path

### Goal

Test the core thesis: can the first-party TUI run as a thin client over ACP-shaped semantics without unacceptable complexity or performance loss?

### Tasks

- Introduce a local ACP transport option, such as:
  - in-process adapter
  - local pipe/socket transport
  - loopback stdio subprocess prototype
- Build a TUI-side session/event adapter that consumes ACP-shaped updates instead of direct `AgentSession.run(...)` events.
- Keep rendering logic unchanged as much as possible; only swap the runtime/control source.
- Compare:
  - startup cost
  - streaming latency
  - complexity of permission handling
  - complexity of attachment support
  - session restore/resume behavior
- Decide whether local TUI should fully migrate or keep a direct fast path behind the same semantics.

### Exit criteria

- There is at least one working local ACP-backed TUI prototype.
- The team has evidence, not speculation, about viability and tradeoffs.

## Workstream 7 — Extension command and capability discovery parity

### Goal

Make ACP a credible control plane for Artel's extensibility story.

### Tasks

- Define how ACP clients discover extension-provided commands.
- Decide whether extension commands appear as:
  - advertised ACP available commands
  - ACP extension methods
  - prompt-text slash commands only
- Ensure remote and ACP clients do not have materially different extension-command affordances without a reason.
- Add tests covering discovery and invocation behavior.

### Exit criteria

- ACP has a coherent extensibility story, not just built-in command coverage.

## Workstream 8 — Steering and long-running run control

### Goal

Resolve the gap between TUI run-control affordances and ACP run-control semantics.

### Tasks

- Decide whether steering is a retained product feature across all clients.
- If yes, define an ACP extension for mid-run steering.
- If no, document the product decision and align TUI/remote behavior over time.
- Review cancellation, abort, and stop-reason semantics across:
  - local TUI
  - remote TUI
  - ACP clients
- Ensure tests cover long-running run control and cancellation edge cases.

### Exit criteria

- Artel has one explicit policy for run control beyond plain cancel.

## Workstream 9 — Rationalize broader control planes

### Goal

Clarify what remains in REST/admin surfaces versus what moves into ACP-native workflows.

### Tasks

- Review `artel_core.control` and current REST endpoints.
- Split APIs into:
  - session/runtime control
  - administrative/server management
  - pure product metadata/config surfaces
- Migrate session/runtime control toward ACP where appropriate.
- Keep clearly administrative surfaces separate if that reduces protocol confusion.
- Update docs to explain the boundary.

### Exit criteria

- REST and ACP have clearer, less overlapping roles.
- Session/runtime semantics are no longer fragmented for historical reasons only.

## Workstream 10 — Docs, naming, and product positioning

### Goal

Ensure the product narrative matches the implementation strategy without overstating maturity.

### Tasks

- Update `README.md`, `docs/run-modes.md`, and `docs/acp.md` as migration milestones land.
- Decide how strongly to position ACP in user-facing docs:
  - canonical integration/control plane
  - internal canonical runtime contract
  - or primary user-facing mode
- Avoid claiming that TUI/web/GUI are trivial wrappers until architecture and parity justify it.
- Keep product-scope docs aligned with actual shipped surfaces.

### Exit criteria

- Docs describe ACP-first accurately and conservatively.
- Product messaging does not outrun implementation.

## Suggested implementation order

### Phase A — credibility and baseline

1. Workstream 1 — fix docs/runtime mismatches
2. Workstream 2 — define canonical event model
3. Workstream 3 — close critical turn-model parity gaps

### Phase B — architecture convergence

4. Workstream 4 — classify control-plane capabilities
5. Workstream 5 — align remote transport with ACP semantics
6. Workstream 7 — extension command parity
7. Workstream 8 — steering/run control policy

### Phase C — client migration proof

8. Workstream 6 — local ACP-backed TUI prototype
9. Workstream 9 — rationalize REST vs ACP boundaries
10. Workstream 10 — docs and positioning cleanup

## Recommended first concrete tickets

If work starts immediately, the highest-signal first tickets are:

1. **Implement or remove `available_commands_update` claim**
2. **Write canonical session event vocabulary doc**
3. **Define ACP image/attachment support decision**
4. **Write control-plane capability classification table**
5. **Design steering policy: extension vs de-scope**
6. **Prototype remote event normalization layer before changing UI rendering**

## Definition of success

This backlog should be considered successful when Artel can honestly say:

- ACP is the canonical control-plane contract for session runtime behavior
- first-party clients consume substantially shared semantics across local, remote, and ACP flows
- the remaining non-ACP surfaces are distinct for transport or UX reasons, not because runtime semantics forked accidentally

## Definition of failure

This backlog would fail if Artel ends up with:

- ACP as yet another parallel surface
- a bespoke remote event model still drifting from ACP
- a local TUI still tightly bound to private runtime APIs with no shared semantic layer
- docs that claim ACP-first while key product capabilities remain unavailable or inconsistent
