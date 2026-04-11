#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 🍪 Cookie-Fooocus — SERVER MODE Installer
# Provided by CookieHostUK · Coded with Claude AI assistance
#
# Installs Cookie-Fooocus for multi-user server deployment.
# Enables: PBKDF2 authentication · role-based access · session tokens
#          per-user rate limiting · audit logging
#
# After install:
#   1. Edit auth.json with your user accounts (see auth.json.example)
#   2. Run: bash run_server.sh
# ─────────────────────────────────────────────────────────────────────────────

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; CYAN='\033[0;36m'; RESET='\033[0m'

banner() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}║   🍪  Cookie-Fooocus — SERVER MODE Installer         ║${RESET}"
    echo -e "${CYAN}║   Multi-user · PBKDF2 auth · Role-based access       ║${RESET}"
    echo -e "${CYAN}║   Provided by CookieHostUK                           ║${RESET}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${RESET}"
    echo ""
    echo -e "  This installs Cookie-Fooocus for ${BOLD}multi-user server use${RESET}."
    echo -e "  ${GREEN}✓${RESET}  PBKDF2-HMAC-SHA256 authentication (600k iterations)"
    echo -e "  ${GREEN}✓${RESET}  Admin / User role system"
    echo -e "  ${GREEN}✓${RESET}  Session tokens (1-hour TTL)"
    echo -e "  ${GREEN}✓${RESET}  Per-user rate limiting"
    echo -e "  ${GREEN}✓${RESET}  Full audit logging"
    echo -e "  ${GREEN}✓${RESET}  Safety filter always active"
    echo ""
    echo -e "  ${YELLOW}⚠️  Default credentials:  admin / changeme123${RESET}"
    echo -e "  ${YELLOW}   CHANGE BEFORE EXPOSING TO THE INTERNET${RESET}"
    echo ""
}

info()    { echo -e "${GREEN}[✓]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $*"; }
error()   { echo -e "${RED}[✗]${RESET} $*"; }
section() { echo -e "\n${BOLD}── $* ──────────────────────────────────────────────${RESET}"; }

check_python() {
    section "Checking Python"
    if ! command -v python3 &>/dev/null; then
        error "Python 3 not found. Install Python 3.10+ from https://python.org"
        exit 1
    fi
    PV=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PM=$(echo "$PV" | cut -d. -f1); Pm=$(echo "$PV" | cut -d. -f2)
    if [[ "$PM" -lt 3 ]] || [[ "$PM" -eq 3 && "$Pm" -lt 10 ]]; then
        error "Python 3.10+ required. Found: $PV"; exit 1
    fi
    info "Python $PV found."
}

setup_venv() {
    section "Creating virtual environment"
    VENV="$REPO_DIR/fooocus_env"
    if [[ -d "$VENV" ]]; then
        warn "Environment already exists — skipping creation."
    else
        python3 -m venv "$VENV"
        info "Environment created."
    fi
    source "$VENV/bin/activate"
    PIP="$VENV/bin/pip"
}

install_deps() {
    section "Installing dependencies"
    "$PIP" install --upgrade pip --quiet
    "$PIP" install -r "$REPO_DIR/requirements_versions.txt" --quiet
    "$PIP" install rapidfuzz transformers pillow psutil --quiet || \
        warn "Optional extras partially unavailable — core features still work."
    info "Dependencies installed."
}

setup_auth() {
    section "Setting up authentication"
    AUTH_FILE="$REPO_DIR/auth.json"
    EXAMPLE_FILE="$REPO_DIR/auth.json.example"

    if [[ -f "$AUTH_FILE" ]]; then
        info "auth.json already exists — skipping."
    elif [[ -f "$EXAMPLE_FILE" ]]; then
        cp "$EXAMPLE_FILE" "$AUTH_FILE"
        warn "Created auth.json from example."
        warn "EDIT auth.json NOW and change the default passwords before starting."
    else
        # Create minimal auth.json with default credentials
        cat > "$AUTH_FILE" << 'AUTHJSON'
[
  {
    "user": "admin",
    "pass": "changeme123",
    "role": "admin"
  }
]
AUTHJSON
        warn "Created auth.json with DEFAULT credentials (admin / changeme123)."
        warn "CHANGE THE PASSWORD before starting the server."
    fi
}

create_server_run_script() {
    section "Creating server run script"
    cat > "$REPO_DIR/run_server.sh" << 'RUNSERVER'
#!/usr/bin/env bash
# 🍪 Cookie-Fooocus — Server Mode Run Script
# Provided by CookieHostUK · Coded with Claude AI assistance

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_DIR/fooocus_env"
PYTHON="$VENV/bin/python"

if [[ ! -f "$PYTHON" ]]; then
    echo "❌  Virtual environment not found. Run: bash install_server.sh"
    exit 1
fi

if [[ ! -f "$REPO_DIR/auth.json" ]]; then
    echo "⚠️  No auth.json found — using default credentials (admin / changeme123)"
    echo "    Create auth.json before exposing this to the internet!"
    echo ""
fi

echo "🍪  Cookie-Fooocus — Server Mode"
echo "    Auth:   enabled (PBKDF2)"
echo "    Roles:  admin / user"
echo "    Listen: 0.0.0.0 (all interfaces)"
echo ""

source "$VENV/bin/activate"
"$PYTHON" "$REPO_DIR/entry_with_update.py" --server --listen "$@"
RUNSERVER
    chmod +x "$REPO_DIR/run_server.sh"
    info "Created run_server.sh"
}

make_scripts() {
    section "Configuring run scripts"
    for s in run.sh run_realistic.sh run_anime.sh run_server.sh \
              install_local.sh install_server.sh; do
        [[ -f "$REPO_DIR/$s" ]] && chmod +x "$REPO_DIR/$s" && info "Executable: $s"
    done
}

fix_quarantine() {
    [[ "$(uname -s)" == "Darwin" ]] && \
        xattr -rd com.apple.quarantine "$REPO_DIR" 2>/dev/null && \
        info "macOS quarantine cleared." || true
}

print_done() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}║   ✅  Server Mode installation complete!             ║${RESET}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${RESET}"
    echo ""
    echo -e "  ${BOLD}Next steps:${RESET}"
    echo -e "  1. ${YELLOW}Edit auth.json${RESET} — change the default admin password"
    echo -e "     Format: see auth.json.example"
    echo ""
    echo -e "  2. ${BOLD}Start the server:${RESET}"
    echo -e "     ${GREEN}bash run_server.sh${RESET}"
    echo ""
    echo -e "  ${BOLD}Other run options:${RESET}"
    echo -e "    ${GREEN}bash run.sh${RESET}            Local mode (no auth)"
    echo -e "    ${GREEN}bash run_realistic.sh${RESET}  Realistic preset (local)"
    echo -e "    ${GREEN}bash run_anime.sh${RESET}      Anime preset (local)"
    echo ""
    echo -e "  ${RED}⚠️  Default credentials: admin / changeme123${RESET}"
    echo -e "  ${RED}   DO NOT use these in production without changing them.${RESET}"
    echo ""
}

banner
check_python
setup_venv
install_deps
setup_auth
create_server_run_script
make_scripts
fix_quarantine
print_done
