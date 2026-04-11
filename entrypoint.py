"""
Cookie-Fooocus v3 — Entrypoint
================================
One codebase, two runtime profiles.

Usage:
    CF_MODE=local  python entrypoint.py      # local desktop/web UI
    CF_MODE=server python entrypoint.py      # multi-user server mode

Or via the helper scripts:
    bash scripts/run_local.sh
    bash scripts/run_server.sh

Mode is NEVER auto-detected — it must be set explicitly via CF_MODE.
Default is "local" (safe fallback for personal machines).

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

# ── Mode selection ─────────────────────────────────────────────────────────────
# CF_MODE controls EVERYTHING. No magic detection.
mode = os.getenv("CF_MODE", "local").strip().lower()

VALID_MODES = {"local", "server"}
if mode not in VALID_MODES:
    print(f"[entrypoint] ERROR: CF_MODE={mode!r} is not valid. Use 'local' or 'server'.")
    sys.exit(1)

print(f"[Cookie-Fooocus v3] Starting in {mode.upper()} mode")

# ── Startup update check ───────────────────────────────────────────────────────
# The update module is safe to import before the runtime starts — it never
# modifies running code and only triggers a restart after safely staging.
_update_enabled = os.getenv("CF_SKIP_UPDATE", "0") != "1"
if _update_enabled:
    try:
        from update.updater import start_background_check
        start_background_check()
    except Exception as _exc:
        print(f"[entrypoint] Update check unavailable: {_exc}")

# ── Runtime dispatch ────────────────────────────────────────────────────────────
if mode == "local":
    from runtime.local.app import start
    start()

elif mode == "server":
    from runtime.server.api import start
    start()
