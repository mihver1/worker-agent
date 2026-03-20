# RFC: Artel TUI refresh backlog

## Status

Draft

## Purpose

Turn the Artel TUI refresh direction into a practical implementation backlog.

This backlog is derived from:

- `artel-tui-refresh-inspired-by-toad.md`
- the ACP-first RFCs
- the current Artel TUI structure and product scope

The goal is to improve interaction quality without a risky big-bang rewrite.

## Principles

1. Keep Artel capability breadth intact while improving coherence.
2. Prefer small vertical wins over speculative large rewrites.
3. Separate layout cleanup from deeper runtime convergence work.
4. Keep local and remote TUI flows visually and behaviorally aligned when possible.
5. Use ACP-native convergence as a long-term architectural force, not a precondition for all UX work.

## Success definition

The TUI refresh is succeeding when:

- sessions become visually first-class
- the prompt area feels like a deliberate working surface
- side panels follow one clear layout philosophy
- important actions are more discoverable without requiring memorized commands
- the interface feels more unified across local and remote usage

## Workstream 1 — Session strip and session framing

### Goal

Make sessions visible, legible, and easy to navigate from the primary UI.

### Why this is first

This is one of the highest-value UX improvements and does not require rewriting the whole app.

### Tasks

- Design a session strip or session tab model for the top of the main workspace.
- Show at minimum:
  - current session identity
  - title
  - current activity state
  - whether the session is local or remote
- Define state vocabulary for visible session labels:
  - idle
  - thinking
  - waiting approval
  - tool running
  - disconnected/reconnecting if needed
- Implement fast switching between visible sessions.
- Ensure resumed/forked sessions appear naturally in the session UI.
- Integrate session strip with existing local and remote session bookkeeping.

### Exit criteria

- Sessions are first-class visible UI elements.
- A user can switch active sessions without relying solely on slash commands.

## Workstream 2 — Composer/editor refresh

### Goal

Upgrade the input surface from a smart chat box into a stronger work editor.

### Tasks

- Refine multiline editing ergonomics.
- Improve command completion and slash command grouping.
- Improve file/path insertion UX.
- Improve visual handling of pending attachments.
- Make prompt mode/state more explicit.
- Reduce friction when moving between normal prompts, slash commands, shell-like actions, and attachment-bearing prompts.

### Exit criteria

- The composer feels more editor-grade.
- High-value input workflows are faster and more legible.

## Workstream 3 — Sidebar redesign and panel architecture

### Goal

Replace the current accumulation of side mechanisms with a more intentional secondary layout model.

### Tasks

- Define which information belongs in persistent side panels.
- Define which surfaces should remain transient overlays or inline panels.
- Propose a stable left/right panel structure, for example:
  - left: sessions / project tree / contextual navigation
  - right: plan / tasks / notes / delegates summary
- Review current surfaces and decide their destination:
  - server dock
  - board sidebar
  - permission panel
  - inline input panels
  - action panels
- Eliminate overlap between persistent and transient UI responsibilities.

### Exit criteria

- Side panels follow one explicit philosophy.
- Secondary information no longer feels like multiple unrelated systems.

## Workstream 4 — Navigation and focus model cleanup

### Goal

Make keyboard and focus behavior more predictable and easier to learn.

### Tasks

- Define global navigation rules.
- Define how users enter and exit side panels.
- Define how users switch focus among:
  - prompt
  - conversation
  - sidebars
  - shell/terminal surfaces
  - transient controls
- Group keybindings by navigation intent.
- Reduce surprising focus jumps or hidden panel state.
- Update help text and footer hints to reflect the new model.

### Exit criteria

- Focus movement follows a clear, teachable language.
- Common navigation flows no longer feel ad hoc.

## Workstream 5 — Run-state visibility model

### Goal

Make it obvious what the active session is doing at any given time.

### Tasks

- Define shared visible run states.
- Improve status presentation across:
  - session strip
  - footer
  - conversation area
  - permission surfaces
- Distinguish between:
  - thinking/responding
  - tool activity
  - shell activity
  - waiting approval
  - idle
- Improve remote disconnected or stale-session signaling when relevant.

### Exit criteria

- A user can glance at the UI and understand session state without reading detailed logs.

## Workstream 6 — Command discoverability and command palette refresh

### Goal

Make powerful TUI workflows easier to discover and use.

### Tasks

- Improve command palette organization.
- Group commands by workflow instead of only flat textual matching.
- Surface more high-value commands visually.
- Align command presentation with ACP available commands where relevant.
- Distinguish:
  - session commands
  - project commands
  - server/remote commands
  - UI/view commands
- Improve slash command suggestions with better descriptions and contextual ranking.

### Exit criteria

