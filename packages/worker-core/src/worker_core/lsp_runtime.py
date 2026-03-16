"""First-party LSP runtime for semantic code-intelligence tools."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from collections import defaultdict
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from worker_core.config import CONFIG_DIR, LspConfig, LspServerConfig
from worker_core.extensions import ExtensionContext

_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
}

_LANGUAGE_IDS = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cjs": "javascript",
    ".cs": "csharp",
    ".cts": "typescript",
    ".go": "go",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".mjs": "javascript",
    ".mts": "typescript",
    ".py": "python",
    ".rs": "rust",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
}

_LSP_STATUS = Literal["active", "available", "disabled", "unavailable"]


def _workspace_name(path: str) -> str:
    name = Path(path).resolve().name
    return name or "workspace"


def _file_uri(path: str) -> str:
    return Path(path).resolve().as_uri()


def _path_from_uri(uri: str) -> str:
    if not uri.startswith("file://"):
        return uri
    parsed = urlsplit(uri)
    path = url2pathname(unquote(parsed.path))
    if parsed.netloc and parsed.netloc != "localhost":
        return f"//{parsed.netloc}{path}"
    return path


def _resolve_path(project_dir: str, raw_path: str) -> str:
    path = Path(raw_path)
    if path.is_absolute():
        return str(path.resolve())
    return str((Path(project_dir) / path).resolve())


def _language_id_for_path(path: str) -> str:
    resolved = Path(path)
    if resolved.suffix:
        return _LANGUAGE_IDS.get(resolved.suffix.lower(), resolved.suffix.removeprefix("."))
    return _LANGUAGE_IDS.get(resolved.name.lower(), "plaintext")


def _find_workspace_root(path: str, markers: Iterable[str]) -> str:
    current = Path(path).resolve()
    if current.is_file():
        current = current.parent
    seen: set[Path] = set()
    while current not in seen:
        seen.add(current)
        for marker in markers:
            if (current / marker).exists():
                return str(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return str(Path(path).resolve().parent if Path(path).is_file() else Path(path).resolve())


def _project_has_extension(project_dir: str, extensions: tuple[str, ...]) -> bool:
    if not extensions:
        return True
    root = Path(project_dir).resolve()
    stack = [root]
    while stack:
        current = stack.pop()
        with suppress(OSError):
            for entry in current.iterdir():
                if entry.name in _IGNORE_DIRS:
                    continue
                if entry.is_dir():
                    stack.append(entry)
                    continue
                if entry.suffix.lower() in extensions:
                    return True
    return False


def _merge_list(preferred: list[str], fallback: tuple[str, ...]) -> tuple[str, ...]:
    values = [item for item in preferred if item]
    if values:
        return tuple(values)
    return fallback


def _merge_dict(preferred: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    merged = dict(fallback)
    merged.update(preferred)
    return merged


@dataclass(slots=True)
class LspServerSpec:
    id: str
    name: str
    commands: tuple[tuple[str, ...], ...]
    extensions: tuple[str, ...]
    root_markers: tuple[str, ...]
    initialization: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    disabled: bool = False
    builtin: bool = False
    command_overridden: bool = False

    @classmethod
    def from_config(cls, server_id: str, config: LspServerConfig) -> LspServerSpec | None:
        if not config.command:
            return None
        return cls(
            id=server_id,
            name=server_id,
            commands=(tuple(config.command),),
            extensions=tuple(ext.lower() for ext in config.extensions if ext),
            root_markers=tuple(config.root_markers or [".git"]),
            initialization=dict(config.initialization),
            env=dict(config.env),
            disabled=config.disabled,
            command_overridden=True,
        )


@dataclass(slots=True)
class LspServerStatus:
    id: str
    name: str
    state: _LSP_STATUS
    enabled: bool
    command: str
    extensions: list[str]
    active_clients: int = 0
    error: str = ""

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LspClientStatus:
    id: str
    name: str
    root: str
    command: str
    open_documents: int

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)


def _builtin_specs() -> dict[str, LspServerSpec]:
    return {
        "python": LspServerSpec(
            id="python",
            name="Python",
            commands=(
                ("basedpyright-langserver", "--stdio"),
                ("pyright-langserver", "--stdio"),
                ("pylsp",),
            ),
            extensions=(".py",),
            root_markers=("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", ".git"),
            builtin=True,
        ),
        "typescript": LspServerSpec(
            id="typescript",
            name="TypeScript / JavaScript",
            commands=(("typescript-language-server", "--stdio"),),
            extensions=(".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs"),
            root_markers=("tsconfig.json", "jsconfig.json", "package.json", ".git"),
            builtin=True,
        ),
        "go": LspServerSpec(
            id="go",
            name="Go",
            commands=(("gopls",),),
            extensions=(".go",),
            root_markers=("go.work", "go.mod", ".git"),
            builtin=True,
        ),
        "rust": LspServerSpec(
            id="rust",
            name="Rust",
            commands=(("rust-analyzer",),),
            extensions=(".rs",),
            root_markers=("Cargo.toml", "rust-project.json", ".git"),
            builtin=True,
        ),
    }


def _resolve_command(command: tuple[str, ...]) -> tuple[str, ...] | None:
    if not command:
        return None
    executable = command[0]
    if os.path.isabs(executable) or os.sep in executable:
        if Path(executable).exists():
            return command
        return None
    resolved = shutil.which(executable)
    if not resolved:
        return None
    return (resolved, *command[1:])


def _binary_name(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def _binary_path(base_dir: Path, name: str) -> Path:
    return base_dir / _binary_name(name)


def _npm_bin_path(prefix: Path, executable: str) -> Path:
    suffix = ".cmd" if sys.platform == "win32" else ""
    return prefix / "node_modules" / ".bin" / f"{executable}{suffix}"


class LspClient:
    """Minimal JSON-RPC client for stdio-based language servers."""

    def __init__(
        self,
        *,
        server_id: str,
        server_name: str,
        root: str,
        command: tuple[str, ...],
        initialization: dict[str, Any],
        env: dict[str, str],
    ) -> None:
        self.server_id = server_id
        self.server_name = server_name
        self.root = root
        self.command = command
        self.initialization = initialization
        self.env = env
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._request_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._documents: dict[str, int] = {}
        self._diagnostics: dict[str, list[dict[str, Any]]] = {}
        self._diagnostic_waiters: dict[str, list[asyncio.Event]] = defaultdict(list)
        self._closed = False

    @property
    def diagnostics(self) -> dict[str, list[dict[str, Any]]]:
        return dict(self._diagnostics)

    @property
    def open_documents(self) -> int:
        return len(self._documents)

    async def start(self) -> None:
        env = dict(os.environ)
        env.update(self.env)
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.root,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())
        await self.send_request(
            "initialize",
            {
                "processId": self.process.pid,
                "rootUri": _file_uri(self.root),
                "workspaceFolders": [
                    {
                        "name": _workspace_name(self.root),
                        "uri": _file_uri(self.root),
                    }
                ],
                "initializationOptions": self.initialization,
                "capabilities": {
                    "window": {"workDoneProgress": True},
                    "workspace": {
                        "configuration": True,
                        "workspaceFolders": True,
                        "didChangeWatchedFiles": {"dynamicRegistration": True},
                    },
                    "textDocument": {
                        "synchronization": {"didOpen": True, "didChange": True},
                        "publishDiagnostics": {"relatedInformation": True},
                    },
                },
            },
            timeout=20.0,
        )
        await self.send_notification("initialized", {})
        if self.initialization:
            await self.send_notification(
                "workspace/didChangeConfiguration",
                {"settings": self.initialization},
            )

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = 10.0,
    ) -> Any:
        self._request_id += 1
        request_id = self._request_id
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[request_id] = future
        await self._send_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    async def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._send_message(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
            }
        )

    async def open_document(self, path: str, *, wait_for_diagnostics: bool = False) -> None:
        resolved_path = str(Path(path).resolve())
        event: asyncio.Event | None = None
        if wait_for_diagnostics:
            event = asyncio.Event()
            self._diagnostic_waiters[resolved_path].append(event)

        text = Path(resolved_path).read_text(encoding="utf-8", errors="replace")
        uri = _file_uri(resolved_path)

        if resolved_path in self._documents:
            version = self._documents[resolved_path] + 1
            self._documents[resolved_path] = version
            await self.send_notification(
                "workspace/didChangeWatchedFiles",
                {
                    "changes": [
                        {
                            "uri": uri,
                            "type": 2,
                        }
                    ]
                },
            )
            await self.send_notification(
                "textDocument/didChange",
                {
                    "textDocument": {"uri": uri, "version": version},
                    "contentChanges": [{"text": text}],
                },
            )
        else:
            self._documents[resolved_path] = 0
            self._diagnostics.pop(resolved_path, None)
            await self.send_notification(
                "workspace/didChangeWatchedFiles",
                {
                    "changes": [
                        {
                            "uri": uri,
                            "type": 1,
                        }
                    ]
                },
            )
            await self.send_notification(
                "textDocument/didOpen",
                {
                    "textDocument": {
                        "uri": uri,
                        "languageId": _language_id_for_path(resolved_path),
                        "version": 0,
                        "text": text,
                    }
                },
            )

        if event is not None:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(event.wait(), timeout=3.0)

    async def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True

        with suppress(Exception):
            await self.send_request("shutdown", {}, timeout=1.0)
        with suppress(Exception):
            await self.send_notification("exit", {})

        for future in self._pending.values():
            if not future.done():
                future.cancel()

        if self.process is not None and self.process.returncode is None:
            with suppress(ProcessLookupError):
                self.process.terminate()
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.process.wait(), timeout=1.0)
            if self.process.returncode is None:
                with suppress(ProcessLookupError):
                    self.process.kill()

        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task

    async def _send_message(self, payload: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError(f"LSP server {self.server_id} is not running")
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        header = f"Content-Length: {len(data)}\r\n\r\n".encode("ascii")
        async with self._write_lock:
            self.process.stdin.write(header)
            self.process.stdin.write(data)
            await self.process.stdin.drain()

    async def _reader_loop(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    raw_line = await self.process.stdout.readline()
                    if raw_line == b"":
                        return
                    if raw_line in {b"\r\n", b"\n"}:
                        break
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    key, _, value = line.partition(":")
                    if key and value:
                        headers[key.lower()] = value.strip()

                length = int(headers.get("content-length", "0") or 0)
                if length <= 0:
                    continue
                body = await self.process.stdout.readexactly(length)
                message = json.loads(body.decode("utf-8", errors="replace"))
                await self._handle_message(message)
        except asyncio.IncompleteReadError:
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError(str(exc)))

    async def _stderr_loop(self) -> None:
        assert self.process is not None
        assert self.process.stderr is not None
        try:
            while await self.process.stderr.readline():
                continue
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _handle_message(self, message: dict[str, Any]) -> None:
        if "method" in message and "id" in message:
            result = await self._handle_server_request(
                str(message.get("method") or ""),
                message.get("params"),
            )
            await self._send_message(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "result": result,
                }
            )
            return

        if "method" in message:
            await self._handle_server_notification(
                str(message.get("method") or ""),
                message.get("params"),
            )
            return

        if "id" in message:
            future = self._pending.get(int(message["id"]))
            if future is None or future.done():
                return
            if "error" in message and message["error"]:
                error = message["error"]
                future.set_exception(RuntimeError(str(error)))
            else:
                future.set_result(message.get("result"))

    async def _handle_server_request(self, method: str, params: Any) -> Any:
        del params
        if method == "window/workDoneProgress/create":
            return None
        if method == "workspace/configuration":
            return [self.initialization]
        if method in {"client/registerCapability", "client/unregisterCapability"}:
            return None
        if method == "workspace/workspaceFolders":
            return [{"name": _workspace_name(self.root), "uri": _file_uri(self.root)}]
        return None

    async def _handle_server_notification(self, method: str, params: Any) -> None:
        if method != "textDocument/publishDiagnostics" or not isinstance(params, dict):
            return
        uri = str(params.get("uri", "") or "")
        path = _path_from_uri(uri)
        diagnostics = params.get("diagnostics")
        if not isinstance(diagnostics, list):
            diagnostics = []
        self._diagnostics[path] = [item for item in diagnostics if isinstance(item, dict)]
        waiters = self._diagnostic_waiters.pop(path, [])
        for waiter in waiters:
            waiter.set()


class LspRuntimeManager:
    """Workspace-aware manager for LSP-backed Artel tools."""

    def __init__(self) -> None:
        self.context: ExtensionContext | None = None
        self.project_dir: str = "."
        self.enabled = True
        self.auto_install = True
        self.install_dir = Path(".")
        self.specs: dict[str, LspServerSpec] = {}
        self.tools: list[Any] = []
        self.errors: dict[str, str] = {}
        self.server_statuses: dict[str, LspServerStatus] = {}
        self._resolved_commands: dict[str, tuple[str, ...] | None] = {}
        self._clients: dict[tuple[str, str], LspClient] = {}
        self._spawning: dict[tuple[str, str], asyncio.Task[LspClient]] = {}
        self._installing: dict[str, asyncio.Task[tuple[str, ...] | None]] = {}

    async def load(self, context: ExtensionContext) -> None:
        from worker_core.tools.lsp import create_lsp_tools

        self.context = context
        self.project_dir = context.project_dir or os.getcwd()
        config = getattr(context.config, "lsp", LspConfig())
        self.auto_install = bool(getattr(config, "auto_install", True))
        self.install_dir = Path(
            str(getattr(config, "install_dir", CONFIG_DIR / "lsp"))
        ).expanduser()
        self.errors = {}
        self.specs = self._build_specs(config)
        self._resolved_commands = {
            server_id: self._first_resolved_command(spec) for server_id, spec in self.specs.items()
        }
        self.server_statuses = {
            server_id: self._build_status(server_id, spec) for server_id, spec in self.specs.items()
        }
        self.tools = create_lsp_tools(self.project_dir, self) if self.enabled else []

    async def reload(self) -> None:
        if self.context is None:
            raise RuntimeError("LSP runtime has no extension context")
        await self.close()
        await self.load(self.context)

    async def close(self) -> None:
        clients = list(self._clients.values())
        self._clients = {}
        for task in list(self._installing.values()):
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._installing = {}
        for task in list(self._spawning.values()):
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
        self._spawning = {}
        for client in clients:
            with suppress(Exception):
                await client.shutdown()
        self._refresh_statuses()

    def status_payload(self) -> dict[str, Any]:
        server_items = [
            self.server_statuses[name].to_payload() for name in sorted(self.server_statuses)
        ]
        client_items = [
            self._client_status(client).to_payload() for _, client in sorted(self._clients.items())
        ]
        summary = {
            "enabled": self.enabled,
            "available": sum(
                1 for item in server_items if item["state"] in {"available", "active"}
            ),
            "disabled": sum(1 for item in server_items if item["state"] == "disabled"),
            "unavailable": sum(1 for item in server_items if item["state"] == "unavailable"),
            "clients": len(client_items),
        }
        return {
            "project_dir": self.project_dir,
            "servers": server_items,
            "clients": client_items,
            "summary": summary,
        }

    def status_text(self) -> str:
        if not self.enabled:
            return "LSP is disabled in config."

        lines = [
            "LSP servers:",
            f"- auto_install={'enabled' if self.auto_install else 'disabled'} "
            f"install_dir={self.install_dir}",
        ]
        if not self.server_statuses:
            lines.append("- none configured")
        else:
            for status in [self.server_statuses[name] for name in sorted(self.server_statuses)]:
                line = (
                    f"- {status.id} [{status.state}] command={status.command} "
                    f"extensions={','.join(status.extensions) or '(any)'} "
                    f"active_clients={status.active_clients}"
                )
                if status.error:
                    line += f" error={status.error}"
                lines.append(line)

        if self._clients:
            lines.append("")
            lines.append("Active clients:")
            for _, client in sorted(self._clients.items()):
                info = self._client_status(client)
                lines.append(
                    f"- {info.id} root={info.root} command={info.command} "
                    f"open_documents={info.open_documents}"
                )
        return "\n".join(lines)

    async def hover(self, path: str, *, line: int, column: int) -> Any:
        client = await self._single_client_for_path(path)
        await client.open_document(path)
        return await client.send_request(
            "textDocument/hover",
            {
                "textDocument": {"uri": _file_uri(path)},
                "position": {"line": line - 1, "character": column - 1},
            },
        )

    async def definition(self, path: str, *, line: int, column: int) -> list[dict[str, Any]]:
        return await self._position_request(
            path,
            "textDocument/definition",
            line=line,
            column=column,
        )

    async def references(self, path: str, *, line: int, column: int) -> list[dict[str, Any]]:
        client = await self._single_client_for_path(path)
        await client.open_document(path)
        result = await client.send_request(
            "textDocument/references",
            {
                "textDocument": {"uri": _file_uri(path)},
                "position": {"line": line - 1, "character": column - 1},
                "context": {"includeDeclaration": True},
            },
        )
        return _normalize_locations(result)

    async def implementation(self, path: str, *, line: int, column: int) -> list[dict[str, Any]]:
        return await self._position_request(
            path,
            "textDocument/implementation",
            line=line,
            column=column,
        )

    async def document_symbols(self, path: str) -> list[dict[str, Any]]:
        client = await self._single_client_for_path(path)
        await client.open_document(path)
        result = await client.send_request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": _file_uri(path)}},
        )
        if not isinstance(result, list):
            return []
        return [item for item in result if isinstance(item, dict)]

    async def workspace_symbols(self, query: str) -> list[dict[str, Any]]:
        clients = await self._workspace_clients()
        results: list[dict[str, Any]] = []
        for client in clients:
            try:
                value = await client.send_request("workspace/symbol", {"query": query})
            except Exception:
                continue
            if isinstance(value, list):
                results.extend(item for item in value if isinstance(item, dict))
        return results

    async def diagnostics(self, path: str) -> list[dict[str, Any]]:
        client = await self._single_client_for_path(path)
        await client.open_document(path, wait_for_diagnostics=True)
        return list(client.diagnostics.get(str(Path(path).resolve()), []))

    def _build_specs(self, config: LspConfig) -> dict[str, LspServerSpec]:
        self.enabled = bool(getattr(config, "enabled", True))
        specs = _builtin_specs()
        overrides = getattr(config, "servers", {}) or {}
        for server_id, override in overrides.items():
            if server_id in specs:
                base = specs[server_id]
                specs[server_id] = LspServerSpec(
                    id=server_id,
                    name=base.name,
                    commands=(tuple(override.command),) if override.command else base.commands,
                    extensions=_merge_list(override.extensions, base.extensions),
                    root_markers=_merge_list(override.root_markers, base.root_markers),
                    initialization=_merge_dict(override.initialization, base.initialization),
                    env=_merge_dict(override.env, base.env),
                    disabled=override.disabled,
                    builtin=base.builtin,
                    command_overridden=bool(override.command),
                )
                continue
            configured = LspServerSpec.from_config(server_id, override)
            if configured is not None:
                specs[server_id] = configured
        return specs

    def _first_resolved_command(self, spec: LspServerSpec) -> tuple[str, ...] | None:
        for command in spec.commands:
            resolved = _resolve_command(command)
            if resolved is not None:
                return resolved
        for command in self._managed_command_candidates(spec):
            resolved = _resolve_command(command)
            if resolved is not None:
                return resolved
        return None

    def _managed_command_candidates(self, spec: LspServerSpec) -> tuple[tuple[str, ...], ...]:
        if spec.id == "python":
            return (
                (
                    str(
                        _npm_bin_path(
                            self.install_dir / "npm" / "basedpyright",
                            "basedpyright-langserver",
                        )
                    ),
                    "--stdio",
                ),
                (
                    str(_npm_bin_path(self.install_dir / "npm" / "pyright", "pyright-langserver")),
                    "--stdio",
                ),
                (str(_binary_path(self.install_dir / "uv" / "python-pylsp", "pylsp")),),
            )
        if spec.id == "typescript":
            return (
                (
                    str(
                        _npm_bin_path(
                            self.install_dir / "npm" / "typescript-language-server",
                            "typescript-language-server",
                        )
                    ),
                    "--stdio",
                ),
            )
        if spec.id == "go":
            return ((str(_binary_path(self.install_dir / "go" / "bin", "gopls")),),)
        if spec.id == "rust":
            return ((str(_binary_path(Path.home() / ".cargo" / "bin", "rust-analyzer")),),)
        return ()

    def _build_status(self, server_id: str, spec: LspServerSpec) -> LspServerStatus:
        if spec.disabled:
            return LspServerStatus(
                id=server_id,
                name=spec.name,
                state="disabled",
                enabled=False,
                command=" ".join(spec.commands[0]) if spec.commands else "(missing)",
                extensions=list(spec.extensions),
            )

        resolved = self._resolved_commands.get(server_id)
        if resolved is None:
            candidates = [" ".join(command) for command in spec.commands]
            message = self.errors.get(server_id) or self._unavailable_message(spec, candidates)
            self.errors[server_id] = message
            return LspServerStatus(
                id=server_id,
                name=spec.name,
                state="unavailable",
                enabled=True,
                command=candidates[0] if candidates else "(missing)",
                extensions=list(spec.extensions),
                error=message,
            )

        active_clients = sum(1 for key in self._clients if key[0] == server_id)
        return LspServerStatus(
            id=server_id,
            name=spec.name,
            state="active" if active_clients else "available",
            enabled=True,
            command=" ".join(resolved),
            extensions=list(spec.extensions),
            active_clients=active_clients,
        )

    def _refresh_statuses(self) -> None:
        self.server_statuses = {
            server_id: self._build_status(server_id, spec) for server_id, spec in self.specs.items()
        }

    def _unavailable_message(self, spec: LspServerSpec, candidates: list[str]) -> str:
        message = f"no executable found locally (tried: {', '.join(candidates)})"
        if not self._can_auto_install(spec):
            return message

        installers = self._available_installers(spec)
        if installers:
            return (
                f"{message}; auto-install is enabled and will use "
                f"{' or '.join(installers)} on first use"
            )

        required = self._required_installers(spec)
        if required:
            return f"{message}; auto-install requires {' or '.join(required)}"
        return message

    def _can_auto_install(self, spec: LspServerSpec) -> bool:
        return self.auto_install and spec.builtin and not spec.command_overridden

    def _required_installers(self, spec: LspServerSpec) -> tuple[str, ...]:
        if spec.id == "python":
            return ("npm", "uv")
        if spec.id == "typescript":
            return ("npm",)
        if spec.id == "go":
            return ("go",)
        if spec.id == "rust":
            return ("rustup",)
        return ()

    def _available_installers(self, spec: LspServerSpec) -> tuple[str, ...]:
        return tuple(
            installer for installer in self._required_installers(spec) if shutil.which(installer)
        )

    def _client_status(self, client: LspClient) -> LspClientStatus:
        return LspClientStatus(
            id=client.server_id,
            name=client.server_name,
            root=client.root,
            command=" ".join(client.command),
            open_documents=client.open_documents,
        )

    async def _position_request(
        self,
        path: str,
        method: str,
        *,
        line: int,
        column: int,
    ) -> list[dict[str, Any]]:
        client = await self._single_client_for_path(path)
        await client.open_document(path)
        result = await client.send_request(
            method,
            {
                "textDocument": {"uri": _file_uri(path)},
                "position": {"line": line - 1, "character": column - 1},
            },
        )
        return _normalize_locations(result)

    async def _single_client_for_path(self, path: str) -> LspClient:
        clients = await self._clients_for_path(path)
        if not clients:
            resolved = _resolve_path(self.project_dir, path)
            suffix = Path(resolved).suffix.lower() or Path(resolved).name
            details: list[str] = []
            for spec in self.specs.values():
                if spec.disabled:
                    continue
                if spec.extensions and suffix not in spec.extensions:
                    continue
                status = self.server_statuses.get(spec.id)
                detail = self.errors.get(spec.id) or (status.error if status else "")
                if detail:
                    details.append(f"{spec.id}: {detail}")
            extra = f" Details: {'; '.join(details)}." if details else ""
            raise RuntimeError(
                f"No available LSP server for {resolved}. "
                f"Install a compatible language server for {suffix} "
                f"or configure [lsp.servers.<name>] in config.toml.{extra}"
            )
        return clients[0]

    async def _workspace_clients(self) -> list[LspClient]:
        clients: list[LspClient] = []
        for spec in self.specs.values():
            if spec.disabled:
                continue
            if not _project_has_extension(self.project_dir, spec.extensions):
                continue
            if await self._ensure_resolved_command(spec) is None:
                continue
            root = _find_workspace_root(self.project_dir, spec.root_markers)
            client = await self._get_or_spawn_client(spec, root)
            clients.append(client)
        return clients

    async def _clients_for_path(self, path: str) -> list[LspClient]:
        resolved = _resolve_path(self.project_dir, path)
        suffix = Path(resolved).suffix.lower()
        clients: list[LspClient] = []
        for spec in self.specs.values():
            if spec.disabled:
                continue
            if spec.extensions and suffix not in spec.extensions:
                continue
            if await self._ensure_resolved_command(spec) is None:
                continue
            root = _find_workspace_root(resolved, spec.root_markers)
            clients.append(await self._get_or_spawn_client(spec, root))
        return clients

    async def _get_or_spawn_client(self, spec: LspServerSpec, root: str) -> LspClient:
        key = (spec.id, root)
        existing = self._clients.get(key)
        if existing is not None:
            return existing

        inflight = self._spawning.get(key)
        if inflight is not None:
            return await inflight

        async def _spawn() -> LspClient:
            command = await self._ensure_resolved_command(spec)
            if command is None:
                raise RuntimeError(f"No executable available for LSP server {spec.id}")
            client = LspClient(
                server_id=spec.id,
                server_name=spec.name,
                root=root,
                command=command,
                initialization=spec.initialization,
                env=spec.env,
            )
            await client.start()
            self._clients[key] = client
            self._refresh_statuses()
            return client

        task = asyncio.create_task(_spawn())
        self._spawning[key] = task
        try:
            return await task
        except Exception as exc:
            self.errors[f"{spec.id}:{root}"] = str(exc)
            self._refresh_statuses()
            raise
        finally:
            if self._spawning.get(key) is task:
                self._spawning.pop(key, None)

    async def _ensure_resolved_command(self, spec: LspServerSpec) -> tuple[str, ...] | None:
        resolved = self._resolved_commands.get(spec.id)
        if resolved is not None:
            return resolved
        if not self._can_auto_install(spec):
            return None

        inflight = self._installing.get(spec.id)
        if inflight is not None:
            return await inflight

        async def _install() -> tuple[str, ...] | None:
            command = await self._auto_install_command(spec)
            self._resolved_commands[spec.id] = command
            self._refresh_statuses()
            return command

        task = asyncio.create_task(_install())
        self._installing[spec.id] = task
        try:
            return await task
        finally:
            if self._installing.get(spec.id) is task:
                self._installing.pop(spec.id, None)

    async def _auto_install_command(self, spec: LspServerSpec) -> tuple[str, ...] | None:
        try:
            if spec.id == "python":
                command = await self._install_builtin_python()
            elif spec.id == "typescript":
                command = await self._install_builtin_typescript()
            elif spec.id == "go":
                command = await self._install_builtin_go()
            elif spec.id == "rust":
                command = await self._install_builtin_rust()
            else:
                command = None
        except Exception as exc:  # noqa: BLE001
            self.errors[spec.id] = f"auto-install failed: {exc}"
            return None

        if command is None:
            self.errors[spec.id] = self._unavailable_message(
                spec,
                [" ".join(item) for item in spec.commands],
            )
            return None

        self.errors.pop(spec.id, None)
        return command

    async def _install_builtin_python(self) -> tuple[str, ...] | None:
        self.install_dir.mkdir(parents=True, exist_ok=True)
        errors: list[str] = []

        if shutil.which("npm"):
            for package_name, executable in (
                ("basedpyright", "basedpyright-langserver"),
                ("pyright", "pyright-langserver"),
            ):
                prefix = self.install_dir / "npm" / package_name
                try:
                    await self._run_process(
                        (
                            "npm",
                            "install",
                            "--silent",
                            "--no-fund",
                            "--no-audit",
                            "--prefix",
                            str(prefix),
                            package_name,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{package_name}: {exc}")
                    continue
                command = _resolve_command((str(_npm_bin_path(prefix, executable)), "--stdio"))
                if command is not None:
                    return command

        if shutil.which("uv"):
            tool_dir = self.install_dir / "uv" / "python-pylsp"
            try:
                await self._run_process(
                    (
                        "uv",
                        "tool",
                        "install",
                        "--tool-dir",
                        str(tool_dir),
                        "python-lsp-server",
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"python-lsp-server: {exc}")
            else:
                command = _resolve_command((str(_binary_path(tool_dir, "pylsp")),))
                if command is not None:
                    return command

        if errors:
            raise RuntimeError("; ".join(errors))

        return None

    async def _install_builtin_typescript(self) -> tuple[str, ...] | None:
        if not shutil.which("npm"):
            return None

        self.install_dir.mkdir(parents=True, exist_ok=True)
        prefix = self.install_dir / "npm" / "typescript-language-server"
        await self._run_process(
            (
                "npm",
                "install",
                "--silent",
                "--no-fund",
                "--no-audit",
                "--prefix",
                str(prefix),
                "typescript",
                "typescript-language-server",
            )
        )
        return _resolve_command(
            (str(_npm_bin_path(prefix, "typescript-language-server")), "--stdio")
        )

    async def _install_builtin_go(self) -> tuple[str, ...] | None:
        if not shutil.which("go"):
            return None

        bin_dir = self.install_dir / "go" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        await self._run_process(
            ("go", "install", "golang.org/x/tools/gopls@latest"),
            env={"GOBIN": str(bin_dir)},
        )
        return _resolve_command((str(_binary_path(bin_dir, "gopls")),))

    async def _install_builtin_rust(self) -> tuple[str, ...] | None:
        if not shutil.which("rustup"):
            return None

        await self._run_process(("rustup", "component", "add", "rust-analyzer"))
        resolved = _resolve_command(("rust-analyzer",))
        if resolved is not None:
            return resolved

        cargo_bin = _binary_path(Path.home() / ".cargo" / "bin", "rust-analyzer")
        return _resolve_command((str(cargo_bin),))

    async def _run_process(
        self,
        command: tuple[str, ...],
        *,
        env: dict[str, str] | None = None,
        timeout: float = 180.0,
    ) -> None:
        process_env = dict(os.environ)
        if env:
            process_env.update(env)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=process_env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"{command[0]} is not installed") from exc

        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError as exc:
            with suppress(ProcessLookupError):
                process.kill()
            with suppress(Exception):
                await process.wait()
            raise RuntimeError(f"{' '.join(command)} timed out") from exc

        if process.returncode == 0:
            return

        output = stderr.decode("utf-8", errors="replace").strip()
        if not output:
            output = stdout.decode("utf-8", errors="replace").strip()
        if not output:
            output = f"exit code {process.returncode}"
        raise RuntimeError(output)


def _normalize_locations(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        if "uri" in item and "range" in item:
            normalized.append(item)
            continue
        if "targetUri" in item:
            normalized.append(
                {
                    "uri": item.get("targetUri"),
                    "range": item.get("targetSelectionRange") or item.get("targetRange") or {},
                }
            )
    return normalized


__all__ = [
    "LspClient",
    "LspClientStatus",
    "LspRuntimeManager",
    "LspServerSpec",
    "LspServerStatus",
    "_path_from_uri",
]
