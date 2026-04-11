"""
runtime.local.app — Local mode launcher
=========================================
Starts the Gradio-based desktop/web UI for single-user personal use.

Local mode philosophy: "Make it run, even if quality drops."
- One process, one queue
- No authentication
- No tenancy
- VRAM failures are soft (auto-downscale, never hard reject)
- Update prompt shown on startup (optional)
- No network dependencies beyond model downloads

This module only wires up the local environment and delegates all
generation logic to core/. It never contains inference code.
"""

import os
import sys
import logging

log = logging.getLogger("cookiefooocus.local")

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _apply_local_env() -> None:
    """Apply local-mode environment defaults before anything else loads."""
    # MPS (Apple Silicon) environment variables must be set before torch imports
    os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")
    os.environ.setdefault("GRADIO_SERVER_PORT", "7865")
    # Signal to modules that we are in local mode (no auth, relaxed queue)
    os.environ["CF_MODE"] = "local"


def _load_local_config() -> dict:
    """
    Load config/local.json on top of config/base.json.
    Returns merged config dict. Failures are non-fatal in local mode.
    """
    import json
    base_path  = os.path.join(_ROOT, "config", "base.json")
    local_path = os.path.join(_ROOT, "runtime", "local", "config.json")

    config: dict = {}
    for path in (base_path, local_path):
        try:
            with open(path) as f:
                config.update(json.load(f))
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning("[local] Could not load %s: %s", path, exc)
    return config


def _run_hooks(event: str, config: dict) -> None:
    """
    Execute local hooks defined in runtime/local/hooks.py.
    Hooks are always optional — missing or failing hooks never abort startup.
    """
    try:
        from runtime.local import hooks
        fn = getattr(hooks, event, None)
        if callable(fn):
            fn(config)
    except Exception as exc:
        log.debug("[local] Hook %r skipped: %s", event, exc)


def start() -> None:
    """
    Main entry point for local mode.
    Called by entrypoint.py when CF_MODE=local.
    """
    _apply_local_env()
    config = _load_local_config()

    log.info("[local] Starting Cookie-Fooocus in LOCAL mode")
    log.info("[local] Port: %s", os.environ["GRADIO_SERVER_PORT"])

    _run_hooks("on_startup", config)

    # Delegate to the existing launch machinery — it handles Gradio, model
    # loading, and the full UI. We only provide the environment context here.
    try:
        # launch.py is the existing Fooocus launcher — it reads env vars we set
        # above and starts the Gradio server. In local mode we pass --local-mode
        # internally to disable server-specific subsystems (auth, audit logging).
        sys.argv = _build_argv(config)
        import launch  # noqa: F401 — side-effectful import starts the server
    except SystemExit:
        raise
    except Exception as exc:
        log.critical("[local] Startup failed: %s", exc, exc_info=True)
        sys.exit(1)

    _run_hooks("on_shutdown", config)


def _build_argv(config: dict) -> list:
    """Build sys.argv for the Gradio launcher from local config."""
    import sys as _sys
    argv = [_sys.argv[0]]

    port = config.get("port", os.environ.get("GRADIO_SERVER_PORT", "7865"))
    argv += ["--port", str(port)]

    if config.get("share", False):
        argv.append("--share")

    if config.get("theme"):
        argv += ["--theme", config["theme"]]

    if config.get("language"):
        argv += ["--language", config["language"]]

    if config.get("disable_image_log", False):
        argv.append("--disable-image-log")

    return argv
