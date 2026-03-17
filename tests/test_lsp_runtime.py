from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

_FAKE_LSP_SERVER = """#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname


def path_from_uri(uri: str) -> str:
    parts = urlsplit(uri)
    path = url2pathname(unquote(parts.path))
    if parts.netloc and parts.netloc != "localhost":
        return f"//{parts.netloc}{path}"
    return path


def file_uri(path: str) -> str:
    return Path(path).resolve().as_uri()


def send(payload: dict) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def read_message() -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in {b"\\r\\n", b"\\n"}:
            break
        decoded = line.decode("utf-8", errors="replace").strip()
        key, _, value = decoded.partition(":")
        if key and value:
            headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0") or 0)
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8", errors="replace"))


def loc(path: str, line: int, character: int) -> dict:
    return {
        "uri": file_uri(path),
        "range": {
            "start": {"line": line, "character": character},
            "end": {"line": line, "character": character + 6},
        },
    }


def publish_diagnostics(path: str) -> None:
    send(
        {
            "jsonrpc": "2.0",
            "method": "textDocument/publishDiagnostics",
            "params": {
                "uri": file_uri(path),
                "diagnostics": [
                    {
                        "range": {
                            "start": {"line": 1, "character": 0},
                            "end": {"line": 1, "character": 5},
                        },
                        "severity": 2,
                        "message": "Fake warning from the test LSP server",
                    }
                ],
            },
        }
    )


while True:
    message = read_message()
    if message is None:
        break

    method = message.get("method")
    params = message.get("params", {})
    request_id = message.get("id")

    if method == "initialize":
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "capabilities": {
                        "hoverProvider": True,
                        "definitionProvider": True,
                        "referencesProvider": True,
                        "implementationProvider": True,
                        "documentSymbolProvider": True,
                        "workspaceSymbolProvider": True,
                        "textDocumentSync": 1,
                    }
                },
            }
        )
        continue

    if method == "initialized":
        send({"jsonrpc": "2.0", "id": 9001, "method": "workspace/workspaceFolders", "params": {}})
        send(
            {
                "jsonrpc": "2.0",
                "id": 9002,
                "method": "client/registerCapability",
                "params": {"registrations": []},
            }
        )
        send(
            {
                "jsonrpc": "2.0",
                "id": 9003,
                "method": "client/unregisterCapability",
                "params": {"unregisterations": []},
            }
        )
        continue

    if method in {"textDocument/didOpen", "textDocument/didChange"}:
        doc = params.get("textDocument", {})
        path = path_from_uri(doc.get("uri", ""))
        publish_diagnostics(path)
        continue

    if method == "textDocument/hover":
        path = path_from_uri(params["textDocument"]["uri"])
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "contents": {
                        "kind": "markdown",
                        "value": (
                            "```python\\nhelper() -> int\\n```"
                            "\\n\\nFake helper documentation."
                        ),
                    }
                },
            }
        )
        continue

    if method in {"textDocument/definition", "textDocument/implementation"}:
        path = path_from_uri(params["textDocument"]["uri"])
        helper_path = str(Path(path).with_name("helpers.py"))
        send({"jsonrpc": "2.0", "id": request_id, "result": [loc(helper_path, 0, 4)]})
        continue

    if method == "textDocument/references":
        path = path_from_uri(params["textDocument"]["uri"])
        helper_path = str(Path(path).with_name("helpers.py"))
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": [
                    loc(helper_path, 0, 4),
                    loc(path, 0, 20),
                    loc(path, 1, 8),
                ],
            }
        )
        continue

    if method == "textDocument/documentSymbol":
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": [
                    {
                        "name": "value",
                        "kind": 13,
                        "range": {
                            "start": {"line": 1, "character": 0},
                            "end": {"line": 1, "character": 15},
                        },
                        "selectionRange": {
                            "start": {"line": 1, "character": 0},
                            "end": {"line": 1, "character": 5},
                        },
                    }
                ],
            }
        )
        continue

    if method == "workspace/symbol":
        app_path = path_from_uri(Path.cwd().joinpath("app.py").resolve().as_uri())
        helper_path = str(Path(app_path).with_name("helpers.py"))
        send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": [
                    {
                        "name": "helper",
                        "kind": 12,
                        "location": loc(helper_path, 0, 4),
                    }
                ],
            }
        )
        continue

    if method == "shutdown":
        send({"jsonrpc": "2.0", "id": request_id, "result": None})
        continue

    if method == "exit":
        break

    if request_id is not None:
        send({"jsonrpc": "2.0", "id": request_id, "result": None})
"""


