#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 🍪 Cookie-Fooocus — Server Mode
# Provided by CookieHostUK · Coded with Claude AI assistance
#
# Multi-user server with PBKDF2 auth, roles, session tokens, audit logs.
# Select presets (Realistic, Anime, etc.) inside the web UI.
#
# Before running: edit auth.json with your user accounts.
# ─────────────────────────────────────────────────────────────────────────────

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_DIR/fooocus_env"
PYTHON="$VENV/bin/python"

if [[ ! -f "$PYTHON" ]]; then
    echo "❌  Not installed. Run:  bash install_server.sh"
    exit 1
fi

if [[ ! -f "$REPO_DIR/auth.json" ]]; then
    echo "⚠️   No auth.json found — default credentials active: admin / changeme123"
    echo "    Create auth.json before exposing this to the internet."
    echo ""
fi

echo "🍪  Cookie-Fooocus — Server Mode"
echo "    Auth: enabled  Roles: admin/user  Listen: all interfaces"
echo "    Select presets inside the web UI."
echo ""

source "$VENV/bin/activate"
"$PYTHON" "$REPO_DIR/entry_with_update.py" --server --listen "$@"
