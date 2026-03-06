"""Installer regression tests."""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _copy_checkout(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            ".warp",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
        ),
    )


def test_install_script_uses_project_mode_and_global_config_bootstrap() -> None:
    install_sh = (_repo_root() / "install.sh").read_text(encoding="utf-8")
    assert 'exec uv run --project "$INSTALL_DIR" worker "\\$@"' in install_sh
    assert 'exec uv run --directory "$INSTALL_DIR" worker "\\$@"' not in install_sh
    assert "generate_global_config" in install_sh
    assert '"$WRAPPER" init' not in install_sh


def test_copied_checkout_can_import_worker_tui(tmp_path, monkeypatch) -> None:
    repo_root = _repo_root()
    install_root = tmp_path / "install"
    _copy_checkout(repo_root, install_root)

    package_roots = (
        install_root / "packages/worker-ai/src",
        install_root / "packages/worker-core/src",
        install_root / "packages/worker-server/src",
        install_root / "packages/worker-tui/src",
    )
    for path in package_roots:
        monkeypatch.syspath_prepend(str(path))

    prefixes = ("worker_ai", "worker_core", "worker_server", "worker_tui")
    saved_modules = {
        name: module
        for name, module in sys.modules.items()
        if name in prefixes or name.startswith(prefixes)
    }
    for name in list(saved_modules):
        sys.modules.pop(name, None)

    try:
        module = importlib.import_module("worker_tui.app")
        assert module.WorkerApp.__name__ == "WorkerApp"
    finally:
        for name in list(sys.modules):
            if name in prefixes or name.startswith(prefixes):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
