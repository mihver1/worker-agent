#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
#  Worker — installer
#  Usage:  curl -fsSL https://raw.githubusercontent.com/mihver1/worker-agent/main/install.sh | bash
# ─────────────────────────────────────────────────────────
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────
REPO="https://github.com/mihver1/worker-agent.git"
BRANCH="main"
INSTALL_DIR="${WORKER_INSTALL_DIR:-$HOME/.local/share/worker-agent}"
BIN_DIR="${WORKER_BIN_DIR:-$HOME/.local/bin}"
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
 __        __         _
 \ \      / /__  _ __| | _____ _ __
  \ \ /\ / / _ \| '__| |/ / _ \ '__|
   \ V  V / (_) | |  |   <  __/ |
    \_/\_/ \___/|_|  |_|\_\___|_|

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
    *) die "Unsupported OS: $OS. Worker supports Linux and macOS." ;;
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
if [[ -f "$SCRIPT_DIR/pyproject.toml" ]] && [[ -d "$SCRIPT_DIR/packages/worker-core/src/worker_core" ]]; then
    if [[ "$SCRIPT_DIR" == "$INSTALL_DIR" ]]; then
        die "WORKER_INSTALL_DIR must not point to the current source checkout."
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
    info "Cloning worker-agent into $INSTALL_DIR..."
    git clone --depth 1 --branch "$BRANCH" "$REPO" "$INSTALL_DIR" --quiet
    ok "Cloned successfully"
fi

# ── Step 6: Install dependencies ─────────────────────────
info "Installing dependencies with uv..."
(cd "$INSTALL_DIR" && uv sync --quiet)
ok "Dependencies installed"

# ── Step 7: Create wrapper script ────────────────────────
mkdir -p "$BIN_DIR"
WRAPPER="$BIN_DIR/worker"

cat > "$WRAPPER" <<WRAPPER_EOF
#!/usr/bin/env bash
# Worker — auto-generated launcher
exec uv run --project "$INSTALL_DIR" worker "\$@"
WRAPPER_EOF
chmod +x "$WRAPPER"
ok "Launcher created: $WRAPPER"

# ── Step 8: Ensure BIN_DIR is in PATH ───────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    SHELL_RC="$(detect_shell_rc)"
    EXPORT_LINE="export PATH=\"$BIN_DIR:\$PATH\""

    if [[ -f "$SHELL_RC" ]] && grep -qF "$BIN_DIR" "$SHELL_RC" 2>/dev/null; then
        ok "PATH already configured in $SHELL_RC"
    else
        info "Adding $BIN_DIR to PATH in $SHELL_RC..."
        echo "" >> "$SHELL_RC"
        echo "# Worker agent" >> "$SHELL_RC"
        echo "$EXPORT_LINE" >> "$SHELL_RC"
        ok "PATH updated in $SHELL_RC"
        warn "Run: source $SHELL_RC  (or open a new terminal)"
    fi
    export PATH="$BIN_DIR:$PATH"
fi

# ── Step 9: Run init if no config exists ─────────────────
CONFIG_FILE="$HOME/.config/worker/config.toml"
if [[ ! -f "$CONFIG_FILE" ]]; then
    info "Creating global config..."
    uv run --project "$INSTALL_DIR" python -c \
        'from worker_core.config import generate_global_config; generate_global_config()' \
        >/dev/null 2>&1 || true
    if [[ -f "$CONFIG_FILE" ]]; then
        ok "Config created: $CONFIG_FILE"
    else
        warn "Global config creation skipped. Run 'worker init' manually to configure."
    fi
else
    ok "Config already exists: $CONFIG_FILE"
fi

# ── Done ──────────────────────────────────────────────────
printf "\n${GREEN}${BOLD}Installation complete!${NC}\n\n"
echo "  Quick start:"
echo "    worker -p \"hello\"         # one-shot prompt"
echo "    worker                    # interactive TUI"
echo "    worker init               # reconfigure"
echo ""
echo "  Update:"
echo "    curl -fsSL https://raw.githubusercontent.com/mihver1/worker-agent/main/install.sh | bash"
echo ""
echo "  Uninstall:"
echo "    rm -rf $INSTALL_DIR $WRAPPER"
echo ""
