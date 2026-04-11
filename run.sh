#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# 🍪 Cookie-Fooocus — Local Mode
# Provided by CookieHostUK · Coded with Claude AI assistance
#
# Single-user local mode — no login, no passwords.
# Select your style preset (Realistic, Anime, etc.) inside the web UI.
# ─────────────────────────────────────────────────────────────────────────────

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$REPO_DIR/fooocus_env"
PYTHON="$VENV/bin/python"

if [[ ! -f "$PYTHON" ]]; then
    echo "❌  Not installed. Run:  bash install_local.sh"
    exit 1
fi

echo "🍪  Cookie-Fooocus — Local Mode"
echo "    No auth · Single user · Select presets inside the UI"
echo ""

source "$VENV/bin/activate"
"$PYTHON" "$REPO_DIR/entry_with_update.py" "$@"
