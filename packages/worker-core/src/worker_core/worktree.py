"""First-party git worktree operations and `/wt` command handling."""

from __future__ import annotations

import os
import secrets
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_WORKTREE_BASE = Path.home() / "Projects" / "worker_worktrees"
WORKTREE_BASE_ENV_VARS = (
    "WORKER_EXT_WORKTREE_BASE_DIR",
    "WORKTREE_BASE_DIR",
    "WORKTREES_BASE",
)


class WorktreeError(RuntimeError):
    """Raised when a git worktree operation cannot be completed."""


@dataclass(slots=True, frozen=True)
class WorktreeInfo:
    """Structured `git worktree list --porcelain` entry."""

    path: Path
    head: str
    branch: str | None = None
    detached: bool = False
    bare: bool = False
    locked: str | None = None
    prunable: str | None = None


@dataclass(slots=True, frozen=True)
class CreateResult:
    """Result of a managed worktree creation request."""

    path: Path
    source_ref: str
    branch: str | None
    detached: bool
    created_branch: bool = False
    note: str | None = None


@dataclass(slots=True, frozen=True)
class FinishResult:
    """Result of finishing a managed worktree into the current branch."""

    source_path: Path
    source_branch: str
    target_branch: str


@dataclass(slots=True, frozen=True)
class CreateCommand:
    """Create command for `/wt`."""

    branch: str | None = None


@dataclass(slots=True, frozen=True)
class ListCommand:
    """List command for `/wt`."""


@dataclass(slots=True, frozen=True)
class RemoveCommand:
    """Remove command for `/wt`."""

    target: str


@dataclass(slots=True, frozen=True)
class FinishCommand:
    """Fast-forward merge command for `/wt`."""

    target: str


@dataclass(slots=True, frozen=True)
class HelpCommand:
    """Help command for `/wt`."""


type WorktreeCommand = CreateCommand | ListCommand | RemoveCommand | FinishCommand | HelpCommand


def usage_text() -> str:
    """Render `/wt` command help."""

    return "\n".join(
        [
            "Usage:",
            "  /wt [branch]",
            "  /wt list",
            "  /wt rm <uniq_subpath>",
            "  /wt finish <uniq_subpath>",
            "  /wt help",
            "",
            "Behavior:",
            "  - missing branch => detached worktree from current branch tip",
            "  - existing free branch => worktree on that branch",
            "  - existing checked-out branch => detached worktree from that branch tip",
            "  - new branch name => create branch from current HEAD",
            "  - finish/merge => fast-forward source worktree branch into the current branch",
        ]
    )


def parse_wt_command(arg: str) -> WorktreeCommand:
    """Parse slash-command arguments for `/wt`."""

    tokens = shlex.split(arg)
    if not tokens:
        return CreateCommand()

    first = tokens[0].lower()
    if first in {"help", "-h", "--help"}:
        if len(tokens) != 1:
            raise WorktreeError("Help does not accept extra arguments.")
        return HelpCommand()
    if first in {"list", "ls"}:
        if len(tokens) != 1:
            raise WorktreeError("Usage: /wt list")
        return ListCommand()
    if first in {"rm", "remove"}:
        if len(tokens) != 2:
            raise WorktreeError("Usage: /wt rm <uniq_subpath>")
        return RemoveCommand(target=tokens[1])
    if first in {"finish", "merge"}:
        if len(tokens) != 2:
            raise WorktreeError("Usage: /wt finish <uniq_subpath>")
        return FinishCommand(target=tokens[1])
    if len(tokens) != 1:
        raise WorktreeError(
            "Usage: /wt [branch] | /wt list | /wt rm <uniq_subpath> | /wt finish <uniq_subpath>"
        )
    return CreateCommand(branch=tokens[0])


def parse_worktree_porcelain(output: str) -> list[WorktreeInfo]:
    """Parse `git worktree list --porcelain` output into structured entries."""

    entries: list[WorktreeInfo] = []
    current: dict[str, str | bool] = {}

    def flush() -> None:
        if not current:
            return
        worktree_path = current.get("worktree")
        if not isinstance(worktree_path, str):
            current.clear()
            return
        branch_ref = current.get("branch")
        branch = None
        if isinstance(branch_ref, str):
            branch = branch_ref.removeprefix("refs/heads/")
        entries.append(
            WorktreeInfo(
                path=Path(worktree_path).resolve(),
                head=str(current.get("HEAD", "")),
                branch=branch,
                detached=bool(current.get("detached")),
                bare=bool(current.get("bare")),
                locked=_normalize_optional_flag(current.get("locked")),
                prunable=_normalize_optional_flag(current.get("prunable")),
            )
        )
        current.clear()

    for raw_line in output.splitlines():
        if not raw_line.strip():
            flush()
            continue
        key, _, value = raw_line.partition(" ")
        if key in {"detached", "bare"} and not value:
            current[key] = True
            continue
        current[key] = value
    flush()
    return entries


