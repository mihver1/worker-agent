from pathlib import Path

ROOT_FILES = [
    Path("README.md"),
    Path("mkdocs.yml"),
    Path("docs/installation.md"),
    Path("docs/acp.md"),
    Path("install.sh"),
]


def test_docs_use_artel_acp_command_example() -> None:
    content = Path("docs/acp.md").read_text(encoding="utf-8")

    assert "```bash\nartel acp\n```" in content
    assert "```bash\nworker acp\n```" not in content


def test_artel_docs_and_installer_do_not_reference_old_repo_slug() -> None:
    allowed = {
        "registry/extensions.toml",
        "packages/worker-core/src/worker_core/config.py",
        "packages/worker-core/src/worker_core/tools/web_search.py",
        "packages/worker-core/src/worker_core/tools/web_fetch.py",
    }

    offenders: list[str] = []
    needle = "worker-agent"
    for path in ROOT_FILES:
        text = path.read_text(encoding="utf-8")
        if needle in text and path.as_posix() not in allowed:
            offenders.append(path.as_posix())

    assert not offenders, f"Found stale worker-agent references in: {offenders}"
