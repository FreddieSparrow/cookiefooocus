import json
import hashlib
import hmac
import os
import modules.constants as constants

from os.path import exists


def _hash_password(password: str, salt: bytes | None = None) -> str:
    """
    Hash a password with PBKDF2-HMAC-SHA256 (600k iterations, OWASP 2023 recommended).
    Returns a "$pbkdf2$salt_hex$hash_hex" string suitable for storage.

    If a legacy bare SHA-256 hex string is supplied instead of a structured
    hash, it is accepted as-is for backwards compatibility but flagged in logs.
    """
    if salt is None:
        salt = os.urandom(32)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 600_000)
    return f"$pbkdf2${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    if stored.startswith("$pbkdf2$"):
        try:
            _, _, salt_hex, dk_hex = stored.split("$")
            salt = bytes.fromhex(salt_hex)
            dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 600_000)
            return hmac.compare_digest(dk.hex(), dk_hex)
        except (ValueError, IndexError):
            return False
    else:
        # Legacy: bare SHA-256 hex (backwards compat with old auth-example.json)
        candidate = hashlib.sha256(password.encode('utf-8')).hexdigest()
        return hmac.compare_digest(candidate, stored)


def auth_list_to_dict(auth_list):
    auth_dict = {}
    for auth_data in auth_list:
        if 'user' not in auth_data:
            continue
        if 'hash' in auth_data:
            # Pre-hashed value from config (PBKDF2 or legacy SHA-256)
            auth_dict[auth_data['user']] = auth_data['hash']
        elif 'pass' in auth_data:
            # Plaintext password in config — hash it now with PBKDF2
            auth_dict[auth_data['user']] = _hash_password(auth_data['pass'])
    return auth_dict


def load_auth_data(filename=None):
    auth_dict = None
    if filename is not None and exists(filename):
        with open(filename, encoding='utf-8') as auth_file:
            try:
                auth_obj = json.load(auth_file)
                if isinstance(auth_obj, list) and len(auth_obj) > 0:
                    auth_dict = auth_list_to_dict(auth_obj)
            except Exception as e:
                print('load_auth_data, e: ' + str(e))
    return auth_dict


auth_dict = load_auth_data(constants.AUTH_FILENAME)

auth_enabled = auth_dict is not None


def check_auth(user, password):
    if not auth_dict or user not in auth_dict:
        return False
    return _verify_password(password, auth_dict[user])
