# Product report: Artel vs OpenCode vs Aider vs Goose vs Gemini CLI

## Scope and method

This report uses only artifacts I could inspect directly from source, docs, tests, and CLI/help surfaces.

### Repositories and revisions inspected

- **Artel (current project)** — commit `b9a19143a258b48a960db9256e4a52e145cf196e`
- **OpenCode** — `https://github.com/anomalyco/opencode` at commit `689d9e14eade9001568c46c602092eb01fe7e746`
- **Aider** — `https://github.com/Aider-AI/aider` at commit `861a1e4d154f268547a06497cc380e5a5dc8483a`
- **Goose** — `https://github.com/block/goose` at commit `831cb9bb82de6dec9ec1561c1e260309de11b1e6`
- **Gemini CLI** — `https://github.com/google-gemini/gemini-cli` at commit `9f7691fd882fdfb94259a83b6d4499e9b612cf81`

## Important framing updates

Per your instruction:

- **Claude Code is removed from this report**.
- I also **do not use the old Artel employee model as a core comparison axis**.
- I also **do not use Artel web as a strategic axis**, except where it matters as a currently exposed/claimed product surface.

So the comparison is centered on:

- inspectable implementation depth
- terminal/CLI workflows
- server/remote/control-plane support
- MCP
- extension/plugin systems
- agent/subagent/orchestration capabilities
- provider breadth
- scheduling/automation
- tests and source transparency

---

## Executive summary

### Short version

- **OpenCode** is the strongest overall product in this comparison by **breadth of implemented surfaces** visible in source: TUI, web, desktop, headless server, control plane, MCP with OAuth, built-in agents/subagents, LSP, worktrees, import/export, GitHub/PR flows, and extensive tests.
- **Gemini CLI** is extremely strong in **agent system depth, hooks, MCP, extensions, non-interactive automation, checkpointing, and test/eval coverage**. It looks like one of the most sophisticated open-source agent CLIs in terms of modern agent architecture.
- **Goose** is strong in **local agent + server + ACP + MCP + schedules + recipes + extension architecture**, with a substantial Rust implementation and broad test surface.
- **Aider** remains very strong as a **focused terminal pair-programming tool** with git-centric workflows, repo mapping, lint/test loops, web/image inputs, browser UI, file watching, and mature Python ergonomics — but it is narrower as a platform than OpenCode, Goose, or Gemini CLI.
- **Artel** is a credible Python agent platform with strong inspectability, rules, server/control-plane APIs, provider breadth, MCP basics, worktree support, delegation tools, and schedules — but it is still behind the strongest open-source peers in product completeness, especially around MCP depth, agent architecture, code-intelligence features, and finished multi-surface UX.

### Position of Artel among these projects

Today, based on inspectable source:

- **Artel is ahead of Aider** in server/control-plane ambition and explicit policy/rules surfaces.
- **Artel is behind Goose, Gemini CLI, and OpenCode** in total product maturity and implemented feature breadth.
- **OpenCode and Gemini CLI** are the closest references if the goal is a broad, modern, extensible coding-agent product.
- **Aider** is the strongest reference if the goal is a pragmatic terminal-first coding workflow with tight git/edit ergonomics and lower system complexity.

---

## Current project snapshot: Artel

## Verified strengths

### 1. Inspectable Python architecture with multiple runtime modes

Artel is a real multi-package Python workspace:

- root workspace: `pyproject.toml:1-62`
- core: `packages/artel-core/pyproject.toml:1-26`
- server: `packages/artel-server/pyproject.toml:1-22`

Confirmed runtime modes in CLI/docs:

- one-shot prompt mode: `packages/artel-core/src/artel_core/cli.py:29-77`
- local TUI path: `packages/artel-core/src/artel_core/cli.py:78-90`
- headless server: `packages/artel-core/src/artel_core/cli.py:104-120`
- remote connect: `packages/artel-core/src/artel_core/cli.py:132-147`
- web command exposed: `packages/artel-core/src/artel_core/cli.py:150-189`
- MCP commands: `packages/artel-core/src/artel_core/cli.py:192-327`
- schedule commands: `packages/artel-core/src/artel_core/cli.py:330-536`
- extension commands: `packages/artel-core/src/artel_core/cli.py:539-672`
- config/rpc/acp/login/rules commands: `packages/artel-core/src/artel_core/cli.py:675-720` and below
- command list confirmed at runtime: `['acp', 'config', 'connect', 'ext', 'init', 'login', 'mcp', 'rpc', 'rule', 'rules', 'schedule', 'serve', 'server-tray', 'web']`

### 2. Strong explicit server/control-plane surface

Artel server exposes a large HTTP/WebSocket API surface for config, providers, prompts, skills, rules, extensions, MCP, sessions, delegates, tasks, notes, bash, worktree, and more:

