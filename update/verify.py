"""
update.verify — SHA-256 manifest verification
===============================================
Verifies the integrity of a downloaded release before applying it.

Rules:
    - Never trust a download without checking the SHA-256 manifest
    - Manifest must be downloaded separately from the ZIP (different path)
    - A missing manifest is a warning, not a hard failure — but logs loudly
    - A hash mismatch is always a hard failure (abort the update)

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("cookiefooocus.update.verify")


def verify_zip_against_manifest(
    zip_path: Path,
    manifest: Optional[dict],
) -> bool:
    """
    Verify the downloaded ZIP against the release manifest.

    Args:
        zip_path:  Path to the downloaded .zip file.
        manifest:  Parsed manifest dict from the GitHub release.
                   Expected shape: {"files": {"release.zip": {"sha256": "..."}}}

    Returns:
        True  — verified OK (or no manifest, accepted with warning)
        False — hash mismatch (abort update)
    """
    if not zip_path.exists():
        log.error("[verify] ZIP not found at %s", zip_path)
        return False

    if not manifest:
        log.warning(
            "[verify] No manifest available — skipping hash check. "
            "This is acceptable for dev/beta channels but not recommended for stable."
        )
        return True   # Accept without verification (warn only)

    files = manifest.get("files", {})
    zip_name = zip_path.name
    meta = files.get(zip_name) or files.get("release.zip")

    if not meta:
        log.warning("[verify] ZIP not listed in manifest — accepting with warning.")
        return True

    expected = meta.get("sha256", "").lower()
    if not expected:
        log.warning("[verify] No sha256 in manifest entry for %s.", zip_name)
        return True

    actual = _sha256(zip_path)
    if actual != expected:
        log.error(
            "[verify] HASH MISMATCH for %s\n  expected: %s\n  actual:   %s",
            zip_path, expected, actual,
        )
        return False

    log.info("[verify] %s — SHA-256 OK (%s…)", zip_name, actual[:12])
    return True


def compatibility_check(release_meta: dict, current_version: str) -> bool:
    """
    Check whether the new release is compatible with the current installation.

    Checks:
        - Minimum Python version requirement
        - Breaking change flag in manifest

    Returns True if the update should proceed.
    """
    min_python = release_meta.get("min_python", "3.10")
    breaking   = release_meta.get("breaking_change", False)

    import sys
    major, minor = sys.version_info.major, sys.version_info.minor
    req_major, req_minor = (int(x) for x in min_python.split(".")[:2])

    if (major, minor) < (req_major, req_minor):
        log.error(
            "[verify] Python %s.%s does not meet minimum requirement %s for this release.",
            major, minor, min_python,
        )
        return False

    if breaking:
        log.warning(
            "[verify] Release is marked as a BREAKING CHANGE from %s. "
            "Review the changelog before applying.",
            current_version,
        )
        # Don't block — the user opted into updates. Log and proceed.

    return True


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
