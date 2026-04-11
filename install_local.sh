#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 🍪 Cookie-Fooocus — LOCAL MODE Installer
# Provided by CookieHostUK · Coded with Claude AI assistance
#
# Installs Cookie-Fooocus for single-user personal use.
# No authentication, no passwords — identical behaviour to upstream Fooocus.
#
# After install, use:
#   bash run.sh              standard mode
#   bash run_realistic.sh    realistic photo preset
#   bash run_anime.sh        anime / illustration preset
# ─────────────────────────────────────────────────────────────────────────────

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; CYAN='\033[0;36m'; RESET='\033[0m'

banner() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}║   🍪  Cookie-Fooocus — LOCAL MODE Installer          ║${RESET}"
    echo -e "${CYAN}║   Single-user · No passwords · No auth               ║${RESET}"
    echo -e "${CYAN}║   Provided by CookieHostUK                           ║${RESET}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${RESET}"
    echo ""
    echo -e "  This installer sets up Cookie-Fooocus for ${BOLD}personal local use${RESET}."
    echo -e "  ${GREEN}✓${RESET}  No login system"
    echo -e "  ${GREEN}✓${RESET}  No passwords"
    echo -e "  ${GREEN}✓${RESET}  Identical to upstream Fooocus behaviour"
    echo -e "  ${GREEN}✓${RESET}  Safety filter always active"
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

make_scripts() {
    section "Configuring run scripts"
    for s in run.sh run_realistic.sh run_anime.sh install_local.sh install_server.sh; do
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
    echo -e "${CYAN}║   ✅  Local Mode installation complete!              ║${RESET}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${RESET}"
    echo ""
    echo -e "  ${BOLD}To start:${RESET}"
    echo -e "    ${GREEN}bash run.sh${RESET}            Standard (auto hardware)"
    echo -e "    ${GREEN}bash run_realistic.sh${RESET}  Realistic photo preset"
    echo -e "    ${GREEN}bash run_anime.sh${RESET}      Anime / illustration preset"
    echo ""
    echo -e "  ${BOLD}Note:${RESET} The first run will ask you to choose a hardware mode."
    if [[ "$(uname -m)" == "arm64" && "$(uname -s)" == "Darwin" ]]; then
        echo -e "  ${YELLOW}Apple Silicon detected${RESET} — choose mode ${CYAN}6${RESET} for best performance."
    fi
    echo ""
}

banner
check_python
setup_venv
install_deps
make_scripts
fix_quarantine
print_done
