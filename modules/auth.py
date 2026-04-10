"""
Cookie-Fooocus — Hardened Authentication (Server Mode Only)
────────────────────────────────────────────────────────────
PBKDF2-HMAC-SHA256 authentication with role-based access control.

This module is ONLY imported when --server is passed at startup.
In local mode it is never loaded — local mode has no login system.

Password storage format:
  $pbkdf2$<salt_hex>$<dk_hex>

Role system:
  admin — full access: can view audit logs, manage users, change settings
  user  — standard access: generate images only

Default credentials (CHANGE IMMEDIATELY):
  Username: admin
  Password: changeme123
  Role:     admin

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import json
import hashlib
import hmac
import logging
import os
import modules.constants as constants

from os.path import exists

log = logging.getLogger("cookiefooocus.auth")

_VALID_ROLES = {"admin", "user"}
_DEFAULT_ROLE = "user"


def _hash_password(password: str, salt: bytes | None = None) -> str:
    """
    Hash a password with PBKDF2-HMAC-SHA256 (600k iterations, OWASP 2023).
    Returns a "$pbkdf2$salt_hex$hash_hex" string for storage.
    """
    if salt is None:
        salt = os.urandom(32)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 600_000)
    return f"$pbkdf2${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    """Constant-time comparison — prevents timing attacks."""
    if stored.startswith("$pbkdf2$"):
        try:
            _, _, salt_hex, dk_hex = stored.split("$")
            salt = bytes.fromhex(salt_hex)
            dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 600_000)
            return hmac.compare_digest(dk.hex(), dk_hex)
        except (ValueError, IndexError):
            return False
    else:
        # Legacy: bare SHA-256 (backwards compat)
        candidate = hashlib.sha256(password.encode('utf-8')).hexdigest()
        return hmac.compare_digest(candidate, stored)


def auth_list_to_dict(auth_list: list) -> dict:
    """
    Convert auth.json list to {username: {"hash": ..., "role": ...}} dict.
    Plaintext passwords are hashed with PBKDF2 on first load.
    """
    auth_dict = {}
    for auth_data in auth_list:
        if 'user' not in auth_data:
            continue

        role = auth_data.get("role", _DEFAULT_ROLE)
        if role not in _VALID_ROLES:
            log.warning("[auth] Unknown role '%s' for user '%s' — defaulting to 'user'",
                        role, auth_data['user'])
            role = _DEFAULT_ROLE

        if 'hash' in auth_data:
            # Pre-hashed value (PBKDF2 or legacy SHA-256)
            auth_dict[auth_data['user']] = {
                "hash": auth_data['hash'],
                "role": role,
            }
        elif 'pass' in auth_data:
            # Plaintext — hash it now, log a warning
            log.info("[auth] Hashing plaintext password for user '%s'.", auth_data['user'])
            auth_dict[auth_data['user']] = {
                "hash": _hash_password(auth_data['pass']),
                "role": role,
            }
    return auth_dict


def load_auth_data(filename: str = None) -> dict | None:
    auth_dict = None
    if filename is not None and exists(filename):
        with open(filename, encoding='utf-8') as auth_file:
            try:
                auth_obj = json.load(auth_file)
                if isinstance(auth_obj, list) and len(auth_obj) > 0:
                    auth_dict = auth_list_to_dict(auth_obj)
            except Exception as e:
                log.error('[auth] Failed to load auth data: %s', e)
    return auth_dict


def _load_default_auth() -> dict:
    """
    Return the hardcoded default admin account.
    This is used when no auth.json exists.
    IMPORTANT: Change the password immediately after first login.
    """
    log.warning(
        "[auth] No auth.json found — using DEFAULT credentials. "
        "Username: admin  Password: changeme123  "
        "CHANGE THIS IMMEDIATELY by creating auth.json."
    )
    return {
        "admin": {
            "hash": _hash_password("changeme123"),
            "role": "admin",
        }
    }


# Load auth data (or use default)
_raw_auth = load_auth_data(constants.AUTH_FILENAME)
auth_dict = _raw_auth if _raw_auth is not None else _load_default_auth()
auth_enabled = True   # Always enabled in server mode


def check_auth(user: str, password: str) -> bool:
    """Verify username + password. Returns True if valid."""
    if not auth_dict or user not in auth_dict:
        return False
    return _verify_password(password, auth_dict[user]["hash"])


def get_user_role(user: str) -> str | None:
    """Return the role for a user, or None if user doesn't exist."""
    if not auth_dict or user not in auth_dict:
        return None
    return auth_dict[user].get("role", _DEFAULT_ROLE)


def is_admin(user: str) -> bool:
    """Return True if user has admin role."""
    return get_user_role(user) == "admin"


def list_users() -> list[dict]:
    """Return a list of {user, role} dicts (no password hashes)."""
    return [
        {"user": u, "role": v["role"]}
        for u, v in auth_dict.items()
    ]


def change_password(user: str, current_password: str, new_password: str) -> tuple[bool, str]:
    """
    Allow a user to change their own password.

    - User must supply their CURRENT password to authenticate the change.
    - Usernames cannot be changed here (set by admin in auth.json).
    - Returns (success: bool, message: str).

    Password requirements:
      - Minimum 12 characters
      - Cannot be the same as the current password
      - Cannot be the default 'changeme123'
    """
    if not auth_dict or user not in auth_dict:
        return False, "User not found."

    if not _verify_password(current_password, auth_dict[user]["hash"]):
        return False, "Current password is incorrect."

    if len(new_password) < 12:
        return False, "New password must be at least 12 characters."

    if new_password == current_password:
        return False, "New password must be different from current password."

    if new_password in ("changeme123", "password", "admin123", "12345678901234"):
        return False, "That password is not allowed. Please choose a stronger password."

    auth_dict[user]["hash"] = _hash_password(new_password)
    log.info("[auth] Password changed for user '%s'.", user)
    return True, "Password changed successfully."


def admin_add_user(
    admin_user: str,
    new_username: str,
    initial_password: str,
    role: str = "user",
) -> tuple[bool, str]:
    """
    Admin-only: add a new user. Usernames are always set by an admin.

    - admin_user must have role="admin"
    - new_username must be unique
    - initial_password must be ≥ 12 chars (user should change on first login)
    """
    if not is_admin(admin_user):
        return False, "Permission denied — admin role required."

    if new_username in auth_dict:
        return False, f"User '{new_username}' already exists."

    if not new_username or not new_username.replace("_", "").replace("-", "").isalnum():
        return False, "Username must be alphanumeric (underscores/hyphens allowed)."

    if len(initial_password) < 12:
        return False, "Initial password must be at least 12 characters."

    if role not in _VALID_ROLES:
        return False, f"Invalid role '{role}'. Must be one of: {_VALID_ROLES}"

    auth_dict[new_username] = {
        "hash": _hash_password(initial_password),
        "role": role,
    }
    log.info("[auth] Admin '%s' added user '%s' with role '%s'.", admin_user, new_username, role)
    return True, f"User '{new_username}' created with role '{role}'."


def admin_remove_user(admin_user: str, target_user: str) -> tuple[bool, str]:
    """Admin-only: remove a user. Admins cannot remove themselves."""
    if not is_admin(admin_user):
        return False, "Permission denied — admin role required."

    if admin_user == target_user:
        return False, "Cannot remove your own account."

    if target_user not in auth_dict:
        return False, f"User '{target_user}' not found."

    del auth_dict[target_user]
    log.info("[auth] Admin '%s' removed user '%s'.", admin_user, target_user)
    return True, f"User '{target_user}' removed."
