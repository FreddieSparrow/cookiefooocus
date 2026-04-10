"""
Cookie-Fooocus Model Verification
Maintains a SHA-256 hash registry of trusted model files.

Why this matters
────────────────
Even .safetensors files (no executable code) can:
  • Crash GPU drivers via malformed tensor shapes
  • Exploit bugs in safetensors/torch parsing libraries
  • Corrupt VRAM through out-of-bounds writes (rare, real)

An explicit hash registry means only files whose content matches a
known-good hash are loaded silently.  Unknown files still load — but
the user/operator sees a clear warning and must explicitly approve.

Usage
─────
  # At startup, add hashes for models you trust:
  register_trusted("my_model.safetensors", "abc123...")

  # Before loading any model:
  verify_model(Path("/models/my_model.safetensors"))
  # → raises ModelUntrustedWarning if hash unknown/mismatch
  # → raises ModelHashMismatch if hash known but wrong
"""

import hashlib
import json
import logging
import warnings
from pathlib import Path
from typing import Optional

log = logging.getLogger("cookiefooocus.model_verify")

# ── Persistent trust-store path ───────────────────────────────────────────────
_TRUST_STORE_PATH = Path.home() / ".config" / "cookiefooocus" / "trusted_models.json"


class ModelHashMismatch(RuntimeError):
    """Raised when a model file's hash does not match the registered value."""


class ModelUntrustedWarning(UserWarning):
    """Issued when a model has no registered hash (unknown, not necessarily malicious)."""


# ── In-memory trust store (name → expected_sha256) ────────────────────────────
_trust_store: dict[str, str] = {}


def _load_trust_store() -> None:
    """Load persisted hashes from disk into memory."""
    global _trust_store
    if _TRUST_STORE_PATH.exists():
        try:
            data = json.loads(_TRUST_STORE_PATH.read_text())
            if isinstance(data, dict):
                _trust_store = data
                log.debug("[verify] Loaded %d trusted model entries.", len(_trust_store))
        except Exception as exc:
            log.warning("[verify] Could not load trust store: %s", exc)


def _save_trust_store() -> None:
    _TRUST_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TRUST_STORE_PATH.write_text(json.dumps(_trust_store, indent=2))


# Load on import
_load_trust_store()


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    """Return the lower-case hex SHA-256 of a file, streaming in chunks."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def register_trusted(filename: str, expected_hash: str) -> None:
    """
    Register a trusted model filename → SHA-256 hash mapping.
    Call this for every model you ship / pre-approve.

    Parameters
    ----------
    filename      : Basename of the model file (e.g. "v1-5-pruned.safetensors")
    expected_hash : Lowercase hex SHA-256 string
    """
    _trust_store[filename] = expected_hash.lower()
    _save_trust_store()
    log.info("[verify] Registered trusted model: %s", filename)


def verify_model(path: Path, allow_unknown: bool = True) -> str:
    """
    Verify a model file against the trust store.

    Returns the actual SHA-256 hex string.

    Raises
    ------
    FileNotFoundError    : path does not exist
    ModelHashMismatch    : hash registered but does not match file
    ModelUntrustedWarning: hash not registered (only if allow_unknown=False)
    """
    if not path.exists():
        raise FileNotFoundError(f"Model file not found: {path}")

    actual_hash = sha256_file(path)
    name        = path.name

    if name in _trust_store:
        expected = _trust_store[name]
        if actual_hash != expected:
            raise ModelHashMismatch(
                f"SECURITY: Model '{name}' hash mismatch!\n"
                f"  Expected : {expected}\n"
                f"  Actual   : {actual_hash}\n"
                "The file may have been tampered with. Remove it or re-register its hash."
            )
        log.info("[verify] ✓ Trusted model verified: %s", name)
    else:
        msg = (
            f"Model '{name}' has no registered hash (SHA-256: {actual_hash}).\n"
            "If you trust this file, register it with:\n"
            f"  from modules.model_verification import register_trusted\n"
            f"  register_trusted('{name}', '{actual_hash}')"
        )
        if allow_unknown:
            warnings.warn(msg, ModelUntrustedWarning, stacklevel=2)
            log.warning("[verify] UNKNOWN model loaded without hash verification: %s", name)
        else:
            raise ModelUntrustedWarning(msg)

    return actual_hash


def trust_and_register(path: Path) -> str:
    """
    Compute hash of the given model and add it to the trust store.
    Use this once after manually inspecting a new model file.

    Returns the registered SHA-256 hash.
    """
    actual_hash = sha256_file(path)
    register_trusted(path.name, actual_hash)
    return actual_hash


def list_trusted() -> dict[str, str]:
    """Return a copy of the current trust store {filename: sha256}."""
    return dict(_trust_store)
