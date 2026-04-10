"""
Cookie-Fooocus — Session Manager (Server Mode Only)
─────────────────────────────────────────────────────
Issues short-lived session tokens after successful authentication so
users don't re-hash passwords on every request.

Properties:
  - 256-bit random token (cryptographically secure)
  - Configurable TTL (default 3 600 s / 1 hour)
  - In-memory store — sessions do not survive restart (intentional)
  - Thread-safe

ONLY loaded when --server flag is active (modules/security/__init__.py gates it).

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import logging
import secrets
import threading
import time
from typing import Optional

log = logging.getLogger("cookiefooocus.session")

_SESSION_TTL  = 3_600     # seconds — 1 hour
_TOKEN_BYTES  = 32        # 256-bit token

_sessions: dict[str, dict] = {}   # token → {user, role, created, expires}
_lock = threading.Lock()


def create_session(user: str, role: str = "user") -> str:
    """
    Create a new session for an authenticated user.
    Returns the session token (opaque 64-char hex string).
    """
    token  = secrets.token_hex(_TOKEN_BYTES)
    now    = time.monotonic()
    with _lock:
        _sessions[token] = {
            "user":    user,
            "role":    role,
            "created": now,
            "expires": now + _SESSION_TTL,
        }
    log.info("[session] Created session for user=%s role=%s", user, role)
    return token


def validate_session(token: str) -> bool:
    """Return True if the token is valid and not expired."""
    if not token:
        return False
    with _lock:
        sess = _sessions.get(token)
        if sess is None:
            return False
        if time.monotonic() > sess["expires"]:
            del _sessions[token]
            return False
        return True


def get_session_user(token: str) -> Optional[str]:
    """Return the username for a valid token, or None."""
    with _lock:
        sess = _sessions.get(token)
        if sess and time.monotonic() <= sess["expires"]:
            return sess["user"]
    return None


def get_session_role(token: str) -> Optional[str]:
    """Return the role for a valid token, or None."""
    with _lock:
        sess = _sessions.get(token)
        if sess and time.monotonic() <= sess["expires"]:
            return sess["role"]
    return None


def revoke_session(token: str) -> None:
    """Invalidate a session immediately (logout)."""
    with _lock:
        _sessions.pop(token, None)


def purge_expired() -> int:
    """Remove all expired sessions. Returns number removed."""
    now = time.monotonic()
    with _lock:
        expired = [t for t, s in _sessions.items() if now > s["expires"]]
        for t in expired:
            del _sessions[t]
    return len(expired)


def active_session_count() -> int:
    """Return number of currently active (non-expired) sessions."""
    now = time.monotonic()
    with _lock:
        return sum(1 for s in _sessions.values() if now <= s["expires"])