def _write_fake_server(path: Path) -> Path:
    path.write_text(textwrap.dedent(_FAKE_LSP_SERVER), encoding="utf-8")
    return path


def _make_executable(path: Path) -> Path:
    path.chmod(path.stat().st_mode | 0o111)
    return path


def _write_fake_npm(path: Path, *, server_path: Path, log_path: Path) -> Path:
    script = f"""#!/bin/sh
set -eu

prefix=""
packages=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    install|--silent|--no-fund|--no-audit)
      shift
      ;;
    --prefix)
      prefix="$2"
      shift 2
      ;;
    *)
      packages="$packages $1"
      shift
      ;;
  esac
done

/bin/mkdir -p "$prefix/node_modules/.bin"
/bin/mkdir -p "$prefix/node_modules/typescript/lib"

for package in $packages; do
  printf '%s\\n' "$package" >> "{log_path}"
  case "$package" in
    basedpyright)
      /bin/cat > "$prefix/node_modules/.bin/basedpyright-langserver" <<'EOF'
#!/bin/sh
exec "{sys.executable}" "{server_path}" "$@"
EOF
      /bin/chmod +x "$prefix/node_modules/.bin/basedpyright-langserver"
      ;;
    pyright)
      /bin/cat > "$prefix/node_modules/.bin/pyright-langserver" <<'EOF'
#!/bin/sh
exec "{sys.executable}" "{server_path}" "$@"
EOF
      /bin/chmod +x "$prefix/node_modules/.bin/pyright-langserver"
      ;;
    typescript-language-server)
      /bin/cat > "$prefix/node_modules/.bin/typescript-language-server" <<'EOF'
#!/bin/sh
exec "{sys.executable}" "{server_path}" "$@"
EOF
      /bin/chmod +x "$prefix/node_modules/.bin/typescript-language-server"
      ;;
    typescript)
      : > "$prefix/node_modules/typescript/lib/tsserver.js"
      ;;
  esac
done
"""
    path.write_text(textwrap.dedent(script), encoding="utf-8")
    return _make_executable(path)


