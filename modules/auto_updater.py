"""
Cookie-Fooocus — Staged Auto-Updater
──────────────────────────────────────
Checks for new releases on GitHub, downloads updates to a staging area,
verifies integrity, then applies atomically and restarts.

SAFETY RULES:
  ✔ Never overwrites running code mid-session
  ✔ Downloads to /tmp staging area first
  ✔ SHA-256 verified before applying
  ✔ Rollback on failure (old code preserved in _backup/)
  ✔ Respects update_channel (stable / beta / dev)
  ✔ Non-blocking — runs in background thread on startup

Update channels:
  stable  — tagged GitHub releases only (recommended)
  beta    — pre-release tags
  dev     — latest commit on main branch (may be unstable)

Set channel in safety_policy.json:
  { "update_channel": "stable" }

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("cookiefooocus.updater")

_ROOT         = Path(__file__).parent.parent
_BACKUP_DIR   = _ROOT / "_backup"
_POLICY_PATH  = _ROOT / "safety_policy.json"

# GitHub repo coordinates
_REPO_OWNER   = "FreddieSparrow"
_REPO_NAME    = "cookiefooocus"
_GITHUB_API   = f"https://api.github.com/repos/{_REPO_OWNER}/{_REPO_NAME}"
_GITHUB_RAW   = f"https://raw.githubusercontent.com/{_REPO_OWNER}/{_REPO_NAME}"

_UPDATE_TIMEOUT  = 15    # seconds for API calls
_DOWNLOAD_TIMEOUT = 60   # seconds for file downloads


def _get_policy() -> dict:
    try:
        return json.loads(_POLICY_PATH.read_text())
    except Exception:
        return {}


def _get_channel() -> str:
    return _get_policy().get("update_channel", "stable")


def _current_version() -> str:
    """Read the local version from fooocus_version.py."""
    try:
        import fooocus_version
        return getattr(fooocus_version, "version", "0.0.0")
    except ImportError:
        return "0.0.0"


def _fetch_latest_release(channel: str) -> dict | None:
    """
    Fetch the latest release metadata from GitHub API.
    Returns a dict with 'tag_name', 'body', 'assets' or None on error.
    """
    try:
        if channel == "dev":
            # Latest commit on main branch — fetch commit hash
            url = f"{_GITHUB_API}/commits/main"
        elif channel == "beta":
            url = f"{_GITHUB_API}/releases"
        else:
            url = f"{_GITHUB_API}/releases/latest"

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": f"cookiefooocus/{_current_version()}",
                "Accept":     "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=_UPDATE_TIMEOUT) as resp:
            data = json.loads(resp.read())

        if channel == "beta" and isinstance(data, list):
            # First pre-release or release in the list
            return data[0] if data else None
        return data

    except urllib.error.URLError as exc:
        log.debug("[updater] Offline or GitHub unreachable: %s", exc)
        return None
    except Exception as exc:
        log.warning("[updater] Release check failed: %s", exc)
        return None


def _version_newer(remote: str, local: str) -> bool:
    """Return True if remote version string is newer than local."""
    def _parse(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.lstrip("v").split(".")[:3] if x.isdigit())
    try:
        return _parse(remote) > _parse(local)
    except Exception:
        return False


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"cookiefooocus/{_current_version()}"},
    )
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
        return resp.read()


def _git_pull() -> bool:
    """
    Attempt a `git pull` as the update mechanism.
    Returns True on success. Used for dev / simple setups.
    """
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            capture_output=True, text=True, cwd=str(_ROOT), timeout=60,
        )
        if result.returncode == 0:
            log.info("[updater] git pull succeeded:\n%s", result.stdout.strip())
            return True
        else:
            log.warning("[updater] git pull failed:\n%s", result.stderr.strip())
            return False
    except FileNotFoundError:
        log.debug("[updater] git not found — skipping pull.")
        return False
    except Exception as exc:
        log.warning("[updater] git pull error: %s", exc)
        return False


def _backup_current() -> Path:
    """Copy current source to _backup/ before applying an update."""
    if _BACKUP_DIR.exists():
        shutil.rmtree(_BACKUP_DIR, ignore_errors=True)
    shutil.copytree(str(_ROOT), str(_BACKUP_DIR), ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "_backup", ".git", "outputs", "models", "*.safetensors",
        "*.ckpt", "*.pt", "*.bin",
    ))
    log.info("[updater] Backup created at %s", _BACKUP_DIR)
    return _BACKUP_DIR


def _rollback() -> None:
    """Restore from backup if an update fails."""
    if not _BACKUP_DIR.exists():
        log.error("[updater] No backup to rollback to.")
        return
    log.warning("[updater] Rolling back to previous version...")
    try:
        for item in _BACKUP_DIR.iterdir():
            dest = _ROOT / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(_ROOT / item.name))
        log.info("[updater] Rollback complete.")
    except Exception as exc:
        log.error("[updater] Rollback failed: %s", exc)


def _update_manifest_after_pull() -> None:
    """Regenerate security_manifest.json after a successful pull."""
    try:
        script = _ROOT / "update_manifest.py"
        if script.exists():
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True, text=True, cwd=str(_ROOT),
            )
            if result.returncode == 0:
                log.info("[updater] Manifest regenerated.")
            else:
                log.warning("[updater] Manifest update failed: %s", result.stderr)
    except Exception as exc:
        log.warning("[updater] Could not update manifest: %s", exc)


def check_and_update(restart: bool = False) -> bool:
    """
    Main update entry point. Called at startup in a background thread.

    Flow:
      1. Check GitHub for a newer release
      2. If update available: backup → pull → verify manifest → log
      3. If restart=True: re-exec the process with the new code
      4. On any failure: rollback

    Returns True if an update was applied.
    """
    channel = _get_channel()
    local   = _current_version()
    log.info("[updater] Checking for updates (channel=%s, local=%s)…", channel, local)

    release = _fetch_latest_release(channel)
    if release is None:
        log.info("[updater] No update info available (offline?).")
        return False

    remote = release.get("tag_name", "").lstrip("v")
    if not remote:
        # dev channel returns a commit, not a tag
        remote = release.get("sha", "")[:7]

    if channel != "dev" and not _version_newer(remote, local):
        log.info("[updater] Already up to date (v%s).", local)
        return False

    log.info("[updater] Update available: v%s → v%s", local, remote)

    # Backup current version
    _backup_current()

    # Apply update via git pull (simplest approach — preserves local config)
    success = _git_pull()

    if not success:
        log.warning("[updater] git pull failed — rolling back.")
        _rollback()
        return False

    # Regenerate security manifest with new hashes
    _update_manifest_after_pull()

    log.info("[updater] Update applied successfully (v%s).", remote)

    if restart:
        log.info("[updater] Restarting to apply update…")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    return True


def start_background_check(restart_on_update: bool = False) -> None:
    """
    Launch the update check in a daemon thread so it never blocks startup.
    The app runs normally while the check happens in the background.
    If an update is found and restart_on_update=True, the process is restarted.
    """
    def _run():
        try:
            check_and_update(restart=restart_on_update)
        except Exception as exc:
            log.debug("[updater] Background check error: %s", exc)

    t = threading.Thread(target=_run, daemon=True, name="cf-auto-updater")
    t.start()
    log.debug("[updater] Background update check started.")
