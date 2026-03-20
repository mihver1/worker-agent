# RFC: Artel TUI refresh inspired by Toad UX benchmark

## Status

Draft

## Summary

This RFC proposes a product and UX refresh for the Artel TUI, using Toad as a benchmark for interaction quality without adopting Toad directly.

The goal is not to copy another product wholesale.
The goal is to make Artel's TUI feel at least as coherent, navigable, and polished as the best ACP-first terminal UIs while preserving Artel's own scope and capabilities.

## Why this RFC exists

Artel already has substantial capability breadth in the TUI:

- local and remote workflows
- session continue/resume
- rules management
- tasks and notes
- delegation/orchestration surfaces
- schedules and MCP flows
- worktree/git helpers
- image attachment workflows
- server and control-plane related surfaces

But the current TUI is stronger in feature breadth than in interaction coherence.

Compared to Toad as a UX benchmark, Artel currently underperforms in:

- session-centric framing
- prompt/editor polish
- sidebar and panel philosophy
- shell-as-a-surface coherence
- navigation/focus language
- overall visual hierarchy

This RFC turns that observation into a concrete refresh direction.

## Non-goals

This RFC does not propose:

- adopting Toad code directly
- changing Artel's licensing, packaging, or Python baseline to match Toad
- turning Artel into a generic multi-agent marketplace product
- replacing Artel's domain-specific capabilities with a thinner, less capable UI
- promising immediate web parity from TUI improvements alone

## Product position

Artel should remain:

- an Artel-native product
- ACP-native in architecture where appropriate
- richer than a generic ACP host in domain capabilities

But it should become much better at presenting those capabilities through a coherent terminal UX.

## Core diagnosis

## What Toad gets right

As a UX benchmark, Toad is strong because it has:

1. a clear mental model of the interface
2. a first-class prompt/editor surface
3. strong session UX
4. clean sidebar architecture
5. shell as a first-class workflow surface
6. a strong navigation and focus model
7. a product-grade mapping from ACP events to UI primitives

## Where Artel currently falls short

Artel today has too many useful surfaces that feel like adjacent mechanisms instead of one interaction language.

Examples:

- conversation area is strong, but not clearly dominant over secondary surfaces
- server dock, board sidebar, inline panels, permission panel, and command UI do not yet feel like one family
- many workflows are available, but too many remain command-discoverable rather than visually legible
- local and remote mental models still differ more than they should
- session switching and session state are not as first-class as they should be

## Design goals

Artel TUI should become:

### 1. More session-centric

A session should feel like a first-class visible object, not just a hidden conversation state plus optional commands.

### 2. More editor-grade

The composer should feel like a serious working surface, not just a smart chat input.

### 3. More layered

The interface should clearly separate:

- primary work area
- contextual side information
- transient controls and overlays

### 4. More navigable

Focus movement, panel entry/exit, session switching, and run control should follow a simpler, more memorable language.

### 5. More ACP-aligned

As Artel moves toward ACP-native runtime semantics, the TUI should increasingly render a normalized session event model rather than transport- or runtime-specific branches.

## Proposed UI architecture

## Primary layer

The primary layer is the main conversation and input workflow.

It should contain:

- session strip / session tabs
- conversation canvas
- prompt/editor surface
- core run state / mode state summary

This should be the dominant default view.

## Secondary layer

The secondary layer contains information that is useful often, but should not compete with the conversation itself.

Recommended secondary panels:

### Left contextual panel

Potential contents:

- project tree
- session list / session groups
- maybe server/project context in remote mode

### Right work-context panel

Potential contents:

- plan
- tasks
- notes
- maybe delegates summary
- maybe MCP/schedules summary in a later phase

The exact split may evolve, but the key principle is:

- side panels should be structured and intentional
- not a collection of unrelated side mechanisms

## Transient layer

Transient controls should appear as overlays, modals, or inline transient panels.

Recommended transient surfaces:

- permission decisions
- command palette
- mode switcher
- rule editor
- targeted action menus

These should be easy to enter and easy to dismiss back into the primary flow.

## Proposed UX pillars

## Pillar 1 — Session strip / session tabs

Artel should adopt a first-class session strip similar in spirit to Toad's session framing.

Minimum capabilities:

- show current session clearly
- show run state per session
- quick session switching
- visible local and remote session continuity
- support resumed/forked sessions naturally

Optional future additions:

- unread/new activity indicators
- grouped sessions by host/project
- visual distinction for delegated/background sessions

## Pillar 2 — Composer/editor refresh

The composer should be upgraded into a more product-grade editor surface.

Desired qualities:

- multiline ergonomics
- stronger slash-command UX
- clearer mode-sensitive behavior
- better file/path insertion UX
- richer command completion
- better visual structure for prompt, context, and pending attachments

This does not require building a full code editor.
It requires making the prompt area feel deliberate and powerful.

## Pillar 3 — Sidebar philosophy

Artel should adopt one sidebar philosophy instead of multiple unrelated side surfaces.

The refresh should answer explicitly:

- what belongs in persistent side panels?
- what belongs in transient overlays?
- what belongs only in commands?