@pytest.mark.asyncio
async def test_lsp_tools_use_fake_server_and_render_semantic_results(tmp_path: Path) -> None:
    from artel_core.config import ArtelConfig, LspConfig, LspServerConfig
    from artel_core.extensions import ExtensionContext
    from artel_core.lsp_runtime import LspRuntimeManager

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "app.py").write_text("from helpers import helper\nvalue = helper()\n", encoding="utf-8")
    (repo / "helpers.py").write_text("def helper():\n    return 42\n", encoding="utf-8")

    server_path = _write_fake_server(tmp_path / "fake_lsp_server.py")
    config = ArtelConfig(
        lsp=LspConfig(
            servers={
                "python": LspServerConfig(
                    command=[sys.executable, "-u", str(server_path)],
                    extensions=[".py"],
                    root_markers=[".git"],
                )
            }
        )
    )

    runtime = LspRuntimeManager()
    await runtime.load(ExtensionContext(project_dir=str(repo), runtime="local", config=config))
    try:
        tools = {tool.name: tool for tool in runtime.tools}
        assert {
            "lsp_hover",
            "lsp_definition",
            "lsp_references",
            "lsp_implementation",
            "lsp_document_symbols",
            "lsp_workspace_symbols",
            "lsp_diagnostics",
        }.issubset(tools)

        hover = await tools["lsp_hover"].execute(path="app.py", line=2, column=9)
        definition = await tools["lsp_definition"].execute(
            path="app.py",
            line=2,
            column=9,
            max_results=5,
        )
        references = await tools["lsp_references"].execute(
            path="app.py",
            line=2,
            column=9,
            max_results=5,
        )
        implementation = await tools["lsp_implementation"].execute(
            path="app.py",
            line=2,
            column=9,
        )
        document_symbols = await tools["lsp_document_symbols"].execute(path="app.py")
        workspace_symbols = await tools["lsp_workspace_symbols"].execute(query="helper")
        diagnostics = await tools["lsp_diagnostics"].execute(path="app.py")

        assert "helper() -> int" in hover
        assert "Fake helper documentation" in hover
        assert "Definitions for" in definition
        assert "helpers.py:1:5" in definition
        assert "def helper():" in definition
        assert "References for" in references
        assert "app.py:2:9" in references
        assert "value = helper()" in references
        assert "Implementations for" in implementation
        assert "helpers.py:1:5" in implementation
        assert "Document symbols for" in document_symbols
        assert "Variable value [2:1]" in document_symbols
        assert "Workspace symbols" in workspace_symbols
        assert "Function helper" in workspace_symbols
        assert "helpers.py:1:5" in workspace_symbols
        assert "Diagnostics for" in diagnostics
        assert "Fake warning from the test LSP server" in diagnostics

        payload = runtime.status_payload()
        assert payload["summary"]["clients"] == 1
        assert any(item["state"] == "active" for item in payload["servers"])
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_lsp_runtime_auto_installs_builtin_server_on_first_use(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from artel_core.config import ArtelConfig, LspConfig
    from artel_core.extensions import ExtensionContext
    from artel_core.lsp_runtime import LspRuntimeManager

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "app.py").write_text("from helpers import helper\nvalue = helper()\n", encoding="utf-8")
    (repo / "helpers.py").write_text("def helper():\n    return 42\n", encoding="utf-8")

    fake_server = _write_fake_server(tmp_path / "fake_lsp_server.py")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    install_log = tmp_path / "npm-install.log"
    _write_fake_npm(bin_dir / "npm", server_path=fake_server, log_path=install_log)
    monkeypatch.setenv("PATH", str(bin_dir))
    config = ArtelConfig(
        lsp=LspConfig(
            enabled=True,
            auto_install=True,
            install_dir=str(tmp_path / "lsp-cache"),
        )
    )

    runtime = LspRuntimeManager()
    await runtime.load(
        ExtensionContext(
            project_dir=str(repo),
            runtime="local",
            config=config,
        )
    )
    try:
        tools = {tool.name: tool for tool in runtime.tools}
        assert "lsp_definition" in tools

        initial_payload = runtime.status_payload()
        python_status = next(item for item in initial_payload["servers"] if item["id"] == "python")
        assert python_status["state"] == "unavailable"
        assert "auto-install is enabled" in python_status["error"]

        definition = await tools["lsp_definition"].execute(path="app.py", line=2, column=9)

        assert "Definitions for" in definition
        assert "helpers.py:1:5" in definition
        assert install_log.read_text(encoding="utf-8").splitlines() == ["basedpyright"]

        final_payload = runtime.status_payload()
        python_status = next(item for item in final_payload["servers"] if item["id"] == "python")
        assert python_status["state"] == "active"
        assert final_payload["summary"]["clients"] == 1
    finally:
        await runtime.close()

    runtime = LspRuntimeManager()
    await runtime.load(ExtensionContext(project_dir=str(repo), runtime="local", config=config))
    try:
        tools = {tool.name: tool for tool in runtime.tools}
        payload = runtime.status_payload()
        python_status = next(item for item in payload["servers"] if item["id"] == "python")
        assert python_status["state"] == "available"

        definition = await tools["lsp_definition"].execute(path="app.py", line=2, column=9)

        assert "helpers.py:1:5" in definition
        assert install_log.read_text(encoding="utf-8").splitlines() == ["basedpyright"]
    finally:
        await runtime.close()


def test_lsp_status_command_renders_runtime_status(monkeypatch, tmp_path: Path) -> None:
    from artel_core import cli as cli_mod
    from artel_core.config import ArtelConfig

    class _Runtime:
        async def load(self, context):
            self.context = context

        def status_text(self) -> str:
            return "LSP servers:\n- python [available] command=/fake/pyright --stdio"

        def status_payload(self) -> dict[str, object]:
            return {"summary": {"available": 1}}

        async def close(self) -> None:
            return None

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("artel_core.cli.load_config", lambda project_dir=None: ArtelConfig())
    monkeypatch.setattr(
        "artel_core.artel_bootstrap.bootstrap_artel",
        lambda *args, **kwargs: type("Bootstrap", (), {"project_dir": str(tmp_path)})(),
    )
    monkeypatch.setattr("artel_core.lsp_runtime.LspRuntimeManager", _Runtime)

    runner = CliRunner()
    result = runner.invoke(cli_mod.cli, ["lsp", "status"])

    assert result.exit_code == 0
    assert "python [available]" in result.output