- route registration: `packages/artel-server/src/artel_server/server.py:2604-2667`
- server startup: `packages/artel-server/src/artel_server/server.py:2677-2755`

This remains a genuine differentiator versus simpler terminal-only tools.

### 3. Rules/policy system is first-class and well surfaced

Evidence:

- README rules section: `README.md:20-29`, `README.md:102-147`
- CLI rule commands: `packages/artel-core/src/artel_core/cli.py:555-672`
- tests: `tests/test_rules_cli.py:6-66`

### 4. Provider breadth is strong and explicit in code

Provider specs include hosted APIs, OpenAI-compatible backends, cloud providers, and local runtimes:

- provider specs: `packages/artel-ai/src/artel_ai/provider_specs.py:23-257`
- provider implementations: `packages/artel-ai/src/artel_ai/providers/*.py`
- README provider summary: `README.md:149-164`

### 5. MCP config/runtime exists and is tested

Evidence:

- registry and merge logic: `packages/artel-core/src/artel_core/mcp.py:17-280`
- CLI: `packages/artel-core/src/artel_core/cli.py:192-327`
- tests: `tests/test_builtin_capabilities.py:8-76`, `tests/test_mcp_cli_and_runtime.py:35-140`

### 6. Worktree support exists and is tested

Evidence:

- worktree manager: `packages/artel-core/src/artel_core/worktree.py:1-320`
- built-in tool: `packages/artel-core/src/artel_core/tools/builtins.py:521-580`
- tests: `tests/test_worktree.py:1-208`, `tests/test_worktree_integration.py:20-70`

### 7. Delegation/subagent runtime exists

Evidence:

- delegation service: `packages/artel-core/src/artel_core/delegation/service.py:1-239`
- orchestration module is a public alias layer over delegation: `packages/artel-core/src/artel_core/orchestration.py:1-25`
- tools registered by default: `packages/artel-core/src/artel_core/tools/builtins.py:583-616`
- server endpoints: `packages/artel-server/src/artel_server/server.py:2041-2053`, `packages/artel-server/src/artel_server/server.py:2643-2645`
- tests: `tests/test_delegation_tools.py:28-70`, `tests/test_delegation_server_api.py:12-55`, `tests/test_orchestration_surface.py:4-25`

### 8. Scheduling/automation is implemented and tested

This is now part of the actual product surface and should be counted.

Evidence:

- README scheduled tasks section: `README.md:23`, `README.md:63-64`, `README.md:203-245`
- CLI schedule commands: `packages/artel-core/src/artel_core/cli.py:330-536`
- server scheduler service: `packages/artel-server/src/artel_server/server.py:262-347`
- tests: `tests/test_schedule_cli.py:8-80`, `tests/test_schedule_service.py`, `tests/test_schedule_server_api.py`, `tests/test_schedule_storage.py`

### 9. Web search/fetch tools have explicit safety guardrails

Evidence:

- search tool: `packages/artel-core/src/artel_core/tools/web_search.py:1-240`
- fetch tool: `packages/artel-core/src/artel_core/tools/web_fetch.py:1-245`
- built-in registration: `packages/artel-core/src/artel_core/tools/builtins.py:598-616`

## Verified weaknesses

### 1. Built-in capability integration is still shallow

Artel’s bundled capability registry currently returns only one built-in capability:

- `artel-mcp`: `packages/artel-core/src/artel_core/builtin_capabilities.py:25-34`
- tests assert only that: `tests/test_builtin_capabilities.py:8-20`

So although worktree/delegation are in-tree, the bundled capability story is still immature compared with stronger peers.

### 2. MCP depth is still basic relative to leading peers

Artel has config + runtime + CLI status/reload/set/remove, but I did not find:

- MCP OAuth flow comparable to OpenCode or Gemini CLI
- richer auth lifecycle for remote MCP servers
- as broad a management surface as stronger peers

### 3. Orchestration is still thin relative to advanced agent systems

The current public orchestration layer is effectively a naming wrapper over delegation:

- `packages/artel-core/src/artel_core/orchestration.py:1-25`

That is fine as a substrate, but it is much thinner than Gemini CLI’s agent registry/subagent system or Goose’s richer agent/session/recipe stack.

### 4. No visible LSP/code-intelligence subsystem

I did not find an Artel subsystem comparable to OpenCode’s `src/lsp`.

That leaves Artel behind OpenCode in IDE-like code intelligence support.

### 5. Web command is exposed, but current checkout still has placeholder web entrypoint

Even though web is no longer a main comparison axis here, this is still an important factual note because it affects current product reality.

- README still says experimental web UI exists: `README.md:29`, `README.md:69-70`, `README.md:86`
- actual entrypoint raises: `packages/artel-web/src/artel_web/app.py:1-17`

### 6. cmux gating is not part of current runtime behavior

