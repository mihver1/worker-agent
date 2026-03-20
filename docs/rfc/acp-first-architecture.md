# RFC: ACP-native architecture for Artel

## Status

Draft

## Summary

This RFC proposes making ACP the canonical external control-plane interface for Artel sessions, turns, tools, approvals, orchestration, and resumable state.

Under this model:

- Artel keeps a shared core runtime model
- ACP becomes the primary protocol used to expose that runtime to other frontends
- user-facing surfaces such as the local TUI, remote clients, editor integrations, and future GUI layers should prefer thin-client designs over the same runtime semantics
- transport choice remains flexible: stdio, local IPC, WebSocket, or in-process adapters can all carry the same ACP-shaped interactions

This is an architectural direction, not a claim that the current checkout is already ACP-only or that all non-ACP surfaces should be removed.

## Motivation

Artel already supports multiple surfaces:

- local TUI via `artel`
- print mode via `artel -p`
- headless server mode via `artel serve`
- remote TUI via `artel connect`
- JSON-RPC via `artel rpc`
- ACP via `artel acp`

As a product grows across multiple surfaces, the main risk is semantic drift:

- one surface supports a lifecycle that another does not
- session state differs across transports
- tool-call, approval, and streaming behavior diverge
- docs and tests describe different realities

An ACP-native approach aims to reduce that drift by making one protocol the canonical way to express runtime behavior outside the core runtime.

## Goals

1. Define a single canonical external control plane for Artel runtime behavior.
2. Make ACP the preferred integration contract for editor, IDE, and custom client workflows.
3. Let TUI, remote clients, and future GUI layers consume the same session and event semantics.
4. Minimize product logic duplicated in surface-specific adapters.
5. Improve consistency for session lifecycle, streaming, tool use, approvals, resume, and orchestration.
6. Keep Artel's supported user-facing surfaces aligned with the current product scope while simplifying their implementation strategy over time.

## Non-goals

This RFC does not propose:

- removing the local TUI, print mode, server mode, or remote mode
- making `artel acp` the only user-facing command worth documenting
- replacing the internal runtime with raw message passing everywhere
- promising that web or GUI surfaces become free to build
- deprecating JSON-RPC immediately
- expanding the current checkout into a full web-first product strategy

## Current state

Today, Artel exposes ACP over stdio and documents support for:

- session creation, load, list, resume, fork, and cancel
- streamed assistant text and reasoning updates
- tool-call start/completion/failure events
- session-scoped configuration such as mode, model, and thinking
- workspace-aware session scoping
- limited slash-command advertisement

That is already strong enough to support real ACP clients. However, Artel still has multiple run modes and implementation paths whose product semantics may evolve at different speeds.

## Proposal

### 1. Treat ACP as the canonical external runtime contract

Artel should define one shared runtime model internally, then expose that model externally through ACP as the primary control-plane contract.

In practice, this means new multi-surface runtime features should be evaluated with the question:

> Can this behavior be expressed cleanly through the ACP contract?

If not, either:

- the feature is too surface-specific and should stay local to that surface, or
- ACP is missing runtime semantics that should be designed explicitly

### 2. Keep transports separate from protocol semantics

ACP should describe the contract, not mandate one transport.

Preferred transport forms may include:

- ACP over stdio for editor and subprocess integrations
- ACP over local IPC for built-in local clients such as the TUI
- ACP over WebSocket or server-managed channels for remote clients
- ACP over in-process adapters where a direct embedded transport is simpler

The design goal is:

- same semantics
- transport-appropriate bindings
- minimal surface-specific runtime behavior

### 3. Build thin clients over shared semantics

User-facing surfaces should increasingly behave like clients over one runtime model.

Examples:

- the local TUI can remain a first-party built-in Artel mode while consuming ACP-shaped session and event flows over a local transport
- remote clients can map the same turn lifecycle and tool-event semantics onto network transports
- future web or GUI clients can reuse the same capability and session model instead of inventing a parallel API

### 4. Preserve a core runtime beneath ACP

Artel should not collapse its internal architecture into protocol messages only.

A healthier layering is:

1. core runtime model
2. ACP adapter/contract layer
3. transport bindings
4. clients and UIs

This keeps internal code ergonomic while ensuring ACP remains a faithful externalization of runtime behavior.

## Required runtime semantics

ACP is only a good canonical control plane if it captures enough product semantics.

The minimum target should include the following areas.

### Session lifecycle

- create session
- attach/load existing session
- list sessions
- resume session
- fork session
- close or abandon session cleanly
- cancel in-flight work

### Turn lifecycle

- user prompt submission
- assistant turn start
- streaming deltas
- reasoning updates where supported
- final turn completion
- error completion with structured failure details
- retry or replay-friendly behavior where relevant

### Tool lifecycle

