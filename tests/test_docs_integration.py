"""Integration tests for the documentation build."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_mkdocs_build_strict(tmp_path) -> None:
    site_dir = tmp_path / "site"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mkdocs",
            "build",
            "--strict",
            "--site-dir",
            str(site_dir),
        ],
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        "mkdocs build --strict failed\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert (site_dir / "index.html").exists()
    assert (site_dir / "acp" / "index.html").exists()
    assert (site_dir / "configuration" / "index.html").exists()
    assert (site_dir / "cli" / "index.html").exists()