- `command_requires_cmux()` always returns `False`: `packages/artel-core/src/artel_core/artel_bootstrap.py:25-32`
- README explicitly documents local TUI with no cmux required: `README.md:51-52`, `README.md:80-86`

This matters mainly because some older backlog framing is no longer the product reality.

### 7. Test breadth is respectable but smaller than strongest peers

- Artel visible test file count: **83** `test_*.py` files under `tests/`

That is solid, but lower than the broadest peers in this comparison.

---

## OpenCode

## Strong points

### 1. Broadest product surface in the comparison

OpenCode exposes, in visible code/help:

- terminal UI/default mode
- ACP
- MCP management
- attach to running server
- one-shot run mode
- debug tools
- auth/account/provider management
- agent management
- upgrade/uninstall
- headless server
- web
- model listing
- stats
- export/import
- GitHub integration
- PR checkout flow
- session management
- DB tools

Evidence:

- CLI help observed directly
- command registration: `/tmp/artel_product_compare/opencode/packages/opencode/src/index.ts:55-157`

### 2. Multi-surface architecture: server, web, desktop, control plane

Evidence:

- package tree includes app/web/desktop/desktop-electron/enterprise/console
- server app: `/tmp/artel_product_compare/opencode/packages/opencode/src/server/server.ts:52-260`
- workspace control-plane server: `/tmp/artel_product_compare/opencode/packages/opencode/src/control-plane/workspace-server/server.ts:1-64`
- desktop in README: `/tmp/artel_product_compare/opencode/README.md:67-83`

### 3. Built-in agents/subagents are strong and explicit

Built-in agents include:

- `build`
- `plan`
- `general`
- `explore`
- hidden compaction/title/summary agents

Evidence:

- README agents section: `/tmp/artel_product_compare/opencode/README.md:100-113`
- agent definitions: `/tmp/artel_product_compare/opencode/packages/opencode/src/agent/agent.ts:24-257`
- CLI agent management: `/tmp/artel_product_compare/opencode/packages/opencode/src/cli/cmd/agent.ts:31-257`

### 4. MCP is very deep

Confirmed capabilities:

- local MCP and remote MCP
- status tracking
- OAuth for remote MCP servers
- token storage and auth lifecycle
- CLI auth/list/logout/debug flows
- UI notifications around auth problems

Evidence:

- MCP runtime: `/tmp/artel_product_compare/opencode/packages/opencode/src/mcp/index.ts:328-537`
- MCP auth CLI: `/tmp/artel_product_compare/opencode/packages/opencode/src/cli/cmd/mcp.ts:53-319`
- tests: `/tmp/artel_product_compare/opencode/packages/opencode/test/mcp/oauth-auto-connect.test.ts:1-199`

### 5. LSP support is first-class

Evidence:

- README FAQ explicitly claims out-of-the-box LSP support: `/tmp/artel_product_compare/opencode/README.md:133-137`
- LSP runtime: `/tmp/artel_product_compare/opencode/packages/opencode/src/lsp/index.ts:14-260`
- tests: `/tmp/artel_product_compare/opencode/packages/opencode/test/lsp/client.test.ts:1-95`

### 6. Worktree lifecycle is richer than Artel’s

Evidence:

- worktree create/bootstrap/remove/reset logic: `/tmp/artel_product_compare/opencode/packages/opencode/src/worktree/index.ts:338-670`

### 7. Plugin SDK/runtime is powerful and typed

Evidence:

- plugin SDK: `/tmp/artel_product_compare/opencode/packages/plugin/src/index.ts:1-234`
- runtime loader: `/tmp/artel_product_compare/opencode/packages/opencode/src/plugin/index.ts:16-149`
- tests: `/tmp/artel_product_compare/opencode/packages/opencode/test/plugin/auth-override.test.ts:1-43`

### 8. High visible test breadth

- visible test files: **105** `*.test.ts` files under `packages/opencode/test/`

## Weak points

### 1. Highest system complexity in the comparison

OpenCode is a very large Bun monorepo with many packages and a broad dependency graph.

Evidence:

- root workspace: `/tmp/artel_product_compare/opencode/package.json:1-115`
- main runtime package deps: `/tmp/artel_product_compare/opencode/packages/opencode/package.json:1-145`

This breadth is powerful, but it increases maintenance and onboarding cost.

### 2. Bun-first environment is a real adoption constraint for some teams

Evidence:

- package manager: `bun@1.3.10` in `/tmp/artel_product_compare/opencode/package.json:7`

### 3. Desktop still labeled beta

Evidence:

- `/tmp/artel_product_compare/opencode/README.md:67-83`

---

## Aider

## Strong points

### 1. Extremely strong terminal-first coding workflow

Aider is clearly optimized for direct coding work in an existing repo.

Evidence from README:

