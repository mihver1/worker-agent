#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
#  Artel — installer
#  Usage:  curl -fsSL https://raw.githubusercontent.com/mihver1/artel/main/install.sh | bash
# ─────────────────────────────────────────────────────────
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────
REPO="https://github.com/mihver1/artel.git"
BRANCH="main"
INSTALL_DIR="${ARTEL_INSTALL_DIR:-${ARTEL_INSTALL_DIR:-$HOME/.local/share/artel-agent}}"
BIN_DIR="${ARTEL_BIN_DIR:-${ARTEL_BIN_DIR:-$HOME/.local/bin}}"
CONFIG_DIR="${ARTEL_CONFIG_DIR:-${ARTEL_CONFIG_DIR:-$HOME/.config/artel}}"
MIN_PYTHON="3.12"

# ── Colors ────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { printf "${CYAN}▸${NC} %s\n" "$*"; }
ok()    { printf "${GREEN}✔${NC} %s\n" "$*"; }
warn()  { printf "${YELLOW}⚠${NC} %s\n" "$*"; }
err()   { printf "${RED}✖${NC} %s\n" "$*" >&2; }
die()   { err "$@"; exit 1; }

# ── Helpers ───────────────────────────────────────────────
command_exists() { command -v "$1" &>/dev/null; }
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

version_ge() {
    # Returns 0 if $1 >= $2 (semantic version comparison)
    printf '%s\n%s' "$2" "$1" | sort -t. -k1,1n -k2,2n -k3,3n -C
}

detect_shell_rc() {
    case "${SHELL:-/bin/bash}" in
        */zsh)  echo "$HOME/.zshrc"  ;;
        */bash)
            if [[ -f "$HOME/.bash_profile" ]]; then
                echo "$HOME/.bash_profile"
            else
                echo "$HOME/.bashrc"
            fi
            ;;
        */fish) echo "$HOME/.config/fish/config.fish" ;;
        *)      echo "$HOME/.profile" ;;
    esac
}

# ── Banner ────────────────────────────────────────────────
printf "\n${BOLD}${CYAN}"
cat <<'EOF'
    _         _       _
   / \   _ __| |_ ___| |
  / _ \ | '__| __/ _ \ |
 / ___ \| |  | ||  __/ |
/_/   \_\_|   \__\___|_|

EOF
printf "${NC}"
echo "  Extensible Python coding agent"
echo ""

# ── Step 1: Check OS ─────────────────────────────────────
info "Checking environment..."
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
    Linux|Darwin) ;;
    *) die "Unsupported OS: $OS. Artel supports Linux and macOS." ;;
esac
ok "OS: $OS ($ARCH)"

# ── Step 2: Check / install git ──────────────────────────
if ! command_exists git; then
    die "git is required but not found. Install it first:\n  macOS:  xcode-select --install\n  Linux:  sudo apt install git  (or your package manager)"
fi
ok "git: $(git --version | head -1)"

# ── Step 3: Check / install uv ──────────────────────────
if command_exists uv; then
    ok "uv: $(uv --version)"
else
    info "Installing uv (Python package manager)..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Source the env so uv is available in the current session
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if command_exists uv; then
        ok "uv installed: $(uv --version)"
    else
        die "Failed to install uv. Install manually: https://docs.astral.sh/uv/getting-started/installation/"
    fi
fi

# ── Step 4: Check Python >= 3.12 ────────────────────────
PYTHON_VERSION=""
if command_exists python3; then
    PYTHON_VERSION="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
fi

if [[ -n "$PYTHON_VERSION" ]] && version_ge "$PYTHON_VERSION" "$MIN_PYTHON"; then
    ok "Python: $PYTHON_VERSION"
else
    info "Python >= $MIN_PYTHON not found, installing via uv..."
    uv python install "$MIN_PYTHON"
    ok "Python $MIN_PYTHON installed via uv"
fi

# ── Step 5: Materialize repository ───────────────────────
if [[ -f "$SCRIPT_DIR/pyproject.toml" ]] && [[ -d "$SCRIPT_DIR/packages/artel-core/src/artel_core" ]]; then
    if [[ "$SCRIPT_DIR" == "$INSTALL_DIR" ]]; then
        die "ARTEL_INSTALL_DIR must not point to the current source checkout."
    fi
    info "Installing from local checkout in $SCRIPT_DIR..."
    rm -rf "$INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    tar \
        -C "$SCRIPT_DIR" \
        --exclude .git \
        --exclude .venv \
        --exclude .warp \
        --exclude __pycache__ \
        --exclude .pytest_cache \
        --exclude .mypy_cache \
        -cf - . | tar -C "$INSTALL_DIR" -xf -
    ok "Copied local checkout into $INSTALL_DIR"
