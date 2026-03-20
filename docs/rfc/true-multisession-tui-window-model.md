# RFC: True multi-session TUI window model for Artel

## Status

Draft

## Summary

This RFC defines what “true multi-session” should mean in one Artel TUI window.

Today, the server/runtime can support multiple sessions concurrently, but the TUI is still effectively a single-active-session interface with some session switching affordances.

This RFC proposes a future model where one TUI window can:

- keep multiple sessions alive simultaneously
- surface them explicitly in the UI
- switch focus among them without losing the state of other sessions
- render activity and status per session
- avoid dangerous, implicit context replacement behavior

## Why this RFC exists

Recent investigation showed:

- the Artel server can run multiple sessions concurrently, even over one websocket client connection
- the current TUI window model is not a true concurrent multisession UX
- previous confusing behavior came from session-context switching in the TUI, not from a proven server-side concurrency limitation

That means Artel now needs an explicit decision:

- either keep a one-window-one-active-session model and make that crystal clear,
- or design a real multi-session window model intentionally.

This RFC covers the second option.

## Non-goals

This RFC does not propose:

- implementing true multisession immediately
- supporting unlimited sessions with no UX constraints
- rendering all active sessions in full detail simultaneously
- introducing a full tabbed desktop-style MDI product inside the TUI
- removing the option to keep a simpler single-session workflow as the default

## Definitions

## Session

A persisted Artel conversation with its own:

- session id
- title
- project/workspace context
- model/thinking state
- runtime activity state
- history and artifacts

## Active session

The session currently receiving primary user attention and prompt input.

## Live session

A session that still exists in the current window model and may be:

- idle
- running
- waiting permission
- disconnected
- backgrounded

A live session is not necessarily the active session.

## True multisession

For Artel TUI, true multisession means all of the following are true:

1. multiple sessions can remain live in one window
2. switching active session does not destroy the others
3. each session has its own visible state
4. background sessions are still represented in the UI
5. session switching is explicit and safe, not an accidental context overwrite

## Current limitations

Today the TUI still behaves like a single-active-session system in important ways:

- one `_session` object for local mode
- one `_remote_session_id` for remote mode
- one main conversation view bound to one active transcript
- one main input surface bound to one active session at a time
- historically, changing session context while a run was active caused confusion

Even though the server can handle concurrent sessions, the window model does not yet expose them honestly.

## Design goals

## Goal 1 — Session truthfulness

The UI should never imply that switching context is harmless if it actually replaces the active runtime context.

## Goal 2 — Explicit session ownership

Each live session should have:

- an identity in the session strip
- a visible state
- stable semantics when backgrounded

## Goal 3 — Focused complexity

Multisession should not turn the TUI into an unreadable wall of parallel transcripts.

## Goal 4 — Compatible with current architecture

The design should build on the existing:

- session strip work
- visible session state model
- server session controller model
- local/remote convergence work

## Proposed product model

## 1. One active transcript, multiple live sessions

The TUI should continue to show one primary transcript at a time.

But the session strip should become a true live-session surface, not just a current-session summary.

That means:

- one active transcript in the main conversation area
- multiple live sessions represented in the strip
- switching changes which transcript is active
- other sessions continue to exist and retain state

This is the most practical terminal UX model.

## 2. Session strip becomes session switcher + status surface

The session strip should evolve to show more than one session.

Minimum target:

- current active session
- other live sessions in the current window
- clear activity markers per session

Potential session chip data:

- title
- short session id
- project hint
- local/remote indicator
- state badge

Potential state badges:

- idle
- thinking
- responding
- tool
- waiting approval
- disconnected
- background

## 3. Background sessions are allowed, but not invisible

If a session continues running while not active, the strip should show that.

Examples:

- a session continues thinking in the background
- a background session needs approval
- a background session disconnects in remote mode

This is the difference between “real multisession” and “just resume later”.

## 4. Prompt input targets only the active session

The composer remains single-target.

At any given time, prompt input is sent only to the currently active session.

This keeps the model understandable.

## 5. Session switching must be explicit and state-aware

Switching to another session should:

- swap the active transcript view
- update the composer target
- preserve other live sessions
- refuse or require explicit confirmation if a pending transient interaction would be lost

## Key design questions

## Question A — Do background sessions keep streaming into hidden transcripts?

Possible approaches:

### Option A1 — background sessions keep accumulating transcript state silently

