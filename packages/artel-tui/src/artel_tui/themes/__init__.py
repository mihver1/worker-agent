"""Theme system — built-in + user-defined Textual CSS themes.

Built-in themes live in this package as ``.tcss`` files.
User themes are loaded from ``~/.config/artel/themes/`` and
``.artel/themes/`` (project override), with legacy Artel paths still
read as fallback during migration.

Each theme is a Textual CSS string applied via ``App.stylesheet``.
"""

from __future__ import annotations

from pathlib import Path

from artel_core.config import (
    CONFIG_DIR,
    LEGACY_CONFIG_DIR,
    legacy_project_state_dir,
    project_state_dir,
)

_THEMES_DIR = Path(__file__).parent

# ── Built-in themes (CSS strings) ────────────────────────────────

_DARK = """\
Screen {
    background: #1e1e2e;
}
Header {
    background: #313244;
    color: #cdd6f4;
}
Footer {
    background: #313244;
    color: #a6adc8;
}
.user-message {
    background: #313244;
    color: #cdd6f4;
    border-left: thick #89b4fa;
}
.assistant-message {
    background: #1e1e2e;
    color: #cdd6f4;
}
.tool-message {
    background: #1e1e2e;
    color: #6c7086;
    text-style: italic;
}
.error-message {
    background: #45243a;
    color: #f38ba8;
}
Input {
    background: #313244;
    color: #cdd6f4;
    border: tall #585b70;
}
"""

_LIGHT = """\
Screen {
    background: #eff1f5;
}
Header {
    background: #ccd0da;
    color: #4c4f69;
}
Footer {
    background: #ccd0da;
    color: #5c5f77;
}
.user-message {
    background: #ccd0da;
    color: #4c4f69;
    border-left: thick #1e66f5;
}
.assistant-message {
    background: #eff1f5;
    color: #4c4f69;
}
.tool-message {
    background: #eff1f5;
    color: #7c7f93;
    text-style: italic;
}
.error-message {
    background: #fce4e8;
    color: #d20f39;
}
Input {
    background: #ccd0da;
    color: #4c4f69;
    border: tall #9ca0b0;
}
"""

_MONOKAI = """\
Screen {
    background: #272822;
}
Header {
    background: #3e3d32;
    color: #f8f8f2;
}
Footer {
    background: #3e3d32;
    color: #75715e;
}
.user-message {
    background: #3e3d32;
    color: #f8f8f2;
    border-left: thick #a6e22e;
}
.assistant-message {
    background: #272822;
    color: #f8f8f2;
}
.tool-message {
    background: #272822;
    color: #75715e;
    text-style: italic;
}
.error-message {
    background: #4a1c2c;
    color: #f92672;
}
Input {
    background: #3e3d32;
    color: #f8f8f2;
    border: tall #75715e;
}
"""

_DRACULA = """\
Screen {
    background: #282a36;
}
Header {
    background: #44475a;
    color: #f8f8f2;
}
Footer {
    background: #44475a;
    color: #6272a4;
}
.user-message {
    background: #44475a;
    color: #f8f8f2;
    border-left: thick #bd93f9;
}
.assistant-message {
    background: #282a36;
    color: #f8f8f2;
}
.tool-message {
    background: #282a36;
    color: #6272a4;
    text-style: italic;
}
.error-message {
    background: #3d1f34;
    color: #ff5555;
}
Input {
    background: #44475a;
    color: #f8f8f2;
    border: tall #6272a4;
}
"""

BUILTIN_THEMES: dict[str, str] = {
    "dark": _DARK,
    "light": _LIGHT,
    "monokai": _MONOKAI,
    "dracula": _DRACULA,
}


# ── User themes loading ──────────────────────────────────────────


def _user_themes_dirs(project_dir: str = "") -> list[Path]:
    dirs = [LEGACY_CONFIG_DIR / "themes", CONFIG_DIR / "themes"]
    if project_dir:
        dirs.extend(
            [
                legacy_project_state_dir(project_dir) / "themes",
                project_state_dir(project_dir) / "themes",
            ]
        )
    return list(dict.fromkeys(dirs))


def load_themes(project_dir: str = "") -> dict[str, str]:
    """Return all available themes (built-in + user).

    User themes override built-in ones with the same name.
    """
    themes = dict(BUILTIN_THEMES)
    for d in _user_themes_dirs(project_dir):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.tcss")):
            try:
                themes[f.stem] = f.read_text(encoding="utf-8")
            except OSError:
                continue
    return themes


def list_themes(project_dir: str = "") -> list[str]:
    """Return sorted list of available theme names."""
    return sorted(load_themes(project_dir).keys())
