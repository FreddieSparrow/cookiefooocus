"""
update.git_fetcher — GitHub release fetcher
=============================================
Fetches release metadata and downloads release ZIPs from GitHub.

Design decisions:
    - Uses GitHub Releases API, NOT raw git pull
    - Downloads are pinned to tagged releases (not latest commit)
    - dev channel is the only exception — uses commit SHAs
    - All network calls have explicit timeouts
    - Returns None on any network failure (caller decides what to do)

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

log = logging.getLogger("cookiefooocus.update.fetcher")

_REPO_OWNER      = "FreddieSparrow"
_REPO_NAME       = "cookiefooocus"
_GITHUB_API      = f"https://api.github.com/repos/{_REPO_OWNER}/{_REPO_NAME}"
_API_TIMEOUT_S   = 15
_DL_TIMEOUT_S    = 120


def _current_version() -> str:
    try:
        import fooocus_version
        return getattr(fooocus_version, "version", "0.0.0")
    except ImportError:
        return "0.0.0"


def _ua() -> str:
    return f"cookiefooocus/{_current_version()}"


def fetch_latest_release(channel: str = "stable") -> Optional[dict]:
    """
    Fetch the latest release metadata from GitHub.

    Returns a dict with at minimum:
        {
            "tag_name": "v3.1.0",
            "zip_url":  "https://...",
            "manifest_url": "https://...",   # optional
        }

    Returns None on any network or parse error.
    """
    try:
        if channel == "dev":
            url = f"{_GITHUB_API}/commits/main"
        elif channel == "beta":
            url = f"{_GITHUB_API}/releases"
        else:
            url = f"{_GITHUB_API}/releases/latest"

        req = urllib.request.Request(
            url,
            headers={"User-Agent": _ua(), "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT_S) as resp:
            import json
            data = json.loads(resp.read())

        if channel == "beta" and isinstance(data, list):
            data = data[0] if data else None
        if not data:
            return None

        if channel == "dev":
            return {
                "tag_name":    data.get("sha", "")[:7],
                "zip_url":     f"https://github.com/{_REPO_OWNER}/{_REPO_NAME}/archive/main.zip",
                "manifest_url": None,
            }

        tag      = data.get("tag_name", "")
        zip_url  = data.get("zipball_url", "")
        manifest_url: Optional[str] = None

        # Look for manifest in release assets
        for asset in data.get("assets", []):
            if asset.get("name", "").endswith("_manifest.json"):
                manifest_url = asset.get("browser_download_url")
                break

        return {"tag_name": tag, "zip_url": zip_url, "manifest_url": manifest_url}

    except urllib.error.URLError as exc:
        log.debug("[fetcher] Offline or unreachable: %s", exc)
        return None
    except Exception as exc:
        log.warning("[fetcher] fetch_latest_release failed: %s", exc)
        return None


def download_zip(url: str, dest: Path) -> bool:
    """
    Download a release ZIP to dest.
    Returns True on success, False on any error.
    """
    try:
        log.info("[fetcher] Downloading %s → %s", url, dest)
        req = urllib.request.Request(url, headers={"User-Agent": _ua()})
        with urllib.request.urlopen(req, timeout=_DL_TIMEOUT_S) as resp:
            dest.write_bytes(resp.read())
        log.info("[fetcher] Download complete (%d bytes)", dest.stat().st_size)
        return True
    except Exception as exc:
        log.warning("[fetcher] Download failed: %s", exc)
        return False


def download_manifest(url: str) -> Optional[dict]:
    """
    Download and parse a JSON release manifest.
    Returns None on any error.
    """
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _ua()})
        with urllib.request.urlopen(req, timeout=_API_TIMEOUT_S) as resp:
            import json
            return json.loads(resp.read())
    except Exception as exc:
        log.warning("[fetcher] Manifest download failed: %s", exc)
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
