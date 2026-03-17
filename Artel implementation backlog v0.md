# Artel implementation backlog v0
This document operationalizes `Artel PRD v0` into an execution-ordered backlog. The goal is to turn Artel into Artel with a full product rename, first-run migration, cmux-only bootstrap, built-in official capabilities, and an orchestration-first workspace with dashboard, orchestrator, and employee surfaces.
## Current implementation anchors
Artel-first bootstrap, config resolution, and compatibility handling are now in place. The repository ships with an `artel` primary entrypoint, the `src/artel` meta-package, Artel-first generated config/templates, and Artel-first docs/install surfaces, while temporary compatibility bridges remain through the `artel` CLI alias, legacy env/path fallbacks, and internal `artel_*` implementation imports.
Persisted state is now Artel-first across global/project config, auth, prompts, skills, themes, local server state, extension manifests, provider overlay, and web/admin/runtime copy, with legacy Artel fallback reads intentionally preserved where migration still depends on them. The broad user-facing rename sweep across README/docs, installer/help text, runtime strings, and rename-sensitive tests is complete and validated.
cmux integration now exists as a helper foundation and the shared workspace-summary model has been extracted into core, but the interactive product path is still not cmux-gated and the web layer still retains some duplicate rendering heuristics. Official capabilities also remain externalized through the registry and separate repos, so the backlog has moved past broad rename hygiene and is now primarily blocked on cmux-only bootstrap plus in-tree capability migration.
## Status update — 2026-03-10
Workstream 1 is functionally in place: Artel bootstrap, first-run migration scaffolding, Artel-first path/constants, versioned migration tracking, and compatibility fallback reads have landed with migration-sensitive coverage. Bootstrap now also performs cmux preflight for the default interactive Artel path while continuing to skip that gate for the expected non-interactive/backend commands.
Workstream 2 is partially complete: root metadata, the primary `artel` entrypoint, the `src/artel` package, the alias-package compatibility bridge, and the broad user-facing rename sweep are done. Artel-first entry-point group discovery with Artel fallback is also in place; the physical `artel_*` package/module rename and first-party import flip remain deferred.
Workstream 3 is largely complete: config templates, env resolution, `.artel` project generation, prompts/skills/themes lookup, installer behavior, managed local server tokens, and Artel-first generated artifacts are in place with legacy Artel fallbacks preserved where needed.
Workstream 4 is mostly in place as a runtime substrate: the cmux helper layer now covers detection, structured preflight diagnostics, status/progress/logging, pane/browser actions, workspace/surface list-create-focus-rename helpers, and bootstrap helpers for the default Artel workspace plus dashboard/orchestrator surfaces. Remaining gaps are product-level hardening and breadth: the exact long-term surface model is still evolving, some lifecycle semantics still need tightening, and later workstreams still need to consume the cmux substrate rather than only proving it exists.
Workstream 5 is partially complete: the shared workspace-summary module and tests are in place and the web layer already delegates much of its follow/task/file/diff/terminal/tool summarization to core, but `packages/artel-web/src/artel_web/rendering.py` still contains duplicate formatting and summary-adjacent heuristics that should be collapsed further into core-backed helpers so the web layer becomes a thinner presenter.
Workstream 6 is started but not complete: first-party bundled capability registration plus initial worktree, MCP, and orchestration runtime skeletons are present in-tree with targeted tests, but the implementation is still at substrate/skeleton level rather than a fully integrated built-in capability migration from the former external official packages.
Workstream 7 has foundational progress through the in-tree orchestration runtime and employee record model, but the full employee lifecycle, task execution wiring, cancellation/control flows, and control-plane exposure are still incomplete.
Workstreams 8-9 are still largely ahead of the implementation: there is not yet a true dashboard product surface or a fully adapted orchestrator-first interactive flow, even though the cmux bootstrap now prepares placeholder Artel dashboard/orchestrator surfaces.
Workstream 10 is partially complete: Artel rename hygiene across README/docs, installer/help text, runtime copy, and rename-sensitive tests is mostly done, but the docs still do not reflect the intended cmux-only product flow, bundled official capabilities, or dashboard/orchestrator/employee surface model.
## Validation snapshot — 2026-03-10
Targeted validation against the current backlog state passed: `uv run pytest -q tests/test_migrations.py tests/test_workspace_summary.py tests/test_installation.py tests/test_tui_phase5.py tests/test_extensions_phase3.py` completed successfully with 105 passing tests. Repository-wide lint validation is not yet green: `uv run ruff check .` still reports pre-existing issues outside the rename/migration slices.
The post-Workstreams-1-4 validation gate is substantially satisfied: Artel boots with migrated config/auth/state, performs interactive cmux preflight, and passes targeted rename-sensitive, migration-sensitive, and cmux/bootstrap-sensitive test slices. The later gates are not yet satisfied because the product surfaces are still placeholders, bundled capabilities are not yet fully integrated replacements for the former external official packages, and no finished dashboard/orchestrator/employee workspace exists.
## Execution strategy
Keep the tree runnable after each milestone by preserving the current compatibility bridges until the physical module-tree rename and built-in-capability migration land. The next priority order should be: finish Workstream 5 shared-summary extraction cleanup, harden Workstream 6 bundled capability integration, then build Workstreams 7-9 on top of the now-existing cmux/runtime substrate.
## Immediate next steps — 2026-03-10
1. Remove the remaining duplicate workspace-summary heuristics from `packages/artel-web/src/artel_web/rendering.py` so the web layer becomes a thinner consumer of shared core helpers.
2. Expand `artel_core.workspace_summary` with any remaining pure formatting/selection helpers needed by web surfaces and lock them down with tests before building new UI on top.
3. Harden Workstream 6 by wiring bundled worktree/MCP/orchestration capabilities more deeply into default runtime/admin flows and by adding stronger integration coverage.
4. Build the real dashboard/orchestrator product surfaces on top of the existing cmux workspace bootstrap instead of relying on placeholders alone.
5. Update README/docs/registry positioning after the surface model and bundled-capability behavior land so the public product narrative matches the actual Artel runtime model.
## Workstream 1 — bootstrap and migration foundation
1. Add a new Artel bootstrap module that runs before normal CLI initialization and is responsible for first-run migration decisions, cmux preflight, and compatibility path resolution.
2. Introduce explicit Artel path/constants definitions for global config dir, auth file, session DB, extension manifest, registry cache, provider overlay, prompts, skills, and migration state while preserving temporary read access to the legacy Artel paths.
3. Extend the migration system in `packages/artel-core/src/artel_core/migrations.py` so it can manage real versioned data migrations instead of only config-version tracking.
4. Add idempotent migration helpers for global state rooted in `~/.config/artel`: `config.toml`, `auth.json`, `sessions.db`, `extensions.lock`, `registry_cache`, `server-provider-overlay.json`, `prompts`, `skills`, `SYSTEM.md`, `APPEND_SYSTEM.md`, and `state.json`.
5. Add idempotent migration helpers for project-local state rooted in `.artel`: `config.toml`, `AGENTS.md`, `SYSTEM.md`, `APPEND_SYSTEM.md`, `prompts`, `skills`, `server.json`, and `mcp.json` where present.
6. Define the migration policy for copy-vs-move behavior and record migration provenance so the process can be safely re-run.
7. Add temporary compatibility reads so Artel can still consume legacy Artel state when migration has not yet occurred or only partially completed.
8. Add unit tests for each migrated artifact class and integration tests for first-run migration across both global and project-local state.
## Workstream 2 — repository, package, and entrypoint rename
1. Rename the root project metadata in `pyproject.toml` from Artel to Artel, including the project name, workspace members, uv source aliases, and top-level package metadata.
2. Rename the root meta-package from `src/artel` to `src/artel`.
3. Rename workspace package directories and project metadata from `artel-ai`, `artel-core`, `artel-server`, `artel-tui`, and `artel-web` to Artel equivalents.
4. Introduce temporary `artel_*` alias packages so Artel import paths can exist before the physical module-tree rename.
5. Keep internal first-party implementation imports on `artel_*` until the real module rename lands; switching internal imports early creates duplicate Python module identities under both names and breaks registries, monkeypatches, and persisted/runtime lookups.
6. Rename the physical Python module packages from `artel_ai`, `artel_core`, `artel_server`, `artel_tui`, and `artel_web` to Artel equivalents.
7. Update all first-party import paths, module docstrings, logger names, and package references after the physical module rename.
8. Replace the main CLI entrypoint with `artel` and add only the minimum temporary compatibility alias required to bridge the transition from existing Artel installs.
9. Rename all first-party entry-point group names from `artel.extensions`, `artel.tui`, `artel.server`, `artel.ai`, and `artel.web` to Artel names while preserving temporary compatibility discovery for legacy third-party plugins.
10. Rename top-level project strings in help text, UI copy, installer output, generated config templates, web labels, and tests.
11. Update all tests, fixtures, and import-time monkeypatches that refer to Artel package names or Artel-specific paths.
12. Run a repository-wide sweep for residual Artel-specific package/import references and resolve intentional compatibility leftovers explicitly.
## Workstream 3 — config, environment, installer, and generated artifact rename
1. Rename generated config templates and comments in `packages/artel-core/src/artel_core/config.py (213-577)` from Artel to Artel.
2. Rename config/env variables such as `ARTEL_CONFIG_DIR`, `ARTEL_INSTALL_DIR`, `ARTEL_BIN_DIR`, and Worktree-related Artel env names to Artel equivalents while preserving temporary fallback reads of the old names.
3. Rename project-local directory generation from `.artel` to `.artel` and update generated `AGENTS.md`/context files accordingly.
4. Rename prompt and skill discovery roots from Artel paths to Artel paths while preserving temporary legacy fallback reads.
5. Rename OAuth auth storage defaults from Artel paths to Artel paths and update error/help text that still says `artel login`.
6. Rename provider overlay, managed local server registry, and any other generated runtime artifacts from Artel-specific paths to Artel-specific paths.
7. Update `install.sh` to install Artel into Artel-named directories, create an `artel` launcher, restore migrated extension state correctly, and print Artel-specific guidance.
8. Add installer and config-generation tests that cover new Artel paths plus legacy Artel migration behavior.
## Workstream 4 — cmux-only preflight and runtime substrate
1. Add a cmux detection/preflight layer that validates binary availability, expected capabilities, and socket reachability before starting the Artel interactive product path.
2. Define actionable fail-fast error messages for common preflight failures: binary missing, socket unavailable, capabilities missing, or unsupported runtime environment.
3. Expand the cmux helper layer to include workspace/surface lifecycle APIs needed by Artel v0: create/list/select workspace, create/list/focus/rename surface, and any required workspace metadata operations.
4. Add tests for the new cmux wrapper layer with mocked subprocess responses for success and failure paths.
5. Integrate cmux preflight into the main `artel` interactive bootstrap so Artel exits immediately with instructions instead of falling back to a non-cmux runtime.
6. Decide and implement the exact gating behavior for non-interactive or backend commands so the product remains internally coherent while still supporting required development/test flows.
## Workstream 5 — shared workspace-summary domain model
1. Extract the task/focus/diff/terminal/tool-activity summarization logic from the current web layer into a shared Artel core module.
2. Introduce typed summary models for current task, focused artifact, terminal context, diff snapshot, tool activity, recent updates, and actor status.
3. Move the current heuristics from `packages/artel-web/src/artel_web/rendering.py` into reusable pure functions that do not depend on NiceGUI.
4. Adapt the web layer to consume the shared summary module instead of its private Artel-specific rendering logic.
5. Add unit tests that lock down the extracted workspace-summary behavior before the dashboard is built on top of it.
## Workstream 6 — built-in official capability integration
1. Create in-tree Artel modules for worktree management, orchestration/subagents, and MCP integration.
2. Move the current worktree service behavior from `/Users/m.verhovyh/Projects/artel-ext-worktree/src/artel_ext_worktree/service.py` into first-party Artel code and update naming/path conventions from Artel to Artel.
3. Move the current MCP config/runtime behavior from `/Users/m.verhovyh/Projects/artel-ext-mcp/src/artel_ext_mcp` into first-party Artel code, including Artel path support for `.artel/mcp.json` and global Artel config roots.
4. Move the current subagent registry, runner, server endpoints, and TUI integration from `/Users/m.verhovyh/Projects/artel-ext-subagents/src/artel_ext_subagents` into first-party Artel code.
5. Preserve a clean extension-registration boundary for these built-in capabilities so community plugins can still use the same conceptual API model.
6. Update extension discovery so built-in official capabilities load by default without going through external package installation.
7. Update extension admin flows so built-in capabilities are visible as bundled product features and not treated like removable third-party packages.
8. Keep `example` as an external sample plugin and update docs/tests so it remains a reference implementation rather than a bundled feature.
9. Add unit and integration tests for all three built-in capability migrations.
## Workstream 7 — orchestration runtime and employee lifecycle
1. Introduce first-party orchestration services that model the orchestrator session, employee sessions, task assignment, lifecycle, and status propagation.
2. Define persistent or in-memory employee registry/state objects with enough metadata for dashboard rendering: employee id, display name, assigned task, status, cmux surface, project path, worktree path, branch, and latest updates.
3. Add the employee creation flow: allocate worktree, create cmux surface, create Artel agent session, and register the employee with the orchestrator.
4. Add employee control flows for cancel, stop, remove, and focus surface.
5. Adapt current subagent tool/command semantics into Artel-native orchestration APIs and user-visible commands.
6. Expose orchestration status through the server/control plane where secondary clients need visibility into employee state.
7. Add integration tests for orchestrator-to-employee creation, task execution, cancellation, and status inspection.
## Workstream 8 — dashboard surface
1. Create the dedicated dashboard TUI surface as a product-specific UI instead of a generic chat surface.
2. Implement the dashboard layout with sections for active tasks, queued tasks, employee roster, employee status, workspace evidence, focused file/diff/terminal context, and recent event feed.
3. Bind the dashboard to the shared workspace-summary domain model and employee registry.
4. Add operator actions for refreshing state, focusing an actor surface, and creating or inspecting employees.
5. Ensure dashboard updates are driven by orchestration events rather than only by full manual refreshes where feasible.
6. Add unit tests for dashboard data shaping and integration tests for end-to-end dashboard rendering in a cmux-backed session.
## Workstream 9 — orchestrator surface and interactive product flow
1. Rename and adapt the current main TUI app into the Artel orchestrator surface.
2. Remove or de-emphasize the old Artel generic chat framing so the orchestrator surface behaves as the command console for delegation and coordination.
3. Integrate employee/orchestration commands into the orchestrator UI.
4. Make the orchestrator bootstrap create or attach to the correct Artel cmux workspace and ensure the orchestrator surface title is stable and predictable.
5. Ensure the orchestrator surface reports status/progress back into cmux using the expanded helper layer.
6. Add integration tests for Artel workspace bootstrap with both `dashboard` and `orchestrator` surfaces present.
## Workstream 10 — docs, registry, and secondary surface cleanup
1. Update `README.md`, installer docs, CLI docs, configuration docs, extension docs, and web docs to Artel naming and cmux-only guidance.
2. Update the official registry metadata so bundled product capabilities are no longer advertised as external install targets.
3. Update any remaining web UI branding and shared-control labels from Artel to Artel.
4. Decide which secondary client surfaces remain supported in v0 and align their naming, copy, and control endpoints with the Artel model.
5. Add migration notes for existing Artel users and plugin authors.
## Validation gates
After Workstreams 1-3, Artel should boot with migrated config/auth/state and pass rename-sensitive unit tests.
After Workstreams 4-6, Artel should load only through cmux and have built-in worktree/MCP/orchestration capabilities without relying on external official packages.
After Workstreams 7-9, Artel should create a full workspace with dashboard, orchestrator, and employee surfaces backed by real worktrees and agent sessions.
Before completion, the renamed repository must pass unit tests and integration tests for migration, bootstrap, orchestration, and dashboard behavior, and the user-facing docs/install flow must describe Artel rather than Artel.