- AI pair programming in terminal: `/tmp/artel_product_compare/aider/README.md:5-12`
- codebase mapping: `/tmp/artel_product_compare/aider/README.md:49-53`
- git integration: `/tmp/artel_product_compare/aider/README.md:63-67`
- IDE/editor workflow: `/tmp/artel_product_compare/aider/README.md:70-74`
- images and web pages: `/tmp/artel_product_compare/aider/README.md:77-81`
- voice-to-code: `/tmp/artel_product_compare/aider/README.md:84-88`
- linting & testing loop: `/tmp/artel_product_compare/aider/README.md:91-95`

### 2. Practical git-centric implementation

Evidence:

- git setup/init and `.gitignore` management: `/tmp/artel_product_compare/aider/aider/main.py:60-206`
- tests for git setup and repo handling: `/tmp/artel_product_compare/aider/tests/basic/test_main.py:117-153`

### 3. File watcher / comment-driven workflow support

Evidence:

- file watcher implementation: `/tmp/artel_product_compare/aider/aider/watch.py:73-190`
- watcher integrated into runtime via `coder.io.file_watcher`: `/tmp/artel_product_compare/aider/aider/watch.py:88`

### 4. Browser/web UI exists, even if experimental

Evidence:

- Streamlit launch path: `/tmp/artel_product_compare/aider/aider/main.py:233-260`
- GUI code with file/web/git/shell controls: `/tmp/artel_product_compare/aider/aider/gui.py:150-290`

### 5. Rich command surface for coding tasks

Evidence:

- command implementation includes model switching, chat modes, web scraping, shell execution, git-oriented workflows, etc.: `/tmp/artel_product_compare/aider/aider/commands.py:36-320`
- `cmd_web` specifically: `/tmp/artel_product_compare/aider/aider/commands.py:219-253`

### 6. Focused Python implementation with moderate complexity

Evidence:

- Python package with CLI entrypoint: `/tmp/artel_product_compare/aider/pyproject.toml:1-53`

### 7. Visible test coverage

- visible test files: **36** `test_*.py` files under `tests/`
- representative CLI/main tests: `/tmp/artel_product_compare/aider/tests/basic/test_main.py:1-220`

## Weak points

### 1. No visible MCP support in inspected source

A grep for MCP/Model Context Protocol in the inspected Python source did not surface an MCP subsystem comparable to Artel/OpenCode/Goose/Gemini CLI.

### 2. No visible server/control-plane architecture comparable to platform-oriented peers

Aider is much more of a direct operator-facing coding tool than a remote multi-client agent platform.

That is a strength for simplicity, but a limitation if the product goal includes remote clients, APIs, or server-managed sessions.

### 3. Narrower platform scope

Compared with OpenCode, Goose, and Gemini CLI, Aider appears more focused on:

- chat/edit/git workflow
- shell/lint/test loop
- repo context handling

and less focused on:

- MCP ecosystems
n- agent registries/subagents as first-class extensibility substrate
- remote server/control plane
- desktop + control-plane product breadth

---

## Goose

## Strong points

### 1. Strong local-agent plus platform architecture

Goose explicitly positions itself as:

- local, extensible, open-source AI agent
- automates engineering tasks
- supports multi-model configuration
- integrates with MCP servers
- available as desktop app and CLI

Evidence:

- README: `/tmp/artel_product_compare/goose/README.md:3-22`

### 2. Real Rust multi-crate architecture with server and ACP

Visible crates:

- `goose`
- `goose-cli`
- `goose-server`
- `goose-acp`
- `goose-mcp`
- test support crates

Evidence:

- workspace: `/tmp/artel_product_compare/goose/Cargo.toml:1-90`
- crate tree inspected under `/tmp/artel_product_compare/goose/crates/`

### 3. CLI is broad and productized

Visible CLI features include:

- configure/info
- bundled MCP servers
- ACP agent server on stdio
- interactive sessions
- run mode
- recipes
- schedules
- gateways
- project/session management

Evidence:

- CLI source: `/tmp/artel_product_compare/goose/crates/goose-cli/src/cli.rs:35-258`, plus command definitions surfaced by grep throughout file

### 4. Scheduling exists as a real subsystem

Evidence:

- schedule commands in CLI: `/tmp/artel_product_compare/goose/crates/goose-cli/src/cli.rs` grep output around schedule subcommands
- schedule execution engine: `/tmp/artel_product_compare/goose/crates/goose/src/scheduler.rs:780-920`

### 5. MCP/builtin extension story is strong

Evidence:

- built-in extension registration: `/tmp/artel_product_compare/goose/crates/goose/src/builtin_extension.rs`
- goose-mcp builtin servers: `/tmp/artel_product_compare/goose/crates/goose-mcp/src/lib.rs:12-61`
- server main can run bundled MCP servers: `/tmp/artel_product_compare/goose/crates/goose-server/src/main.rs:27-72`
- MCP replay/integration tests: `/tmp/artel_product_compare/goose/crates/goose/tests/mcp_integration_test.rs:136-220`
- integration shell script for MCP workflows: `/tmp/artel_product_compare/goose/scripts/test_mcp.sh:1-134`

