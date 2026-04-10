"""
Cookie-Fooocus — Security Module
──────────────────────────────────
Boundary for all security-related functionality:
  - Authentication (server mode only)
  - Rate limiting
  - Session management

This module is only fully active in --server mode.
In local mode, auth functions return safe no-op defaults.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import args_manager as _args

_server_mode: bool = getattr(getattr(_args, "args", None), "server", False)

if _server_mode:
    from modules.auth import (
        auth_enabled,
        check_auth,
        load_auth_data,
    )
    from modules.session_manager import (
        create_session,
        validate_session,
        revoke_session,
        get_session_user,
    )
else:
    # Local mode — auth is completely absent
    auth_enabled = False
    check_auth   = None

    def create_session(*_a, **_kw):
        return None

    def validate_session(*_a, **_kw):
        return False

    def revoke_session(*_a, **_kw):
        pass

    def get_session_user(*_a, **_kw):
        return "local"


__all__ = [
    "auth_enabled",
    "check_auth",
    "create_session",
    "validate_session",
    "revoke_session",
    "get_session_user",
]
