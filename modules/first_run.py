"""
Cookie-Fooocus First-Run Setup Wizard
Runs once on first launch to let the user choose their memory configuration.

Saves choices to ~/.config/cookiefooocus/first_run.json.

Tamper-hardening
────────────────
The config file is validated on every load:
  • All keys must be present and of the correct type
  • Enum values are checked against an explicit allowlist
  • An HMAC-SHA256 signature prevents silent file modification
    (key derived from machine-id so it doesn't leave the device)

If validation fails the wizard re-runs automatically.
"""

import hashlib
import hmac
import json
import os
import platform
import sys
from pathlib import Path

_CONFIG_PATH = Path.home() / ".config" / "cookiefooocus" / "first_run.json"

# ── Allowed values (strict allowlist — anything else is tampered/corrupt) ─────
_VALID_MODES   = {"1", "2", "3", "4", "5"}
_VALID_ARGS    = {
    "1": [],
    "2": ["--always-low-vram"],
    "3": ["--always-cpu"],
    "4": [],
    "5": ["--always-no-vram", "--unet-in-fp8-e4m3fn", "--vae-in-cpu"],
}

MEMORY_MODES = {
    "1": {"label": "GPU (VRAM) — Fastest, requires NVIDIA/AMD GPU with 4 GB+ VRAM",   "args": []},
    "2": {"label": "Low VRAM  — GPU with < 4 GB VRAM (slower but compatible)",         "args": ["--always-low-vram"]},
    "3": {"label": "CPU / RAM — No GPU required (slowest, uses system RAM only)",      "args": ["--always-cpu"]},
    "4": {"label": "Auto-detect — Cookie-Fooocus chooses based on available hardware", "args": []},
    "5": {
        "label": "No VRAM / 16 GB RAM minimum — iGPU, server, or no dedicated GPU (requires 16 GB+ DDR4/DDR5)",
        "args":  ["--always-no-vram", "--unet-in-fp8-e4m3fn", "--vae-in-cpu"],
    },
}


# ── HMAC signing (machine-bound, prevents off-device replay too) ──────────────

def _machine_key() -> bytes:
    """Derive a stable, machine-unique key for HMAC signing."""
    try:
        if platform.system() == "Linux":
            mid = Path("/etc/machine-id").read_text().strip()
        elif platform.system() == "Darwin":
            import subprocess
            mid = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                text=True
            )
        elif platform.system() == "Windows":
            import subprocess
            mid = subprocess.check_output(
                ["wmic", "csproduct", "get", "UUID"],
                text=True
            )
        else:
            mid = "fallback-machine-id"
    except Exception:
        mid = "fallback-machine-id"

    return hashlib.sha256(("cookiefooocus-cfg-v1:" + mid).encode()).digest()


def _sign(payload: str) -> str:
    return hmac.new(_machine_key(), payload.encode(), hashlib.sha256).hexdigest()


def _verify_signature(config_raw: str, stored_sig: str) -> bool:
    expected = _sign(config_raw)
    return hmac.compare_digest(expected, stored_sig)


# ── Config validation ─────────────────────────────────────────────────────────

def _validate(config: dict) -> None:
    """
    Raise ValueError if the config dict fails validation.
    Strict: unknown keys, wrong types, or out-of-allowlist values all fail.
    """
    required = {"memory_mode", "extra_args", "adult_filter_enabled"}
    unknown  = set(config.keys()) - required - {"_sig"}
    if unknown:
        raise ValueError(f"Unknown config keys (possible tampering): {unknown}")

    mode = config.get("memory_mode")
    if mode not in _VALID_MODES:
        raise ValueError(f"Invalid memory_mode {mode!r} — must be one of {_VALID_MODES}")

    args = config.get("extra_args")
    expected_args = _VALID_ARGS[mode]
    if args != expected_args:
        raise ValueError(
            f"extra_args {args!r} does not match expected {expected_args!r} for mode {mode!r}"
        )

    adf = config.get("adult_filter_enabled")
    if not isinstance(adf, bool):
        raise ValueError(f"adult_filter_enabled must be a bool, got {type(adf).__name__!r}")


