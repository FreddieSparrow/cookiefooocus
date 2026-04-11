"""
update.updater — Orchestrator: fetch → verify → stage → swap → restart
=========================================================================
The main update coordinator. Called by entrypoint.py on startup.

Safety rules:
    ✔ entrypoint.py and update/ are never auto-replaced while running
    ✔ All downloads go to /tmp staging, never over live code
    ✔ SHA-256 verified before applying
    ✔ Snapshot taken before every swap
    ✔ Rollback triggered automatically on any failure
    ✔ git pull NEVER used in server mode (release ZIPs only)
    ✔ Runs in a daemon thread — never blocks startup

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

log = logging.getLogger("cookiefooocus.update")

_ROOT       = Path(__file__).parent.parent
_BACKUP_DIR = _ROOT / "_backup"
_PROTECTED  = {"entrypoint.py", "update"}   # never auto-replaced


def _current_version() -> str:
    try:
        import fooocus_version
        return getattr(fooocus_version, "version", "0.0.0")
    except ImportError:
        return "0.0.0"


def _version_newer(remote: str, local: str) -> bool:
    def _parse(v: str) -> tuple:
        return tuple(int(x) for x in v.lstrip("v").split(".")[:3] if x.isdigit())
    try:
        return _parse(remote) > _parse(local)
    except Exception:
        return False


def check_for_update() -> Optional[dict]:
    """
    Check GitHub for a newer release.
    Returns the release dict if an update is available, None otherwise.
    """
    from update.git_fetcher import fetch_latest_release
    from modules.auto_updater import _get_channel  # reuse policy reading

    channel = _get_channel()
    local   = _current_version()

    log.info("[updater] Checking (channel=%s, local=v%s)…", channel, local)
    release = fetch_latest_release(channel)
    if not release:
        log.info("[updater] No release info (offline?).")
        return None

    remote = release.get("tag_name", "").lstrip("v")
    if channel != "dev" and not _version_newer(remote, local):
        log.info("[updater] Already up to date (v%s).", local)
        return None

    log.info("[updater] Update available: v%s → v%s", local, remote)
    return release


def apply_update(release: dict, restart: bool = False) -> bool:
    """
    Download, verify, stage, and apply a release.
    Returns True on success. Rolls back automatically on failure.
    """
    from update.git_fetcher   import download_zip, download_manifest, sha256_file
    from update.verify        import verify_zip_against_manifest, compatibility_check
    from update.rollback      import snapshot, restore, symlink_releases

    zip_url      = release.get("zip_url", "")
    manifest_url = release.get("manifest_url")
    version      = release.get("tag_name", "unknown").lstrip("v")

    if not zip_url:
        log.error("[updater] No zip_url in release metadata.")
        return False

    # Compatibility pre-check
    local = _current_version()
    if not compatibility_check(release, local):
        log.warning("[updater] Update rejected by compatibility check.")
        return False

    # Download to temp staging
    with tempfile.TemporaryDirectory(prefix="cf_update_") as staging_dir:
        staging = Path(staging_dir)
        zip_path = staging / "release.zip"

        if not download_zip(zip_url, zip_path):
            log.warning("[updater] Download failed — aborting.")
            return False

        # Download and verify manifest
        manifest = download_manifest(manifest_url) if manifest_url else None
        if not verify_zip_against_manifest(zip_path, manifest):
            log.error("[updater] Integrity check failed — update aborted.")
            return False

        # Snapshot current code before touching anything
        if not snapshot(_ROOT, _BACKUP_DIR):
            log.error("[updater] Could not create snapshot — update aborted.")
            return False

        # Extract ZIP to staging
        extract_dir = staging / "extracted"
        try:
            shutil.unpack_archive(str(zip_path), str(extract_dir))
        except Exception as exc:
            log.error("[updater] Extraction failed: %s", exc)
            restore(_ROOT, _BACKUP_DIR)
            return False

        # Find the root of the extracted tree (GitHub ZIPs have one top-level dir)
        extracted_roots = list(extract_dir.iterdir())
        if len(extracted_roots) == 1 and extracted_roots[0].is_dir():
            new_code = extracted_roots[0]
        else:
            new_code = extract_dir

        # Apply — copy new files over live code, skip protected files
        try:
            for item in new_code.iterdir():
                if item.name in _PROTECTED:
                    log.info("[updater] Skipping protected: %s", item.name)
                    continue
                dest = _ROOT / item.name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(_ROOT / item.name))
        except Exception as exc:
            log.error("[updater] File swap failed: %s — rolling back.", exc)
            restore(_ROOT, _BACKUP_DIR)
            return False

    # Regenerate security manifest
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, str(_ROOT / "update_manifest.py")],
            capture_output=True, text=True, cwd=str(_ROOT),
        )
        if result.returncode != 0:
            log.warning("[updater] Manifest regeneration failed: %s", result.stderr)
    except Exception as exc:
        log.warning("[updater] Could not regenerate manifest: %s", exc)

    # Update symlinks
    releases_dir = _ROOT / "releases"
    symlink_releases(releases_dir, version)

    log.info("[updater] Update applied successfully (v%s).", version)

    if restart:
        log.info("[updater] Restarting to apply update…")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    return True


def start_background_check(restart_on_update: bool = False) -> None:
    """
    Launch the update check in a daemon thread so startup is never blocked.
    """
    def _run():
        try:
            release = check_for_update()
            if release:
                apply_update(release, restart=restart_on_update)
        except Exception as exc:
            log.debug("[updater] Background check error: %s", exc)

    t = threading.Thread(target=_run, daemon=True, name="cf-updater")
    t.start()
    log.debug("[updater] Background update check started.")