elif [[ -d "$INSTALL_DIR/.git" ]]; then
    info "Updating existing installation in $INSTALL_DIR..."
    git -C "$INSTALL_DIR" fetch origin "$BRANCH" --quiet
    git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH" --quiet
    ok "Updated to latest $BRANCH"
else
    if [[ -d "$INSTALL_DIR" ]]; then
        warn "Directory $INSTALL_DIR exists but is not a git repo, removing..."
        rm -rf "$INSTALL_DIR"
    fi
    info "Cloning Artel repository into $INSTALL_DIR..."
    git clone --depth 1 --branch "$BRANCH" "$REPO" "$INSTALL_DIR" --quiet
    ok "Cloned successfully"
fi

# ── Step 6: Install dependencies ─────────────────────────
info "Installing dependencies with uv..."
(cd "$INSTALL_DIR" && uv sync --quiet)
ok "Dependencies installed"

# ── Step 6b: Restore extensions from manifest ────────────
EXT_MANIFEST="$CONFIG_DIR/extensions.lock"
if [[ -f "$EXT_MANIFEST" ]]; then
    # Read sources from the JSON manifest [{"name": "...", "source": "..."}, ...]
    EXT_SOURCES=$(
        (cd "$INSTALL_DIR" && uv run python -c "
import json, sys
try:
    entries = json.load(open('$EXT_MANIFEST'))
    for e in entries:
        src = e.get('source', '')
        if src:
            print(src)
except Exception:
    pass
" 2>/dev/null) || true
    )
    if [[ -n "$EXT_SOURCES" ]]; then
        EXT_COUNT=$(echo "$EXT_SOURCES" | wc -l | tr -d ' ')
        info "Restoring $EXT_COUNT extension(s)..."
        while IFS= read -r ext_source; do
            [[ -z "$ext_source" ]] && continue
            result=$(cd "$INSTALL_DIR" && uv pip install --no-sources "$ext_source" --quiet 2>&1) && \
                status="✓" || status="✗"
            # Extract short name for display
            ext_short=$(echo "$ext_source" | sed 's|.*/||; s|\.git$||; s|@.*||')
            echo "  $status $ext_short"
        done <<< "$EXT_SOURCES"
        ok "Extensions restored"
    fi
fi

# ── Step 7: Create wrapper scripts ───────────────────────
mkdir -p "$BIN_DIR"
ARTEL_WRAPPER="$BIN_DIR/artel"

cat > "$ARTEL_WRAPPER" <<WRAPPER_EOF
#!/usr/bin/env bash
# Artel — auto-generated launcher
exec uv run --project "$INSTALL_DIR" artel "\$@"
WRAPPER_EOF
chmod +x "$ARTEL_WRAPPER"
ok "Launcher created: $ARTEL_WRAPPER"

# ── Step 8: Ensure BIN_DIR is in PATH ───────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    SHELL_RC="$(detect_shell_rc)"
    EXPORT_LINE="export PATH=\"$BIN_DIR:\$PATH\""

    if [[ -f "$SHELL_RC" ]] && grep -qF "$BIN_DIR" "$SHELL_RC" 2>/dev/null; then
        ok "PATH already configured in $SHELL_RC"
    else
        info "Adding $BIN_DIR to PATH in $SHELL_RC..."
        echo "" >> "$SHELL_RC"
        echo "# Artel agent" >> "$SHELL_RC"
        echo "$EXPORT_LINE" >> "$SHELL_RC"
        ok "PATH updated in $SHELL_RC"
        warn "Run: source $SHELL_RC  (or open a new terminal)"
    fi
    export PATH="$BIN_DIR:$PATH"
fi

# ── Step 9: Run init if no config exists ─────────────────
CONFIG_FILE="$CONFIG_DIR/config.toml"
if [[ ! -f "$CONFIG_FILE" ]]; then
    info "Creating global config..."
    uv run --project "$INSTALL_DIR" python -c \
        'from artel_core.config import generate_global_config; generate_global_config()' \
        >/dev/null 2>&1 || true
    if [[ -f "$CONFIG_FILE" ]]; then
        ok "Config created: $CONFIG_FILE"
    else
        warn "Global config creation skipped. Run 'artel init' manually to configure."
    fi
else
    ok "Config already exists: $CONFIG_FILE"
fi

# ── Done ──────────────────────────────────────────────────
printf "\n${GREEN}${BOLD}Installation complete!${NC}\n\n"
echo "  Quick start:"
echo "    artel -p \"hello\"         # one-shot prompt"
echo "    artel                     # interactive TUI"
echo "    artel init                # reconfigure"
echo ""
echo "  Update:"
echo "    curl -fsSL https://raw.githubusercontent.com/mihver1/artel/main/install.sh | bash"
echo ""
echo "  Uninstall:"
echo "    rm -rf $INSTALL_DIR $ARTEL_WRAPPER"
echo ""