Pros:

- true concurrency
- simple runtime semantics

Cons:

- switching back may cause a huge visual jump
- user might miss approvals or important updates

### Option A2 — background sessions keep state, but UI surfaces compact summaries only

Pros:

- preserves concurrency
- keeps main transcript focused
- better terminal UX

Cons:

- requires per-session summary/status design

### Recommendation

Use Option A2.

That is:

- background sessions continue to exist
- strip/status surfaces show their state
- transcript detail is viewed when that session becomes active

## Question B — How should approvals work for non-active sessions?

This is one of the hardest UX questions.

Possible approaches:

### Option B1 — only the active session may ask approval

Pros:

- simple

Cons:

- background concurrency becomes fake or brittle

### Option B2 — background session approval is surfaced globally

Pros:

- more truthful multisession model

Cons:

- more complex UX

### Recommendation

Use a global approval queue concept.

That means:

- approvals are not hidden in inactive sessions
- the UI can show which session needs approval
- the user can switch to that session or answer through a global permission surface

## Question C — How do local sessions fit?

Current local mode holds one `_session` object.

True multisession implies a local window model that can hold multiple local session objects simultaneously.

That means local mode needs a session registry inside the TUI, not just a single `_session` reference.

## Question D — How do remote sessions fit?

Remote mode currently orients around one `_remote_session_id` and one websocket connection.

True multisession implies one of two models:

### Option D1 — one websocket per active viewed session

### Option D2 — one websocket connection that multiplexes multiple session streams

The server already supports multiple session controllers keyed by session id.
The missing piece is the client-side session/window model.

## Recommended architecture direction

## Local mode

Introduce a local window session registry, for example conceptually:

- `local_sessions: dict[session_id, LocalWindowSessionState]`
- `active_local_session_id`

Where each local session state holds:

- AgentSession instance
- title
- project/model/thinking metadata
- visible session state
- cached/restored transcript data if needed

## Remote mode

Introduce a remote window session registry, conceptually:

- `remote_sessions: dict[session_id, RemoteWindowSessionState]`
- `active_remote_session_id`

Where each remote session state holds:

- session metadata
- current visible state
- whether it is subscribed/live
- any pending approval marker
- transcript cache/snapshot if needed

## Shared UI layer

The session strip should consume a unified UI model like:

- `WindowSessionSummary`
- `WindowSessionState`

independent of whether the session is local or remote.

## Proposed staged implementation

## Phase 1 — Honest session strip model

Without yet implementing true concurrency, build the session strip so it can represent multiple sessions cleanly.

Tasks:

- define strip data model for multiple session chips
- represent current + recent/known sessions
- show state badges consistently

## Phase 2 — Window session registries

Introduce explicit session registries in the TUI for local and remote paths.

Tasks:

- stop relying on only one active `_session` / `_remote_session_id`
- create session summary/state structures
- ensure switching preserves inactive sessions

## Phase 3 — Background session state support

Allow non-active sessions to remain live and have visible state changes.

Tasks:

- update strip state when background sessions change
- surface background approvals or urgent conditions
- avoid hidden dangerous state

## Phase 4 — Transcript swap model

Switch the main conversation area between live session views without destroying the others.

Tasks:

- transcript restoration/cache model
- active session swap logic
- main composer retargeting

## Phase 5 — Approval and interruption policy

Define how approvals, cancellation, and steering work when multiple sessions are live.

## UX constraints

To keep the system usable, the RFC recommends these constraints even in true multisession mode:

- one active transcript visible at a time
- one active composer target at a time
- global session strip for live session awareness
- global or strip-linked approval visibility
- no silent session replacement

## Suggested first concrete tickets

1. Define `WindowSessionSummary` / `WindowSessionState` structures in the TUI.
2. Upgrade session strip from single-session summary to multi-session data model.
3. Add explicit inactive/background session state handling in the strip.
4. Prototype remote multi-session summaries before attempting transcript multiplexing.
5. Design approval UX for non-active sessions before claiming full multisession support.

## Recommendation

Do not market current one-window behavior as true multisession.

Adopt this RFC as the design target for true multisession in one Artel TUI window.

In the short term:

- keep the current safe single-active-session behavior
- continue improving strip/session UX
- design true multisession deliberately on top of the proven server concurrency model

That path is much safer than pretending the problem is solved while the window model is still fundamentally single-target.