def _safe_load() -> dict:
    """
    Load and fully validate the config file.
    Raises on any validation or signature error.
    """
    raw  = _CONFIG_PATH.read_text()
    data = json.loads(raw)

    # Signature check
    stored_sig = data.pop("_sig", None)
    if stored_sig is None:
        raise ValueError("Config has no signature — re-running wizard.")
    payload = json.dumps({k: data[k] for k in sorted(data)}, separators=(",", ":"))
    if not _verify_signature(payload, stored_sig):
        raise ValueError("Config signature invalid — file may have been tampered with.")

    _validate(data)
    return data


def _save(config: dict) -> None:
    """Sign and persist the config."""
    payload = json.dumps({k: config[k] for k in sorted(config)}, separators=(",", ":"))
    config  = dict(config)
    config["_sig"] = _sign(payload)
    _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_PATH.write_text(json.dumps(config, indent=2))


# ── Wizard prompts ────────────────────────────────────────────────────────────

def _print_banner() -> None:
    print("\n" + "=" * 60)
    print("   Cookie-Fooocus — First Run Setup")
    print("=" * 60)
    print("This wizard runs once.  Re-run by deleting:")
    print(f"  {_CONFIG_PATH}\n")


def _prompt_memory_mode() -> dict:
    _print_banner()
    print("Select memory / compute mode:\n")
    for key, info in MEMORY_MODES.items():
        print(f"  [{key}] {info['label']}")
    print()
    while True:
        choice = input("Enter choice [1-5] (default 4 = auto): ").strip() or "4"
        if choice in MEMORY_MODES:
            info = MEMORY_MODES[choice]
            print(f"\n  Selected: {info['label']}")
            if choice == "5":
                print("  NOTE: Mode 5 requires at least 16 GB of system RAM (DDR4 or DDR5).")
                print("        Running with less will cause out-of-memory errors.")
            print()
            return {"memory_mode": choice, "extra_args": info["args"]}
        print("  Please enter 1, 2, 3, 4, or 5.")


def _prompt_adult_filter() -> bool:
    print("18+ Content Filter:")
    print("  [Y] ENABLED  — block adult content (recommended, default)")
    print("  [N] DISABLED — no adult content restriction")
    print()
    while True:
        ans = input("Enable 18+ filter? [Y/n]: ").strip().lower() or "y"
        if ans in ("y", "yes"):
            print("  18+ filter ENABLED\n")
            return True
        if ans in ("n", "no"):
            print("  18+ filter DISABLED — you accept responsibility for all content.\n")
            return False
        print("  Please enter Y or N.")


def run_wizard() -> dict:
    config = {}
    config.update(_prompt_memory_mode())
    config["adult_filter_enabled"] = _prompt_adult_filter()
    _save(config)
    print(f"Configuration saved to: {_CONFIG_PATH}\n")
    return config


# ── Public API ────────────────────────────────────────────────────────────────

def load_or_run_wizard() -> dict:
    """
    Return the validated first-run config.
    If the file is missing, corrupt, or tampered with, the wizard re-runs.
    """
    if _CONFIG_PATH.exists():
        try:
            config = _safe_load()
            print(
                f"[Cookie-Fooocus] Config loaded — "
                f"mode={MEMORY_MODES[config['memory_mode']]['label'].split('—')[0].strip()}, "
                f"adult_filter={'ON' if config['adult_filter_enabled'] else 'OFF'}"
            )
            return config
        except Exception as exc:
            print(f"[Cookie-Fooocus] Config validation failed ({exc}). Re-running wizard.")

    # Non-interactive (Docker / CI / piped stdin) — safe defaults, no wizard
    if not sys.stdin.isatty():
        print("[Cookie-Fooocus] Non-interactive mode — using auto-detect + safe defaults.")
        config = {"memory_mode": "4", "extra_args": [], "adult_filter_enabled": True}
        _save(config)
        return config

    return run_wizard()


def apply_memory_config(config: dict) -> None:
    """
    Inject extra CLI flags from the config into sys.argv (before argparse runs)
    and apply the adult-filter toggle.
    """
    for arg in config.get("extra_args", []):
        if arg not in sys.argv:
            sys.argv.append(arg)

    try:
        import modules.content_filter as cf
        cf.set_adult_filter(config.get("adult_filter_enabled", True))
    except ImportError:
        pass