def resolve_remove_target(
    target: str,
    *,
    worktrees: list[WorktreeInfo],
    managed_root: Path,
    primary_worktree: Path,
) -> Path:
    """Resolve a unique managed worktree path from a path fragment or absolute path."""

    target = target.strip()
    if not target:
        raise WorktreeError("Usage: /wt rm <uniq_subpath>")

    managed_root = managed_root.resolve()
    primary_worktree = primary_worktree.resolve()
    removable_paths = [
        worktree.path.resolve()
        for worktree in worktrees
        if worktree.path.resolve() != primary_worktree
        and _is_relative_to(worktree.path.resolve(), managed_root)
    ]

    absolute_target = Path(target).expanduser()
    if absolute_target.is_absolute():
        resolved_target = absolute_target.resolve()
        if resolved_target not in removable_paths:
            raise WorktreeError(f"No managed worktree matches: {resolved_target}")
        return resolved_target

    matches: list[Path] = []
    for candidate in removable_paths:
        relative = candidate.relative_to(managed_root).as_posix()
        values = (candidate.name, relative, candidate.as_posix())
        if any(target in value for value in values):
            matches.append(candidate)

    if not matches:
        raise WorktreeError(f"No managed worktree matches: {target}")
    if len(matches) > 1:
        options = ", ".join(path.relative_to(managed_root).as_posix() for path in matches)
        raise WorktreeError(f"Ambiguous worktree target '{target}': {options}")
    return matches[0]


def format_create_result(result: CreateResult) -> str:
    """Render a human-readable creation result."""

    lines = [
        "Created worktree:",
        f"- path: {result.path}",
        f"- source: {result.source_ref}",
        f"- checkout: {'detached' if result.detached else f'branch {result.branch}'}",
    ]
    if result.created_branch and result.branch:
        lines.append("- branch_status: created")
    elif result.branch:
        lines.append("- branch_status: existing")
    if result.note:
        lines.append(f"- note: {result.note}")
    return "\n".join(lines)


def format_remove_result(path: Path) -> str:
    """Render a human-readable remove result."""

    return f"Removed worktree: {path}"


def format_finish_result(result: FinishResult) -> str:
    """Render a human-readable finish result."""

    return "\n".join(
        [
            "Finished worktree:",
            f"- source_path: {result.source_path}",
            f"- source_branch: {result.source_branch}",
            f"- target_branch: {result.target_branch}",
            "- merge: fast-forward",
        ]
    )


def format_worktree_list(
    *,
    worktrees: list[WorktreeInfo],
    managed_root: Path,
    primary_worktree: Path,
) -> str:
    """Render a human-readable list of repository worktrees."""

    if not worktrees:
        return "No worktrees found."

    managed_root = managed_root.resolve()
    primary_worktree = primary_worktree.resolve()
    lines = ["Worktrees:"]
    for worktree in worktrees:
        flags: list[str] = []
        resolved_path = worktree.path.resolve()
        if resolved_path == primary_worktree:
            flags.append("primary")
        elif _is_relative_to(resolved_path, managed_root):
            flags.append("managed")
        else:
            flags.append("external")
        if worktree.detached:
            flags.append("detached")
        if worktree.locked is not None:
            flags.append("locked")
        if worktree.prunable is not None:
            flags.append("prunable")
        label = worktree.branch or "detached"
        head = worktree.head[:7] if worktree.head else "unknown"
        lines.append(f"- {label} [{', '.join(flags)}] {worktree.path} @ {head}")
    return "\n".join(lines)


