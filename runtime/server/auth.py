"""
runtime.server.auth — Server mode authentication
==================================================
Handles authentication for server mode only.
Never imported in local mode.

Wraps the existing modules.auth implementation and exposes a clean
interface for the FastAPI routes in api.py.

Rules:
    - All server routes require authentication — no anonymous access
    - Sessions use PBKDF2-SHA256 with per-user salts
    - Audit log records every authentication event
    - Failed login attempts are rate-limited

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import logging
from typing import Optional

log = logging.getLogger("cookiefooocus.server.auth")


def _load_auth_backend():
    """Lazy import of modules.auth to keep it out of local mode."""
    try:
        import modules.auth as _auth
        return _auth
    except ImportError as exc:
        log.critical("[server.auth] modules.auth not found: %s", exc)
        raise


def authenticate(username: str, password: str) -> Optional[str]:
    """
    Verify credentials and return a session token on success.
    Returns None if authentication fails.
    Raises RuntimeError if auth subsystem is unavailable.
    """
    auth = _load_auth_backend()
    try:
        return auth.authenticate(username, password)
    except Exception as exc:
        log.warning("[server.auth] authenticate() error: %s", exc)
        return None


def verify_token(token: str) -> Optional[dict]:
    """
    Validate a session token.
    Returns user info dict on success, None on failure.
    """
    auth = _load_auth_backend()
    try:
        return auth.verify_token(token)
    except Exception as exc:
        log.warning("[server.auth] verify_token() error: %s", exc)
        return None


def revoke_token(token: str) -> None:
    """Invalidate a session token (logout)."""
    auth = _load_auth_backend()
    try:
        auth.revoke_token(token)
    except Exception as exc:
        log.warning("[server.auth] revoke_token() error: %s", exc)


def auth_required(token: str) -> dict:
    """
    Verify token and return user info, or raise ValueError if invalid.
    Use as a gate in API routes before any resource access.
    """
    user = verify_token(token)
    if not user:
        raise ValueError("Invalid or expired session token")
    return user