### 6. ACP support is explicit and tested

Evidence:

- dedicated crate: `crates/goose-acp`
- server tests: `/tmp/artel_product_compare/goose/crates/goose-acp/tests/server_test.rs:1-99`

### 7. Session persistence and extension state look substantial

Evidence:

- session manager with DB-backed fields and extension data: grep hits throughout `/tmp/artel_product_compare/goose/crates/goose/src/session/session_manager.rs`
- extension state model: grep hits in `/tmp/artel_product_compare/goose/crates/goose/src/session/extension_data.rs`

### 8. Large implementation surface

- visible Rust source file count is large (hundreds of `.rs` files)
- visible tests exist across ACP, core goose, scheduler, MCP, providers, etc.

## Weak points

### 1. Higher complexity than Artel or Aider

Goose is a substantial Rust platform with CLI, server, ACP, MCP, scheduler, recipes, gateway integrations, and desktop positioning. This is a maintenance and onboarding burden as well as a strength.

### 2. More recipe/extension/platform oriented than minimally direct

Compared with Aider, Goose appears more infrastructure-heavy and less minimal. That is not bad, but it changes the ergonomics target.

### 3. Harder to audit quickly than smaller Python projects

The project has a very large Rust surface area. Although inspectable, it is harder to reason about quickly than Artel or Aider.

---

## Gemini CLI

## Strong points

### 1. One of the deepest agent architectures in the comparison

Gemini CLI explicitly presents itself as an open-source terminal AI agent with:

- built-in tools
- MCP support
- non-interactive mode
- checkpointing
- custom context files
- GitHub integration

Evidence:

- README feature section: `/tmp/artel_product_compare/gemini-cli/README.md:17-29`
- advanced capabilities: `/tmp/artel_product_compare/gemini-cli/README.md:124-145`
- non-interactive examples: `/tmp/artel_product_compare/gemini-cli/README.md:236-256`

### 2. Very broad and modern implementation surface

Visible workspace packages include:

- `packages/cli`
- `packages/core`
- `packages/sdk`
- `packages/a2a-server`
- VS Code companion
- devtools

Evidence:

- workspace config: `/tmp/artel_product_compare/gemini-cli/package.json:1-165`
- package tree under `/tmp/artel_product_compare/gemini-cli/packages/`

### 3. Strong subagent/agent system

Evidence:

- generalist agent: `/tmp/artel_product_compare/gemini-cli/packages/core/src/agents/generalist-agent.ts:16-68`
- subagent tool: `/tmp/artel_product_compare/gemini-cli/packages/core/src/agents/subagent-tool.ts:31-233`
- many agent-related modules under `packages/core/src/agents/`
- evals include subagents/generalist delegation: `evals/subagents.eval.ts`, `evals/generalist_delegation.eval.ts`

### 4. Hook system is sophisticated and first-class

Evidence:

- full hook system: `/tmp/artel_product_compare/gemini-cli/packages/core/src/hooks/hookSystem.ts:38-220`
- hook integration tests: `/tmp/artel_product_compare/gemini-cli/integration-tests/hooks-system.test.ts:12-220`

### 5. MCP support is first-class

Evidence:

- README calls out MCP support: `/tmp/artel_product_compare/gemini-cli/README.md:23-26`, `118-121`
- CLI MCP command: `/tmp/artel_product_compare/gemini-cli/packages/cli/src/commands/mcp.ts:7-35`
- many MCP config/test files under `packages/cli/src/config/mcp/` and command tests
- integration test for simple MCP server: `/tmp/artel_product_compare/gemini-cli/integration-tests/simple-mcp-server.test.ts:1-220`

### 6. Extensions and skills are productized

Evidence:

- extension command implementations and tests under `packages/cli/src/commands/extensions/`
- skills command implementations under `packages/cli/src/commands/skills/`
- config/extension manager modules and tests under `packages/cli/src/config/`

### 7. Checkpointing/session restore are real features

Evidence:

- README mentions conversation checkpointing: `/tmp/artel_product_compare/gemini-cli/README.md:129-130`
- integration test: `/tmp/artel_product_compare/gemini-cli/integration-tests/checkpointing.test.ts:13-154`

### 8. Sandbox and policy systems are explicit

Evidence:

- sandbox config/integration files in `packages/cli/src/config/` and `packages/core/src/config/`
- representative test: `/tmp/artel_product_compare/gemini-cli/packages/core/src/config/sandbox-integration.test.ts:52-64`

### 9. Very high visible quality surface via tests/evals

Evidence:

