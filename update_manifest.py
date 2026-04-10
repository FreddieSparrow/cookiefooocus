#!/usr/bin/env python3
"""
Cookie-Fooocus — Manifest Updater
───────────────────────────────────
Run this after updating any safety-critical file to regenerate
security_manifest.json with the new SHA-256 hash.

Usage:
    python update_manifest.py

The manifest is used by launch.py to verify integrity at boot without
requiring a live internet connection.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import hashlib
import json
import sys
from pathlib import Path

ROOT     = Path(__file__).parent
MANIFEST = ROOT / "security_manifest.json"

TRACKED_FILES = {
    "modules/content_filter.py": {
        "version": "3.0.0",
        "required": True,
        "description": "Core safety filter — must not be modified",
    },
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    if not MANIFEST.exists():
        print("[manifest] No existing manifest — creating from scratch.")
        existing: dict = {"manifest_version": "1.0.0", "update_channel": "stable", "files": {}}
    else:
        existing = json.loads(MANIFEST.read_text())

    files: dict = existing.get("files", {})
    changed = []

    for rel, meta in TRACKED_FILES.items():
        path = ROOT / rel
        if not path.exists():
            print(f"  [MISSING]  {rel}")
            sys.exit(1)

        new_hash = _sha256(path)
        old_hash = files.get(rel, {}).get("sha256", "")

        if new_hash != old_hash:
            changed.append(rel)
            print(f"  [UPDATED]  {rel}  {old_hash[:12] or 'new'} → {new_hash[:12]}")
        else:
            print(f"  [OK]       {rel}  {new_hash[:12]}")

        files[rel] = {**meta, "sha256": new_hash}

    existing["files"] = files
    existing["_comment"] = (
        "Cookie-Fooocus security manifest. "
        "Do not edit manually — use 'python update_manifest.py' to regenerate."
    )

    MANIFEST.write_text(json.dumps(existing, indent=2))

    if changed:
        print(f"\n[manifest] Updated {len(changed)} file(s). Manifest saved.")
    else:
        print("\n[manifest] All hashes current. No changes.")


if __name__ == "__main__":
    main()