- tool discovery or capability advertisement
- tool invocation start
- structured tool arguments
- tool progress or activity updates when available
- tool completion
- tool failure
- approval-gated tool flows

### Session controls

- permission mode changes
- model selection
- reasoning/thinking level
- workspace or project scoping
- interrupt and cancellation

### State and artifacts

- attachments
- generated artifacts
- references to output files and modified files
- resumable state across frontends

### Multi-actor and orchestration behavior

- delegated work units
- progress updates
- completion/failure states
- surfaced background activity

### Capability negotiation and versioning

- what the Artel runtime supports
- what an ACP client can render or act on
- how incompatible feature sets degrade safely

## Design principles

### Canonical semantics over canonical UX

Artel does not need one universal UI. It needs one universal runtime contract.

Different clients may still present:

- different layouts
- different keyboard models
- different rendering strategies
- different affordances for approvals, artifacts, and background work

That is acceptable as long as the underlying semantics stay aligned.

### Surface-specific logic should be narrow

A surface may still need custom code for:

- rendering
- focus and interaction management
- reconnect behavior
- transport bootstrapping
- local device integration

But it should not need to invent a parallel concept of sessions, tools, approvals, or delegation if those are already modeled in ACP.

### Avoid protocol theater

ACP-first should not become a slogan that forces every internal code path through serialization just to claim purity.

The value is:

- one contract
- one state model
- fewer divergent implementations

The value is not:

- turning all internal abstractions into wire messages

## Benefits

If implemented well, this direction should provide:

- less semantic drift across CLI, TUI, remote, and editor surfaces
- easier integration with ACP-capable clients
- stronger testability around one control-plane contract
- lower long-term maintenance cost for new frontends
- clearer product boundaries between runtime semantics and presentation logic

## Risks

### ACP may be too low-level

If ACP exposure stops at token streams and ad hoc events, clients will still need too much product logic.

Mitigation:

- prefer structured domain events over transport-oriented primitives
- define turn, tool, approval, and orchestration lifecycles explicitly

### UI complexity does not disappear

Even with a strong protocol, TUI and GUI clients still need substantial interaction design and rendering work.

Mitigation:

- describe the benefit honestly as semantic reuse, not zero-cost UI development

### Public contract freezes poor abstractions

If Artel exposes unstable or incomplete runtime semantics too early, ACP could lock in weak design decisions.

Mitigation:

- keep a clean internal runtime model
- evolve ACP deliberately with compatibility and versioning in mind

### Product messaging may become confusing

Users may not care that ACP is canonical internally.

Mitigation:

- keep user-facing docs focused on workflows
- present ACP as the primary integration/control-plane contract, not necessarily the only primary human entry point

## Migration strategy

A practical migration path could look like this.

### Phase 1: inventory and gap analysis

- document current session, tool, approval, and orchestration behavior across surfaces
- identify semantics available in core runtime but not exposed over ACP
- identify surface-specific behavior that should remain local

### Phase 2: fill ACP semantic gaps

- add missing runtime concepts to ACP adapters
- standardize structured events for turn lifecycle, tools, approvals, and resumable state
- define capability negotiation for optional features

### Phase 3: align first-party surfaces

- make local and remote clients consume more shared runtime semantics
- reduce surface-specific lifecycle code where possible
- keep transport-specific code isolated

### Phase 4: test against the canonical contract

- add integration tests that validate ACP as the source of truth for supported runtime behavior
- ensure docs reflect actual ACP support rather than aspirational parity
- compare first-party surface behavior against ACP-backed expectations

## Suggested acceptance criteria

This RFC should be considered substantively realized only when:

1. major session lifecycle flows are expressible through ACP
2. tool and approval flows are expressible through ACP
3. resume/continue behavior is consistent across first-party surfaces and ACP clients
4. orchestration/delegation events are represented consistently enough for clients to render them
5. first-party surfaces do not rely on large private runtime APIs that bypass ACP semantics entirely

## Open questions

- Should JSON-RPC remain a parallel integration surface long-term, or gradually narrow to ACP-centric patterns?
- Which parts of the local TUI should remain direct in-process calls for performance or simplicity?
- How should capability negotiation represent optional subsystems such as MCP, schedules, LSP, and delegation?
- Which events should be standardized as stable contract, and which should remain best-effort hints?
- Should remote server control planes expose ACP-native channels directly, or continue to translate through adjacent protocols?

## Recommendation

Adopt ACP-native architecture as a design direction for Artel.

Concretely, that means:

- keep Artel's current supported surfaces
- treat ACP as the primary external control-plane contract
- evolve the runtime so first-party surfaces increasingly share ACP-shaped semantics
- avoid overstating UI simplification while still using ACP to reduce duplicate product logic

This strikes a balance between architectural coherence and practical product scope.
