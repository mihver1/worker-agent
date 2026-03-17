# AGENTS

## Validator: Artel backlog compliance reviewer

### Purpose
Validate the current repository implementation against `Artel implementation backlog v0.md`, with special focus on employee-related work.

### Scope
- Compare implemented features, tests, docs, and CLI behavior to the backlog.
- Report completed, partial, and missing items.
- Prefer evidence from code and tests over assumptions.

### Checklist
1. Read `Artel implementation backlog v0.md`.
2. Inspect employee/orchestration implementation under `packages/artel-core/src/artel_core/`.
3. Inspect tests covering employee lifecycle and CLI.
4. Inspect docs/README for alignment with backlog claims.
5. Produce a validation report with:
   - matched backlog items
   - partial items
   - missing items
   - concrete file references

### Useful files
- `Artel implementation backlog v0.md`
- `packages/artel-core/src/artel_core/orchestration.py`
- `packages/artel-core/src/artel_core/cli.py`
- `packages/artel-core/src/artel_core/cmux.py`
- `packages/artel-core/src/artel_core/worktree.py`
- `tests/test_employee_cli.py`
- `tests/test_builtin_capabilities.py`
- `README.md`

## Validator: Employee lifecycle specialist

### Purpose
Specifically validate Workstream 7 requirements around employee lifecycle.

### Checklist
- Employee record fields exist: id, display name, assigned task, status, cmux surface, project path, worktree path, branch, latest updates.
- Creation flow allocates/plans worktree.
- Creation flow creates/ensures cmux workspace/surface.
- CLI exposes employee creation.
- Runtime supports update/remove/register surface/attach worktree.
- Tests cover create/update/remove and CLI happy/error paths.
- Identify missing cancel/stop/focus/control-plane behaviors.

## Validator: Docs and product narrative reviewer

### Purpose
Check whether docs reflect the current backlog state.

### Checklist
- README uses Artel naming.
- README describes employee/orchestrator/dashboard reality accurately.
- README avoids overstating cmux-only flow if not fully documented.
- Note gaps versus backlog Workstreams 8-10.
