"""Unit tests for the documentation setup."""

from __future__ import annotations

from pathlib import Path

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _collect_nav_paths(items: list[object]) -> list[str]:
    paths: list[str] = []

    for item in items:
        if isinstance(item, dict):
            for value in item.values():
                if isinstance(value, str):
                    paths.append(value)
                elif isinstance(value, list):
                    paths.extend(_collect_nav_paths(value))

    return paths


def test_mkdocs_nav_references_existing_pages() -> None:
    repo_root = _repo_root()
    config = yaml.safe_load((repo_root / "mkdocs.yml").read_text(encoding="utf-8"))
    docs_dir = repo_root / "docs"

    assert config["site_name"] == "Artel"
    assert config["theme"]["name"] == "material"

    referenced_pages = _collect_nav_paths(config["nav"])
    assert referenced_pages, "mkdocs nav should reference at least one page"

    missing_pages = [page for page in referenced_pages if not (docs_dir / page).exists()]
    assert not missing_pages, f"Missing docs pages in mkdocs nav: {missing_pages}"

    expected_pages = {
        "index.md",
        "installation.md",
        "quickstart.md",
        "configuration.md",
        "run-modes.md",
        "acp.md",
        "providers.md",
        "extensions.md",
        "cli.md",
        "web.md",
    }
    assert expected_pages.issubset(set(referenced_pages))


def test_docs_workflow_validates_and_deploys_pages() -> None:
    workflow = (_repo_root() / ".github" / "workflows" / "docs.yml").read_text(encoding="utf-8")

    for snippet in (
        "uv sync --dev",
        "uv run pytest tests/test_docs_unit.py tests/test_docs_integration.py",
        "uv run mkdocs build --strict",
        "actions/configure-pages@v5",
        "actions/upload-pages-artifact@v3",
        "actions/deploy-pages@v4",
    ):
        assert snippet in workflow