- More capability is discoverable without prior memorization.
- Command UX feels like a product feature, not a hidden power-user trapdoor.

## Workstream 7 — Prompt/file/shell workflow integration

### Goal

Make the “talk to the agent / reference files / run shell things” loop feel more unified.

### Tasks

- Improve file insertion and reference flows.
- Decide whether to support a richer fuzzy file picker or tree-assisted insertion path.
- Clarify shell workflow strategy:
  - incremental improvement of current `!` / `!!` flows, or
  - first-class shell surface later
- Ensure shell-related feedback does not feel disconnected from the rest of the conversation UX.
- Improve transitions between prompt-focused and shell-focused actions.

### Exit criteria

- File and shell workflows feel intentionally integrated rather than bolted on.

## Workstream 8 — Permission and transient action UX refresh

### Goal

Make approvals and transient actions less disruptive and more understandable.

### Tasks

- Improve permission panel presentation and context.
- Make allow-once / allow-all / deny decisions easier to parse visually.
- Improve transient action menus for server/session/project items.
- Make transient surfaces easier to dismiss back into the main flow.
- Ensure transient overlays do not compete with sidebars for the same conceptual job.

### Exit criteria

- Permission and action flows feel deliberate and calm rather than interruptive and mechanical.

## Workstream 9 — Local/remote visual convergence

### Goal

Make local and remote Artel feel like the same product.

### Tasks

- Compare local and remote conversation rendering paths.
- Normalize status wording and session-state presentation.
- Normalize how session switching and metadata appear.
- Reduce cases where one mode relies on commands and the other on dedicated UI.
- Use the canonical session event vocabulary to guide UI-level normalization.

### Exit criteria

- Users no longer feel like they are using two different TUIs for local versus remote work.

## Workstream 10 — ACP-native rendering convergence

### Goal

Move the TUI closer to a normalized runtime semantics layer that can support ACP-native architecture over time.

### Tasks

- Identify rendering code that depends directly on local-only runtime details.
- Identify rendering code that depends directly on bespoke remote protocol details.
- Introduce an intermediate semantic event mapping layer where practical.
- Align UI rendering with the canonical session event vocabulary.
- Prepare the ground for local ACP-backed TUI experiments later.

### Exit criteria

- TUI rendering depends more on shared session semantics and less on transport-specific branches.

## Suggested execution order

### Phase A — visible UX wins

1. Workstream 1 — Session strip and framing
2. Workstream 2 — Composer/editor refresh
3. Workstream 3 — Sidebar redesign
4. Workstream 6 — Command discoverability refresh

### Phase B — interaction coherence

5. Workstream 4 — Navigation/focus cleanup
6. Workstream 5 — Run-state visibility
7. Workstream 8 — Permission/transient action UX
8. Workstream 7 — Prompt/file/shell integration improvements

### Phase C — deeper convergence

9. Workstream 9 — Local/remote visual convergence
10. Workstream 10 — ACP-native rendering convergence

## Recommended first concrete tickets

### Ticket 1 — Session strip prototype

- add a top-level session strip widget
- show current session title and state
- support switching between known sessions

### Ticket 2 — Session state model for UI

- define a small visible session-state enum for TUI presentation
- connect existing run activity updates to that model

### Ticket 3 — Command palette cleanup

- group command suggestions by intent
- improve descriptions and ordering

### Ticket 4 — Sidebar destination map

- produce a concrete mapping of current side/transient surfaces into the new layout model

### Ticket 5 — Composer refresh pass 1

- improve multiline handling
- improve pending attachment presentation
- improve file/path command discoverability

## Dependencies and cautions

### Dependencies

- Workstream 1 benefits from Workstream 5 state vocabulary but can start earlier with a minimal state model.
- Workstream 3 should inform Workstream 4 so focus rules match the new panel layout.
- Workstream 10 should follow after enough visible UX stabilization, not before.

### Cautions

- Do not try to solve shell strategy and full ACP-native rendering before obvious layout problems are improved.
- Do not overfit to Toad's generic agent-host model; keep Artel-native workflows central.
- Avoid replacing command-based power features until visually discoverable equivalents exist.

## Measurement ideas

To know whether the refresh is helping, use small qualitative checks:

- Can a new user understand where sessions live?
- Can a user find commands without knowing exact slash syntax?
- Can a user tell whether the agent is thinking, waiting, or idle at a glance?
- Can a user move between prompt, conversation, and side panels without confusion?
- Does local vs remote feel like the same product?

## Recommendation

Adopt this backlog as the implementation plan for the Artel TUI refresh.

Start with the session strip and visible session-state work first. That creates the strongest immediate UX shift while staying compatible with the current architecture and preserving room for later ACP-native convergence.