- 40 integration tests under `integration-tests/*.test.ts`
- numerous unit tests across cli/core packages
- dedicated `evals/` directory with many scenario evals, including tool use, subagents, plan mode, memory, etc.

## Weak points

### 1. Strongest provider coupling in the comparison

Although Gemini CLI is open source and extensible, its product center of gravity is still Gemini/Google.

Evidence:

- README positioning is explicitly Gemini-first: `/tmp/artel_product_compare/gemini-cli/README.md:11-15`, `17-29`
- auth section emphasizes Google account, Gemini API key, Vertex AI: `/tmp/artel_product_compare/gemini-cli/README.md:146-212`

It does have extensibility and MCP, but it is not provider-agnostic in the same way OpenCode aims to be.

### 2. High complexity

Gemini CLI is a large Node/TS workspace with many packages, tests, evals, extension systems, hook systems, and agent systems. That is a real maintenance burden as well as a strength.

### 3. Likely steeper contributor learning curve than Aider or Artel

The presence of many subsystems — hooks, agents, MCP, ACP, extensions, sandboxing, checkpointing, policy, IDE companion — makes it powerful but less lightweight.

---

## Comparative feature matrix

Legend:

- **✅** confirmed in inspected source/docs/tests
- **◐** partially present / narrower / less developed
- **❌** not found in inspected source

| Feature | Artel | OpenCode | Aider | Goose | Gemini CLI |
|---|---:|---:|---:|---:|---:|
| Terminal coding agent | ✅ | ✅ | ✅ | ✅ | ✅ |
| One-shot / non-interactive mode | ✅ `artel -p` | ✅ `run` | ✅ CLI/main supports one-shot file/task flows | ✅ `run` | ✅ `-p` / nonInteractiveCli |
| Local interactive mode | ✅ | ✅ | ✅ | ✅ | ✅ |
| Headless server | ✅ | ✅ | ❌ not found | ✅ | ◐ A2A/server components exist, but primary product is CLI |
| Remote attach/connect | ✅ | ✅ | ❌ | ◐ server/ACP/gateway/session infra exists | ◐ agent/remote invocation exists, not primarily framed like OpenCode |
| REST/control plane | ✅ | ✅ | ❌ | ✅ | ◐ not main product surface in inspected docs |
| ACP support | ✅ | ✅ | ❌ | ✅ | ✅ |
| MCP support | ✅ | ✅ | ❌ | ✅ | ✅ |
| MCP OAuth | ❌ | ✅ | ❌ | ◐ auth-related infra via rmcp/features, not as visibly productized as OpenCode | ◐ not primary README claim, but MCP system is substantial |
| Worktree support | ✅ | ✅ | ❌ not found as dedicated subsystem | ❌ not primary surfaced feature | ❌ not found as dedicated subsystem |
| Built-in agent/subagent system | ◐ delegation tools | ✅ | ❌ | ✅ | ✅ |
| Hooks system | ◐ via extensions/hooks concept, not as deep | ✅ plugin hooks | ❌ not as first-class hook system | ◐ extensions/platform hooks | ✅ very strong |
| Extensions/plugins | ✅ Python extensions | ✅ typed plugin SDK | ❌ not comparable plugin platform found | ✅ extensions/builtin extensions | ✅ extensions + skills |
| Scheduling | ✅ | ❌ not found in inspected source | ❌ | ✅ | ❌ not found as first-class schedule CLI, though automation exists |
| LSP support | ❌ | ✅ | ❌ | ❌ not clearly surfaced | ❌ not clearly surfaced as LSP subsystem |
| Web UI / browser UI | ◐ command exists, entrypoint placeholder in this checkout | ✅ | ✅ experimental Streamlit GUI | ✅ desktop/server ecosystem | ❌ terminal-first; IDE companion exists instead |
| Desktop app | ❌ | ✅ | ❌ | ✅ | ❌ |
| GitHub / PR workflow | ❌ dedicated product flow not found | ✅ | ◐ git-first, but no equivalent product surface | ◐ recipes/gateway/platform integrations | ✅ GitHub integration in README |
| Checkpointing / resume depth | ◐ session resume exists | ✅ | ◐ chat/session history | ✅ session manager substantial | ✅ explicit checkpointing |
| Provider breadth / agnosticism | ✅ broad | ✅ broad | ✅ broad LLM support claim | ✅ multi-model/provider support claim | ◐ Gemini-centered |
| Visible test breadth | ✅ 83 test files | ✅ 105 test files | ✅ 36 test files | ✅ large Rust test surface | ✅ many unit/integration tests + evals |
| Implementation inspectability | ✅ high | ✅ high | ✅ high | ✅ high | ✅ high |

---

## Ranking by product goal

## If your target is “broadest modern open-source coding-agent product”

1. **OpenCode**
2. **Gemini CLI**
3. **Goose**
4. **Artel**
5. **Aider**