class WorktreeManager:
    """High-level git worktree operations for the current repository."""

    def __init__(self, project_dir: str, base_dir: Path | None = None) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.base_dir = resolve_worktree_base_dir(base_dir)
        self._primary_worktree: Path | None = None

    @property
    def primary_worktree(self) -> Path:
        """Main checkout path shared by all linked worktrees."""

        if self._primary_worktree is None:
            common_git_dir = Path(
                self._git_stdout("rev-parse", "--path-format=absolute", "--git-common-dir")
            ).resolve()
            self._primary_worktree = common_git_dir.parent
        return self._primary_worktree

    @property
    def repo_name(self) -> str:
        """Repository directory name used in the managed worktree path."""

        return self.primary_worktree.name

    @property
    def managed_repo_dir(self) -> Path:
        """Managed worktree directory for the current repository."""

        return self.base_dir / self.repo_name

    def create_worktree(self, branch: str | None = None) -> CreateResult:
        """Create a managed worktree from the requested branch or current branch."""

        branch = branch.strip() if branch else None
        if branch is None:
            current_branch = self.current_branch
            if current_branch is None:
                raise WorktreeError("Current worktree is detached; pass a branch name explicitly.")
            worktree_path = self._new_managed_path(current_branch)
            self._git("worktree", "add", "--detach", str(worktree_path), current_branch)
            return CreateResult(
                path=worktree_path,
                source_ref=current_branch,
                branch=None,
                detached=True,
                note=(
                    "No branch was provided, so the worktree was created "
                    f"detached from {current_branch}."
                ),
            )

        if self._local_branch_exists(branch):
            if branch in self.checked_out_branches:
                worktree_path = self._new_managed_path(branch)
                self._git("worktree", "add", "--detach", str(worktree_path), branch)
                return CreateResult(
                    path=worktree_path,
                    source_ref=branch,
                    branch=None,
                    detached=True,
                    note=(
                        f"Branch {branch} is already checked out, so the worktree "
                        "was created detached."
                    ),
                )
            worktree_path = self._new_managed_path(branch)
            self._git("worktree", "add", str(worktree_path), branch)
            return CreateResult(
                path=worktree_path,
                source_ref=branch,
                branch=branch,
                detached=False,
            )

        if self._remote_branch_exists(branch):
            worktree_path = self._new_managed_path(branch)
            remote_ref = f"origin/{branch}"
            self._git("worktree", "add", "--track", "-b", branch, str(worktree_path), remote_ref)
            return CreateResult(
                path=worktree_path,
                source_ref=remote_ref,
                branch=branch,
                detached=False,
                created_branch=True,
                note=f"Created local tracking branch {branch} from {remote_ref}.",
            )

        base_ref = self.current_branch or "HEAD"
        worktree_path = self._new_managed_path(branch)
        self._git("worktree", "add", "-b", branch, str(worktree_path), base_ref)
        return CreateResult(
            path=worktree_path,
            source_ref=base_ref,
            branch=branch,
            detached=False,
            created_branch=True,
        )

    def list_worktrees(self) -> list[WorktreeInfo]:
        """Return current repository worktrees."""

        return parse_worktree_porcelain(self._git_stdout("worktree", "list", "--porcelain"))

    def remove_worktree(self, target: str) -> Path:
        """Remove a managed worktree by path or unique path fragment."""

        path = resolve_remove_target(
            target,
            worktrees=self.list_worktrees(),
            managed_root=self.managed_repo_dir,
            primary_worktree=self.primary_worktree,
        )
        self._git("worktree", "remove", str(path))
        self._git("worktree", "prune")
        self._cleanup_managed_repo_dir()
        return path

    def finish_worktree(self, target: str) -> FinishResult:
        """Fast-forward a managed worktree branch into the current branch."""

        current_branch = self.current_branch
        if current_branch is None:
            raise WorktreeError(
                "Current worktree is detached; checkout the target branch before finishing."
            )

        worktrees = self.list_worktrees()
        source_path = resolve_remove_target(
            target,
            worktrees=worktrees,
            managed_root=self.managed_repo_dir,
            primary_worktree=self.primary_worktree,
        )
        source_worktree = self._worktree_by_path(worktrees, source_path)
        if source_worktree.branch is None:
            raise WorktreeError("Cannot finish a detached worktree; create it from a branch first.")
        if source_worktree.branch == current_branch:
            raise WorktreeError(
                f"Source branch {source_worktree.branch} is already the current branch."
            )

        self._ensure_clean_worktree(
            self.project_dir,
            "Current worktree has uncommitted changes; commit or stash them first.",
        )
        self._ensure_clean_worktree(
            source_worktree.path,
            f"Source worktree {source_worktree.path} has uncommitted changes.",
        )
        self._ensure_fast_forward_possible(
            target_branch=current_branch,
            source_branch=source_worktree.branch,
        )
        self._git("merge", "--ff-only", source_worktree.branch)
        return FinishResult(
            source_path=source_worktree.path,
            source_branch=source_worktree.branch,
            target_branch=current_branch,
        )

    @property
    def current_branch(self) -> str | None:
        """Return the current branch, or `None` if HEAD is detached."""

        result = self._git(
            "symbolic-ref",
            "--quiet",
            "--short",
            "HEAD",
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        stderr = result.stderr.strip().lower()
        if "not a symbolic ref" in stderr or result.returncode == 1:
            return None
        raise WorktreeError(
            result.stderr.strip() or result.stdout.strip() or "git symbolic-ref failed"
        )

    @property
    def checked_out_branches(self) -> set[str]:
        """Branches currently checked out in any worktree for this repository."""

        return {
            worktree.branch for worktree in self.list_worktrees() if worktree.branch is not None
        }

    def _local_branch_exists(self, branch: str) -> bool:
        return (
            self._git(
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{branch}",
                check=False,
            ).returncode
            == 0
        )

    def _remote_branch_exists(self, branch: str) -> bool:
        return (
            self._git(
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/remotes/origin/{branch}",
                check=False,
            ).returncode
            == 0
        )

    def _new_managed_path(self, name_hint: str) -> Path:
        safe_name = _sanitize_name(name_hint)
        self.managed_repo_dir.mkdir(parents=True, exist_ok=True)
        for _ in range(32):
            candidate = self.managed_repo_dir / f"{safe_name}_{secrets.token_hex(3)}"
            if not candidate.exists():
                return candidate
        raise WorktreeError("Failed to allocate a unique managed worktree path.")

    def _cleanup_managed_repo_dir(self) -> None:
        if self.managed_repo_dir.exists() and not any(self.managed_repo_dir.iterdir()):
            self.managed_repo_dir.rmdir()

    def _git_stdout(self, *args: str) -> str:
        return self._git(*args).stdout.strip()

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self._git_at(self.project_dir, *args, check=check)

    def _git_at(
        self,
        cwd: Path,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "git command failed"
            raise WorktreeError(message)
        return result

    def _ensure_clean_worktree(self, path: Path, error_message: str) -> None:
        if self._git_at(path, "status", "--porcelain").stdout.strip():
            raise WorktreeError(error_message)

    def _ensure_fast_forward_possible(self, *, target_branch: str, source_branch: str) -> None:
        result = self._git(
            "merge-base",
            "--is-ancestor",
            target_branch,
            source_branch,
            check=False,
        )
        if result.returncode == 0:
            return
        if result.returncode == 1:
            raise WorktreeError(
                f"Fast-forward merge from {source_branch} into {target_branch} is not possible."
            )
        raise WorktreeError(
            result.stderr.strip() or result.stdout.strip() or "git merge-base failed"
        )

    def _worktree_by_path(self, worktrees: list[WorktreeInfo], path: Path) -> WorktreeInfo:
        resolved_path = path.resolve()
        for worktree in worktrees:
            if worktree.path.resolve() == resolved_path:
                return worktree
        raise WorktreeError(f"Worktree metadata not found for {resolved_path}")


def _execute_worktree_command(manager: WorktreeManager, command: WorktreeCommand) -> str:
    if isinstance(command, HelpCommand):
        return usage_text()
    if isinstance(command, ListCommand):
        return format_worktree_list(
            worktrees=manager.list_worktrees(),
            managed_root=manager.managed_repo_dir,
            primary_worktree=manager.primary_worktree,
        )
    if isinstance(command, RemoveCommand):
        removed_path = manager.remove_worktree(command.target)
        return format_remove_result(removed_path)
    if isinstance(command, FinishCommand):
        finished = manager.finish_worktree(command.target)
        return format_finish_result(finished)

    created = manager.create_worktree(command.branch)
    return format_create_result(created)


def run_worktree_command(project_dir: str, arg: str) -> str:
    """Execute `/wt` command syntax against *project_dir*."""

    try:
        command = parse_wt_command(arg)
    except WorktreeError as exc:
        return f"wt error: {exc}\n\n{usage_text()}"

    manager = WorktreeManager(project_dir)
    try:
        return _execute_worktree_command(manager, command)
    except WorktreeError as exc:
        return f"wt error: {exc}"


def resolve_worktree_base_dir(base_dir: Path | None = None) -> Path:
    """Resolve the base directory used for managed worktrees."""

    if base_dir is not None:
        return base_dir.expanduser().resolve()
    for env_var in WORKTREE_BASE_ENV_VARS:
        value = os.environ.get(env_var, "").strip()
        if value:
            return Path(value).expanduser().resolve()
    return DEFAULT_WORKTREE_BASE.resolve()


def _normalize_optional_flag(value: str | bool | None) -> str | None:
    if value is None or value is False:
        return None
    if value is True:
        return ""
    return value


def _sanitize_name(value: str) -> str:
    sanitized = value.replace("/", "_").replace(" ", "_").strip("_")
    return sanitized or "worktree"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


__all__ = [
    "CreateCommand",
    "CreateResult",
    "FinishCommand",
    "FinishResult",
    "HelpCommand",
    "ListCommand",
    "RemoveCommand",
    "WorktreeCommand",
    "WorktreeError",
    "WorktreeInfo",
    "WorktreeManager",
    "format_create_result",
    "format_finish_result",
    "format_remove_result",
    "format_worktree_list",
    "parse_worktree_porcelain",
    "parse_wt_command",
    "resolve_remove_target",
    "resolve_worktree_base_dir",
    "run_worktree_command",
    "usage_text",
]
