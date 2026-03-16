"""Installer regression tests."""

from __future__ import annotations

import importlib
import shutil
import sys
import tomllib
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
    assert "ARTEL_INSTALL_DIR" in install_sh
    assert "ARTEL_BIN_DIR" in install_sh
    assert "ARTEL_CONFIG_DIR" in install_sh
    assert 'exec uv run --project "$INSTALL_DIR" artel "\\$@"' in install_sh
    assert 'exec uv run --project "$INSTALL_DIR" worker "\\$@"' in install_sh
    assert 'exec uv run --directory "$INSTALL_DIR" worker "\\$@"' not in install_sh
    assert "generate_global_config" in install_sh
    assert '"$ARTEL_WRAPPER" init' not in install_sh


def test_copied_checkout_can_import_worker_tui(tmp_path, monkeypatch) -> None:
    repo_root = _repo_root()
    install_root = tmp_path / "install"
    _copy_checkout(repo_root, install_root)

    package_roots = (
        install_root / "packages/worker-ai/src",
        install_root / "packages/worker-core/src",
        install_root / "packages/worker-server/src",
        install_root / "packages/worker-tui/src",
        install_root / "packages/worker-web/src",
    )
    for path in package_roots:
        monkeypatch.syspath_prepend(str(path))

    prefixes = ("worker_ai", "worker_core", "worker_server", "worker_tui", "worker_web")
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


def test_copied_checkout_can_import_artel_module_aliases(tmp_path, monkeypatch) -> None:
    repo_root = _repo_root()
    install_root = tmp_path / "install"
    _copy_checkout(repo_root, install_root)

    package_roots = (
        install_root / "src",
        install_root / "packages/worker-ai/src",
        install_root / "packages/worker-core/src",
        install_root / "packages/worker-server/src",
        install_root / "packages/worker-tui/src",
        install_root / "packages/worker-web/src",
        install_root / "extensions/worker-ext-example/src",
    )
    for path in package_roots:
        monkeypatch.syspath_prepend(str(path))

    prefixes = (
        "artel",
        "worker",
        "artel_ai",
        "artel_core",
        "artel_server",
        "artel_tui",
        "artel_web",
        "artel_ext_example",
        "worker_ai",
        "worker_core",
        "worker_server",
        "worker_tui",
        "worker_web",
        "worker_ext_example",
    )
    saved_modules = {
        name: module
        for name, module in sys.modules.items()
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes)
    }
    for name in list(saved_modules):
        sys.modules.pop(name, None)

    try:
        assert importlib.import_module("artel_core.cli").main.__name__ == "main"
        assert importlib.import_module("artel_ai.oauth").TokenStore.__name__ == "TokenStore"
        assert (
            importlib.import_module("artel_server.provider_overlay").load_provider_overlay.__name__
            == "load_provider_overlay"
        )
        remote_control = importlib.import_module("artel_tui.remote_control")
        assert remote_control.RemoteControlClient.__name__ in {
            "RemoteControlClient",
            "RemoteWorkerControl",
            "RemoteArtelControl",
        }
        assert remote_control.remote_rest_base_url.__name__ == "remote_rest_base_url"
        assert (
            importlib.import_module("artel_web.backend_store").WebBackendEntry.__name__
            == "WebBackendEntry"
        )
        assert (
            importlib.import_module("artel_ext_example").ExampleExtension.__name__
            == "ExampleExtension"
        )
    finally:
        for name in list(sys.modules):
            if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)


def test_root_pyproject_exposes_artel_as_primary_distribution() -> None:
    with (_repo_root() / "pyproject.toml").open("rb") as file:
        data = tomllib.load(file)

    assert data["project"]["name"] == "artel"
    assert data["project"]["scripts"]["artel"] == "artel_core.cli:main"
    assert data["project"]["scripts"]["worker"] == "worker_core.cli:main"
    assert data["project"]["dependencies"] == [
        "artel-ai",
        "artel-core",
        "artel-server",
        "artel-tui",
        "artel-web",
    ]


def test_workspace_pyprojects_use_artel_distribution_names() -> None:
    expected_names = {
        "packages/worker-ai/pyproject.toml": "artel-ai",
        "packages/worker-core/pyproject.toml": "artel-core",
        "packages/worker-server/pyproject.toml": "artel-server",
        "packages/worker-tui/pyproject.toml": "artel-tui",
        "packages/worker-web/pyproject.toml": "artel-web",
    }

    for rel_path, expected_name in expected_names.items():
        with (_repo_root() / rel_path).open("rb") as file:
            data = tomllib.load(file)
        assert data["project"]["name"] == expected_name


def test_workspace_pyprojects_ship_artel_and_worker_module_packages() -> None:
    expected_packages = {
        "packages/worker-ai/pyproject.toml": ["src/artel_ai", "src/worker_ai"],
        "packages/worker-core/pyproject.toml": ["src/artel_core", "src/worker_core"],
        "packages/worker-server/pyproject.toml": ["src/artel_server", "src/worker_server"],
        "packages/worker-tui/pyproject.toml": ["src/artel_tui", "src/worker_tui"],
        "packages/worker-web/pyproject.toml": ["src/artel_web", "src/worker_web"],
        "extensions/worker-ext-example/pyproject.toml": [
            "src/artel_ext_example",
            "src/worker_ext_example",
        ],
    }

    for rel_path, expected in expected_packages.items():
        with (_repo_root() / rel_path).open("rb") as file:
            data = tomllib.load(file)
        assert data["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == expected


def test_root_meta_package_exposes_artel_with_worker_compatibility(monkeypatch) -> None:
    src_root = _repo_root() / "src"
    monkeypatch.syspath_prepend(str(src_root))

    saved_modules = {
        name: module for name, module in sys.modules.items() if name == "artel" or name == "worker"
    }
    sys.modules.pop("artel", None)
    sys.modules.pop("worker", None)

    try:
        artel = importlib.import_module("artel")
        worker = importlib.import_module("worker")
        assert artel.__doc__ == "Artel meta-package."
        assert "compatibility wrapper" in (worker.__doc__ or "")
    finally:
        sys.modules.pop("artel", None)
        sys.modules.pop("worker", None)
        sys.modules.update(saved_modules)