Rationale:

- OpenCode has the broadest multi-surface platform and strong MCP/LSP/agent/plugin depth.
- Gemini CLI has exceptional modern agent/hook/extension/test depth, but is more provider-centered and less obviously multi-surface than OpenCode.
- Goose is strong and broad, but a bit less obviously polished as a single cohesive surface than OpenCode/Gemini CLI from inspected materials.
- Artel has good platform bones, but less implementation depth in key areas.
- Aider is intentionally narrower.

## If your target is “best terminal-first coding workflow with minimal platform overhead”

1. **Aider**
2. **Artel**
3. **Gemini CLI**
4. **Goose**
5. **OpenCode**

Rationale:

- Aider is most focused and direct for terminal pair-programming in repos.
- Artel is straightforward Python and inspectable, though more platform-oriented than Aider.
- Gemini CLI is terminal-first too, but more system-heavy.
- Goose/OpenCode are broader platforms with more complexity.

## If your target is “most relevant reference set for evolving Artel”

Best references:

1. **OpenCode** — for breadth, MCP depth, agent system, control plane, LSP, product packaging
2. **Gemini CLI** — for subagents, hooks, extension/skills system, checkpointing, eval discipline
3. **Goose** — for ACP/server/MCP/scheduler/recipe architecture in a strongly typed systems-language codebase
4. **Aider** — for terminal ergonomics, git-centric workflow, and practical coding-loop UX

---

## Artel-specific gap analysis against the strongest peers

These are the highest-confidence gaps, based on inspectable source.

### 1. Deepen the agent/subagent architecture

Compared with OpenCode/Gemini CLI/Goose, Artel’s orchestration layer is still thin.

Current evidence:

- `packages/artel-core/src/artel_core/orchestration.py:1-25`
- `packages/artel-core/src/artel_core/delegation/service.py:1-239`

Needed direction:

- richer agent registry and agent types
- better user-facing delegation controls
- stronger agent lifecycle and configuration surface

### 2. Expand MCP beyond config/runtime basics

Compared with OpenCode and Gemini CLI, Artel needs:

- richer remote MCP auth lifecycle
- more complete management UX
- deeper runtime status/connection semantics

Current evidence:

- `packages/artel-core/src/artel_core/mcp.py:17-280`
- `packages/artel-core/src/artel_core/cli.py:192-327`

### 3. Decide whether Artel wants to remain Python-platform-first or become full product suite

OpenCode/Gemini CLI/Goose are much broader products.

If yes, Artel likely needs:

- richer interactive product UX
- stronger extension/bundled capability packaging
- more finished remote/admin/operator surfaces

If no, then Artel should instead sharpen its identity around:

- Python-native hackability
- rules/policy
- explicit server APIs
- practical coding workflow

### 4. Consider code-intelligence features explicitly

OpenCode’s visible LSP support is a meaningful advantage.

Artel currently has search/read/edit/bash/worktree/MCP, but no visible LSP layer.

### 5. Tighten docs/runtime parity

Most important currently visible mismatch:

- README still advertises experimental web UI
- current checkout’s `artel_web/app.py` is placeholder-only

---

## Bottom line

### Best overall open-source comparators for Artel

If the question is: **which projects matter most as comparators for Artel today?**

The answer is:

- **OpenCode** — strongest overall benchmark
- **Gemini CLI** — strongest benchmark for modern agent/hook/subagent architecture
- **Goose** — strong benchmark for server+ACP+MCP+scheduler+extension architecture
- **Aider** — strong benchmark for terminal coding UX and git-first workflow quality

### Current Artel position

Artel is no longer best judged by unfinished employee/web ambitions.

On the code that is actually present today, Artel is best described as:

- an **inspectable Python coding-agent platform**
- with **good server/control-plane bones**
- **strong policy/rules support**
- **basic-but-real MCP/worktree/delegation/scheduling**
- but **still behind the leading open-source agentic coding projects in depth and completeness**

That gap is most visible against **OpenCode** and **Gemini CLI**.

---

## Files central to this assessment

### Artel

- `README.md`
- `pyproject.toml`
- `packages/artel-core/src/artel_core/cli.py`
- `packages/artel-core/src/artel_core/artel_bootstrap.py`
- `packages/artel-core/src/artel_core/builtin_capabilities.py`
- `packages/artel-core/src/artel_core/delegation/service.py`
- `packages/artel-core/src/artel_core/orchestration.py`
- `packages/artel-core/src/artel_core/mcp.py`
- `packages/artel-core/src/artel_core/worktree.py`
- `packages/artel-core/src/artel_core/tools/builtins.py`
- `packages/artel-core/src/artel_core/tools/web_search.py`
- `packages/artel-core/src/artel_core/tools/web_fetch.py`
- `packages/artel-server/src/artel_server/server.py`
- `packages/artel-web/src/artel_web/app.py`
- `tests/test_builtin_capabilities.py`
- `tests/test_delegation_tools.py`
- `tests/test_delegation_server_api.py`
- `tests/test_worktree.py`
- `tests/test_mcp_cli_and_runtime.py`
- `tests/test_schedule_cli.py`
- `tests/test_rules_cli.py`

