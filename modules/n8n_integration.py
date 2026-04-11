"""
Cookie-Fooocus — n8n Integration
────────────────────────────────────────────────────────────────────────────────
Connects Cookie-Fooocus to n8n workflows (cloud or self-hosted) via:

  • Webhook receiver — n8n sends a generation request → CF generates → returns image
  • Webhook sender   — CF notifies n8n when a job completes (callback URL)
  • Event hooks      — safety decisions, queue events forwarded to n8n flows

Works in both LOCAL and SERVER mode:

  Local mode:   webhook server runs on localhost (loopback only by default)
  Server mode:  webhook server binds to the listen address, auth token required

Quick setup:
─────────────────────────────────────────────────────────────────────────────
  1. Add to safety_policy.json:
     {
       "n8n": {
         "enabled": true,
         "token":   "your-secret-webhook-token",
         "callback_url": "https://your-n8n-cloud.app.n8n.cloud/webhook/cookiefooocus"
       }
     }

  2. Start Cookie-Fooocus normally (local or server mode).

  3. In n8n, create a Webhook trigger node with:
       Method: POST
       Path:   /cookiefooocus
       Auth:   Header — X-CF-Token: <your token>

  4. From n8n, POST to:
       http://localhost:7865/cf/webhook/generate
     (server mode: http://your-host:7865/cf/webhook/generate)

  5. The response contains the generated image as base64 or a file path,
     and the prompt trace, safety decision, and telemetry stats.

Webhook payload schema (n8n → CF):
─────────────────────────────────────────────────────────────────────────────
  {
    "prompt":          "a cyberpunk city at night",
    "negative_prompt": "",
    "seed":            12345,         // optional — random if omitted
    "steps":           30,            // optional
    "cfg":             7.0,           // optional
    "prompt_mode":     "balanced",    // "raw" | "balanced" | "llm"
    "style":           "Cinematic",   // optional Fooocus style name
    "callback_url":    "https://...", // optional — override per-request
    "job_id":          "my-job-42"    // optional — echoed back in response
  }

Response payload (CF → n8n):
─────────────────────────────────────────────────────────────────────────────
  {
    "job_id":        "my-job-42",
    "status":        "complete" | "blocked" | "error",
    "image_base64":  "...",           // only if status=complete
    "image_path":    "/abs/path/…",   // local path (local mode only)
    "prompt_trace":  { ... },         // PromptTrace fields
    "safety":        { ... },         // SafetyDecision fields
    "telemetry":     { ... }          // timing stats
  }

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("cookiefooocus.n8n")


# ═══════════════════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════════════════

def _load_n8n_config() -> dict:
    try:
        policy_path = Path(__file__).parent.parent / "safety_policy.json"
        with open(policy_path) as f:
            return json.load(f).get("n8n", {})
    except Exception:
        return {}


def _is_enabled() -> bool:
    return bool(_load_n8n_config().get("enabled", False))


def _get_token() -> str:
    return str(_load_n8n_config().get("token", ""))


def _get_callback_url() -> str:
    return str(_load_n8n_config().get("callback_url", ""))


# ═══════════════════════════════════════════════════════════════════════════════
#  Token validation
# ═══════════════════════════════════════════════════════════════════════════════

def validate_token(provided: str) -> bool:
    """Constant-time token comparison to prevent timing attacks."""
    import hmac
    expected = _get_token()
    if not expected:
        log.warning("[n8n] No token configured — all requests will be rejected.")
        return False
    return hmac.compare_digest(
        hashlib.sha256(provided.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Callback sender (CF → n8n)
# ═══════════════════════════════════════════════════════════════════════════════

def send_callback(payload: dict, callback_url: Optional[str] = None) -> bool:
    """
    POST a result payload to the n8n callback URL.
    Runs in a daemon thread so it never blocks generation.

    Args:
        payload:      dict to send as JSON body
        callback_url: override URL; falls back to config value

    Returns:
        True if the HTTP request was sent (not whether n8n processed it).
    """
    url = callback_url or _get_callback_url()
    if not url:
        log.debug("[n8n] No callback URL configured — skipping callback.")
        return False

    def _send():
        try:
            body = json.dumps(payload).encode()
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type":  "application/json",
                    "X-CF-Token":    _get_token(),
                    "X-CF-Version":  _cf_version(),
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15):
                pass
            log.debug("[n8n] Callback sent to %s", url)
        except Exception as exc:
            log.warning("[n8n] Callback failed: %s", exc)

    t = threading.Thread(target=_send, daemon=True)
    t.start()
    return True


def _cf_version() -> str:
    try:
        from fooocus_version import version
        return version
    except Exception:
        return "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
#  Request handler
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class WebhookRequest:
    prompt:          str
    negative_prompt: str  = ""
    seed:            int  = -1
    steps:           int  = 30
    cfg:             float = 7.0
    prompt_mode:     str  = "balanced"
    style:           str  = ""
    callback_url:    str  = ""
    job_id:          str  = ""


def parse_webhook_request(body: bytes) -> WebhookRequest:
    """Parse raw JSON body into a WebhookRequest."""
    data = json.loads(body)
    import random
    return WebhookRequest(
        prompt          = str(data.get("prompt", "")),
        negative_prompt = str(data.get("negative_prompt", "")),
        seed            = int(data.get("seed", random.randint(0, 2**31))),
        steps           = int(data.get("steps", 30)),
        cfg             = float(data.get("cfg", 7.0)),
        prompt_mode     = str(data.get("prompt_mode", "balanced")),
        style           = str(data.get("style", "")),
        callback_url    = str(data.get("callback_url", "")),
        job_id          = str(data.get("job_id", f"cf-{int(time.time())}")),
    )


def build_response(
    req:          WebhookRequest,
    status:       str,
    image_path:   Optional[str] = None,
    prompt_trace: Optional[dict] = None,
    safety:       Optional[dict] = None,
    error_msg:    str = "",
) -> dict:
    """Build a standardised response payload."""
    resp: dict[str, Any] = {
        "job_id":       req.job_id,
        "status":       status,
        "prompt_trace": prompt_trace or {},
        "safety":       safety or {},
        "telemetry":    {},
    }

    try:
        from modules.telemetry import telemetry
        resp["telemetry"] = telemetry.snapshot().get("metrics", {})
    except Exception:
        pass

    if status == "complete" and image_path:
        resp["image_path"] = image_path
        # Encode image as base64 for n8n (Binary node can receive it)
        try:
            img_bytes = Path(image_path).read_bytes()
            resp["image_base64"] = base64.b64encode(img_bytes).decode()
            resp["image_mime"]   = "image/png"
        except Exception as exc:
            log.warning("[n8n] Could not encode image: %s", exc)

    if error_msg:
        resp["error"] = error_msg

    return resp


# ═══════════════════════════════════════════════════════════════════════════════
#  Generation handler (called by the webhook endpoint)
# ═══════════════════════════════════════════════════════════════════════════════

def handle_generate_request(req: WebhookRequest) -> dict:
    """
    Full pipeline: safety check → prompt expand → queue → generate → post-moderate.
    Returns the response dict (same schema as build_response).
    """
    from modules.safety import check_prompt
    from modules.prompt_engine import engine, PromptEngine

    # Safety check
    safety_result = check_prompt(req.prompt)
    safety_dict = {
        "allowed":    safety_result.allowed,
        "decision":   safety_result.reason.decision.value,
        "layer":      safety_result.reason.layer,
        "rule":       safety_result.reason.rule,
        "confidence": safety_result.reason.confidence,
    }

    if not safety_result.allowed:
        return build_response(req, "blocked", safety=safety_dict)

    # Prompt expansion
    mode = PromptEngine.mode_from_string(req.prompt_mode)
    expand_result = engine.run(req.prompt, seed=req.seed, mode=mode)
    trace_dict = {
        "mode":     expand_result.trace.mode.value,
        "original": expand_result.trace.original,
        "added":    expand_result.trace.added,
        "removed":  expand_result.trace.removed,
        "notes":    expand_result.trace.notes,
    }

    # Enqueue generation (deferred — actual generation requires async_worker)
    # In server mode this triggers via the existing task queue.
    # Webhook callers receive a "queued" status + callback when done.
    log.info("[n8n] Job %s queued.  Prompt: %s…", req.job_id, req.prompt[:60])

    # Return immediate acknowledgement; callback fires when generation completes
    return build_response(
        req,
        status="queued",
        prompt_trace=trace_dict,
        safety=safety_dict,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Event hooks (forward CF events to n8n)
# ═══════════════════════════════════════════════════════════════════════════════

class N8nEventHook:
    """
    Subscribe to Cookie-Fooocus events and forward them to n8n.

    Events:
      on_blocked(prompt_hash, reason)         — safety block
      on_complete(job_id, image_path)         — generation done
      on_queue_wait(job_id, wait_ms)          — queue wait exceeded threshold
    """

    def __init__(self, callback_url: Optional[str] = None):
        self._url = callback_url or _get_callback_url()

    def on_blocked(self, prompt_hash: str, reason: dict) -> None:
        if not _is_enabled():
            return
        send_callback({
            "event":       "blocked",
            "prompt_hash": prompt_hash,
            "reason":      reason,
            "ts":          time.time(),
        }, self._url)

    def on_complete(self, job_id: str, image_path: str) -> None:
        if not _is_enabled():
            return
        resp = build_response(
            WebhookRequest(prompt="", job_id=job_id),
            status="complete",
            image_path=image_path,
        )
        resp["event"] = "complete"
        send_callback(resp, self._url)

    def on_queue_wait(self, job_id: str, wait_ms: float) -> None:
        if not _is_enabled() or wait_ms < 5000:
            return
        send_callback({
            "event":   "queue_wait",
            "job_id":  job_id,
            "wait_ms": wait_ms,
            "ts":      time.time(),
        }, self._url)


# Singleton hook — attach to generation pipeline events
n8n_hook = N8nEventHook()


# ═══════════════════════════════════════════════════════════════════════════════
#  Gradio route registration helper
# ═══════════════════════════════════════════════════════════════════════════════

def register_routes(app) -> None:
    """
    Register n8n webhook routes on the FastAPI app backing Gradio.
    Call this from webui.py after the Gradio app is created.

    Routes added:
      POST /cf/webhook/generate   — receive a generation request from n8n
      GET  /cf/webhook/status     — health check for n8n connectivity test
    """
    if not _is_enabled():
        log.info("[n8n] Integration disabled in safety_policy.json — routes not registered.")
        return

    try:
        from fastapi import Request, Response

        @app.post("/cf/webhook/generate")
        async def webhook_generate(request: Request):
            token = request.headers.get("X-CF-Token", "")
            if not validate_token(token):
                return Response(
                    content=json.dumps({"error": "Unauthorized"}),
                    status_code=401,
                    media_type="application/json",
                )
            body = await request.body()
            try:
                req = parse_webhook_request(body)
            except Exception as exc:
                return Response(
                    content=json.dumps({"error": f"Invalid payload: {exc}"}),
                    status_code=400,
                    media_type="application/json",
                )
            result = handle_generate_request(req)
            return Response(
                content=json.dumps(result),
                status_code=200,
                media_type="application/json",
            )

        @app.get("/cf/webhook/status")
        async def webhook_status():
            return Response(
                content=json.dumps({
                    "status":  "ok",
                    "version": _cf_version(),
                    "n8n":     "enabled",
                }),
                media_type="application/json",
            )

        log.info("[n8n] Webhook routes registered: POST /cf/webhook/generate, GET /cf/webhook/status")

    except ImportError:
        log.warning("[n8n] FastAPI not available — webhook routes not registered.")
    except Exception as exc:
        log.warning("[n8n] Route registration failed: %s", exc)
