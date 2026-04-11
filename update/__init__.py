"""
Cookie-Fooocus v3 — Update Package
=====================================
Safe, versioned, reversible update system.

Components:
    git_fetcher.py  — fetch release info and download ZIPs from GitHub
    verify.py       — SHA-256 manifest verification (no blind trust)
    rollback.py     — atomic rollback to last stable version
    updater.py      — orchestrator: fetch → verify → stage → swap → restart

Safety guarantees:
    ✔ entrypoint.py and update/ themselves are never auto-replaced while running
    ✔ Downloads go to /tmp staging area, never directly over live code
    ✔ SHA-256 verified before applying
    ✔ Rollback snapshot created before every swap
    ✔ git pull NEVER used in server mode — release ZIPs only
    ✔ Non-blocking — background thread, never delays startup
"""

from update.updater import check_for_update, apply_update, start_background_check  # noqa: F401