### OpenCode

- `/tmp/artel_product_compare/opencode/README.md`
- `/tmp/artel_product_compare/opencode/package.json`
- `/tmp/artel_product_compare/opencode/packages/opencode/package.json`
- `/tmp/artel_product_compare/opencode/packages/opencode/src/index.ts`
- `/tmp/artel_product_compare/opencode/packages/opencode/src/agent/agent.ts`
- `/tmp/artel_product_compare/opencode/packages/opencode/src/cli/cmd/agent.ts`
- `/tmp/artel_product_compare/opencode/packages/opencode/src/cli/cmd/run.ts`
- `/tmp/artel_product_compare/opencode/packages/opencode/src/cli/cmd/mcp.ts`
- `/tmp/artel_product_compare/opencode/packages/opencode/src/mcp/index.ts`
- `/tmp/artel_product_compare/opencode/packages/opencode/src/worktree/index.ts`
- `/tmp/artel_product_compare/opencode/packages/opencode/src/lsp/index.ts`
- `/tmp/artel_product_compare/opencode/packages/opencode/src/server/server.ts`
- `/tmp/artel_product_compare/opencode/packages/opencode/src/control-plane/workspace-server/server.ts`
- `/tmp/artel_product_compare/opencode/packages/plugin/src/index.ts`
- `/tmp/artel_product_compare/opencode/packages/opencode/src/plugin/index.ts`

### Aider

- `/tmp/artel_product_compare/aider/README.md`
- `/tmp/artel_product_compare/aider/pyproject.toml`
- `/tmp/artel_product_compare/aider/aider/main.py`
- `/tmp/artel_product_compare/aider/aider/commands.py`
- `/tmp/artel_product_compare/aider/aider/watch.py`
- `/tmp/artel_product_compare/aider/aider/gui.py`
- `/tmp/artel_product_compare/aider/tests/basic/test_main.py`

### Goose

- `/tmp/artel_product_compare/goose/README.md`
- `/tmp/artel_product_compare/goose/Cargo.toml`
- `/tmp/artel_product_compare/goose/crates/goose-cli/src/cli.rs`
- `/tmp/artel_product_compare/goose/crates/goose-server/src/main.rs`
- `/tmp/artel_product_compare/goose/crates/goose/src/scheduler.rs`
- `/tmp/artel_product_compare/goose/crates/goose-mcp/src/lib.rs`
- `/tmp/artel_product_compare/goose/crates/goose/tests/mcp_integration_test.rs`
- `/tmp/artel_product_compare/goose/crates/goose-acp/tests/server_test.rs`
- `/tmp/artel_product_compare/goose/scripts/test_mcp.sh`

### Gemini CLI

- `/tmp/artel_product_compare/gemini-cli/README.md`
- `/tmp/artel_product_compare/gemini-cli/package.json`
- `/tmp/artel_product_compare/gemini-cli/packages/cli/src/nonInteractiveCli.ts`
- `/tmp/artel_product_compare/gemini-cli/packages/cli/src/nonInteractiveCliCommands.ts`
- `/tmp/artel_product_compare/gemini-cli/packages/cli/src/commands/mcp.ts`
- `/tmp/artel_product_compare/gemini-cli/packages/cli/src/ui/commands/agentsCommand.ts`
- `/tmp/artel_product_compare/gemini-cli/packages/core/src/agents/generalist-agent.ts`
- `/tmp/artel_product_compare/gemini-cli/packages/core/src/agents/subagent-tool.ts`
- `/tmp/artel_product_compare/gemini-cli/packages/core/src/hooks/hookSystem.ts`
- `/tmp/artel_product_compare/gemini-cli/packages/core/src/config/sandbox-integration.test.ts`
- `/tmp/artel_product_compare/gemini-cli/integration-tests/checkpointing.test.ts`
- `/tmp/artel_product_compare/gemini-cli/integration-tests/simple-mcp-server.test.ts`
- `/tmp/artel_product_compare/gemini-cli/integration-tests/hooks-system.test.ts`

---

## Final verdict

Among inspectable open-source agentic coding projects I reviewed here:

- **Best overall benchmark:** OpenCode
- **Best modern agent architecture benchmark:** Gemini CLI
- **Strong systems-platform benchmark:** Goose
- **Best focused terminal coding workflow benchmark:** Aider
- **Best current description of Artel:** promising Python platform with strong inspectability and policy/server bones, but not yet at parity with the leading open-source products in implementation depth