This should reduce the feeling that features were added one by one without a single layout doctrine.

## Pillar 4 — Run-state visibility

The TUI should make session and run state legible at a glance.

Useful visible states include:

- idle
- thinking
- responding
- waiting permission
- tool running
- shell running
- disconnected / reconnecting in remote scenarios

Some of this exists already in footer/status flows, but it should become more product-visible.

## Pillar 5 — Better command discoverability

Artel already has a lot of commands and capabilities.
The refresh should make them more discoverable via:

- command palette/menu
- ACP available-commands alignment where relevant
- explicit visual command affordances
- better grouping of commands by intent

The goal is not fewer commands.
The goal is fewer hidden capabilities.

## Pillar 6 — Shell decision

Toad demonstrates that shell can be a first-class surface rather than a command hack.

Artel should explicitly decide whether:

- shell remains command-oriented (`!`, `!!`, related helpers), or
- shell becomes a true first-class surface in the TUI

This RFC recommends treating shell as a strategic design question, not an incidental implementation detail.

A real shell surface would be high impact, but also high cost.

## Pillar 7 — ACP-native rendering convergence

Longer term, the TUI should render a normalized Artel session event vocabulary rather than depending on separate local and remote runtime semantics.

This aligns directly with:

- `canonical-session-event-vocabulary.md`
- ACP-first architecture goals

## Design principles

## Principle 1 — Artel-native, not generic-host-native

Toad is a useful benchmark, but Artel should remain optimized for Artel workflows and capabilities.

## Principle 2 — Capability breadth must not be lost

The refresh should simplify interaction without deleting power.

## Principle 3 — UX coherence before feature sprawl

New surfaces should fit a shared layout and focus language.

## Principle 4 — Commands remain useful, but should not be the only discoverability layer

## Principle 5 — Local and remote should feel like the same product

The user should not need to maintain two mental models for local versus remote Artel sessions.

## Proposed workstreams

## Workstream 1 — Session-first framing

- design session strip / tabs
- define visible session states
- unify local/remote session presentation
- improve resume/fork/switch affordances

## Workstream 2 — Composer/editor refresh

- improve multiline prompt ergonomics
- improve slash command completion and grouping
- improve file/path insertion UX
- refine pending attachment visualization
- improve prompt mode visibility

## Workstream 3 — Sidebar redesign

- define left contextual panel contents
- define right work-context panel contents
- move ad hoc side mechanisms into a clearer structure
- reduce overlap between persistent and transient UI

## Workstream 4 — Navigation and focus model

- define global vs local bindings
- define panel entry/exit rules
- define session switching interactions
- define shell/prompt/tool focus transitions
- simplify keyboard story where possible

## Workstream 5 — Run-state and status visibility

- improve visible activity model
- make permission wait states more explicit
- improve tool-running and shell-running visibility
- align session strip, footer, and conversation indicators

## Workstream 6 — Command discoverability

- improve command palette
- group commands by workflow intent
- align with ACP available commands where relevant
- make high-value actions visually discoverable

## Workstream 7 — Shell strategy

- evaluate whether Artel should build a first-class shell surface
- if yes, define scope and UX model
- if no, improve current shell affordances without pretending they are more than they are

## Workstream 8 — ACP-native UI convergence

- consume canonical session event vocabulary in more places
- reduce local-vs-remote rendering divergence
- prepare for ACP-backed local TUI experiments

## Suggested implementation order

### Phase A — visible UX coherence wins

1. session strip / session framing
2. composer/editor improvements
3. sidebar simplification
4. better command discoverability

### Phase B — interaction architecture

5. focus/navigation model cleanup
6. run-state visibility improvements
7. local/remote presentation convergence

### Phase C — strategic enhancements

8. shell surface decision and implementation if chosen
9. deeper ACP-native rendering convergence

## Suggested first concrete tickets

1. Design and prototype a session strip for Artel TUI
2. Define the persistent side-panel model and retire or re-home overlapping side surfaces
3. Improve command palette / command menu organization
4. Refresh composer/editor behavior around multiline, commands, and file insertion
5. Add a visible run-state model shared across footer and session framing

## Success criteria

This RFC should be considered successful if Artel TUI reaches these qualities:

- sessions are first-class and easy to navigate
- the prompt area feels like a powerful working surface
- the layout has a clear primary/secondary/transient hierarchy
- local and remote workflows feel like one product
- Artel remains capability-rich while becoming easier to understand and operate

## Failure modes

This effort would fail if:

- Artel accumulates more UI features without stronger structure
- session UX remains command-hidden rather than visually legible
- sidebars and panels continue to multiply without a layout doctrine
- shell remains strategically undefined but still leaks into the UX everywhere
- local and remote continue to drift in how they feel

## Recommendation

Adopt this RFC as the design direction for Artel TUI evolution.

Use Toad as a benchmark for UX quality, not as a product template.
The target is not imitation.
The target is an Artel-native TUI that is at least as coherent, navigable, and satisfying to use as the best ACP-first terminal interfaces.
