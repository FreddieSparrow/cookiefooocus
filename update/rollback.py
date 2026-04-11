"""
update.rollback — Atomic rollback to previous stable version
=============================================================
Creates rollback snapshots before every update and restores them on failure.

Release directory layout:
    releases/
        v3.0.0/          ← extracted release
        v3.1.0/          ← extracted release
        current          → symlink to active release
        rollback         → symlink to previous stable release

Rules:
    - snapshot() is always called before swap_release()
    - restore() is idempotent — safe to call even if snapshot is partial
    - Large binary assets (models, .safetensors) are NEVER snapshotted
      (they don't change between code releases)

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

log = logging.getLogger("cookiefooocus.update.rollback")

_IGNORE = shutil.ignore_patterns(
    "__pycache__", "*.pyc", "_backup", ".git",
    "outputs", "models", "*.safetensors", "*.ckpt",
    "*.pt", "*.bin", "*.pth",
)


def snapshot(root: Path, backup_dir: Path) -> bool:
    """
    Copy current code to backup_dir before applying an update.

    Args:
        root:       Repository root directory.
        backup_dir: Where to write the snapshot (e.g. root / "_backup").

    Returns True on success.
    """
    try:
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
        shutil.copytree(str(root), str(backup_dir), ignore=_IGNORE)
        log.info("[rollback] Snapshot created at %s", backup_dir)
        return True
    except Exception as exc:
        log.error("[rollback] Snapshot failed: %s", exc)
        return False


def restore(root: Path, backup_dir: Path) -> bool:
    """
    Restore from snapshot after a failed update.

    Returns True on success.
    """
    if not backup_dir.exists():
        log.error("[rollback] No snapshot at %s — cannot restore.", backup_dir)
        return False
    try:
        log.warning("[rollback] Restoring from %s …", backup_dir)
        for item in backup_dir.iterdir():
            dest = root / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(root / item.name))
        log.info("[rollback] Restore complete.")
        return True
    except Exception as exc:
        log.error("[rollback] Restore failed: %s", exc)
        return False


def symlink_releases(releases_dir: Path, version: str) -> None:
    """
    Update the current → <version> symlink and rotate rollback → previous current.

    Layout after call:
        releases/<version>/
        releases/current → <version>
        releases/rollback → <previous version>
    """
    releases_dir.mkdir(parents=True, exist_ok=True)

    current_link  = releases_dir / "current"
    rollback_link = releases_dir / "rollback"
    new_target    = releases_dir / version

    if not new_target.exists():
        log.warning("[rollback] Target %s does not exist — symlink not updated.", new_target)
        return

    # Rotate: current → rollback
    if current_link.exists() or current_link.is_symlink():
        old_target = current_link.resolve() if current_link.is_symlink() else None
        if old_target and old_target != new_target:
            _safe_unlink(rollback_link)
            rollback_link.symlink_to(old_target)
            log.info("[rollback] rollback → %s", old_target.name)

    _safe_unlink(current_link)
    current_link.symlink_to(new_target)
    log.info("[rollback] current → %s", version)


def _safe_unlink(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink():
            path.unlink()
    except Exception as exc:
        log.warning("[rollback] Could not unlink %s: %s", path, exc)
