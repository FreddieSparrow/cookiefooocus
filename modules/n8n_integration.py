"""
Cookie-Fooocus — n8n Integration (hardened)
────────────────────────────────────────────────────────────────────────────────
Connects Cookie-Fooocus to n8n workflows via signed webhooks.

Security model
─────────────────────────────────────────────────────────────────────────────
  HMAC-SHA256 signature   — every request signed with a shared secret
                            (not a static token comparison — replay-safe)
  Timestamp validation    — requests older than ±300s are rejected
  Nonce store             — each nonce accepted exactly once (replay protection)
  Payload size cap        — 64KB max body (prevents memory exhaustion)
  Schema validation       — strict field whitelist (prevents injection via JSON)
  Per-origin rate limit   — 30 requests / 60s per source IP
  Cost validation         — pixel budget + step cap before job reaches queue

Signing protocol (n8n → CF)
─────────────────────────────────────────────────────────────────────────────
  1. Build JSON body.
  2. Compute: signature = HMAC-SHA256(key=secret, msg=f"{timestamp}:{nonce}:{body}")
  3. Send headers:
       X-CF-Timestamp:  <unix epoch int>
       X-CF-Nonce:      <random 16-byte hex>
       X-CF-Signature:  <hex digest>

  In n8n: use the "Code" node to sign, or the HTTP Request node with
  expression-based headers.  See readme.md for full n8n template.

  For simple local setups where signing is too complex, fall back to
  setting `"simple_token_mode": true` in the n8n config block — this
  reverts to static-token auth (less secure, never use on public servers).

Config (safety_policy.json)
─────────────────────────────────────────────────────────────────────────────
  {
    "n8n": {
      "enabled":           true,
      "secret":            "your-32-char-or-longer-secret",
      "callback_url":      "https://your.n8n.cloud/webhook/cookiefooocus",
      "simple_token_mode": false,
      "max_payload_bytes": 65536,
      "rate_limit_rpm":    30,
      "max_pixels":        1048576,
      "max_steps_api":     60
    }
  }

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

log = logging.getLogger("cookiefooocus.n8n")

_MAX_PAYLOAD_BYTES_DEFAULT = 65_536   # 64 KB
_TIMESTAMP_TOLERANCE_S     = 300      # ±5 minutes
_NONCE_TTL_S               = 600      # keep nonces for 10 minutes
_RATE_WINDOW_S             = 60
_RATE_LIMIT_DEFAULT        = 30       # requests per window

# Cost limits — prevent valid signed requests from burning the GPU
_MAX_PIXELS_DEFAULT  = 1024 * 1024   # 1 MP (1024×1024 max per API request)
_MAX_STEPS_DEFAULT   = 60            # steps cap for API requests
_MAX_FRAMES_DEFAULT  = 96            # video: hard cap on total frames


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


def _cfg(key: str, default=None):
    return _load_n8n_config().get(key, default)


def _is_enabled() -> bool:
    return bool(_cfg("enabled", False))


def _get_secret() -> str:
    return str(_cfg("secret", _cfg("token", "")))  # "token" is legacy alias


def _get_callback_url() -> str:
    return str(_cfg("callback_url", ""))


def _simple_token_mode() -> bool:
    return bool(_cfg("simple_token_mode", False))


def _max_payload() -> int:
    return int(_cfg("max_payload_bytes", _MAX_PAYLOAD_BYTES_DEFAULT))


def _rate_limit_rpm() -> int:
    return int(_cfg("rate_limit_rpm", _RATE_LIMIT_DEFAULT))


def _max_pixels() -> int:
    return int(_cfg("max_pixels", _MAX_PIXELS_DEFAULT))


def _max_steps_api() -> int:
    return int(_cfg("max_steps_api", _MAX_STEPS_DEFAULT))


# ═══════════════════════════════════════════════════════════════════════════════
#  Nonce store (replay prevention)
# ═══════════════════════════════════════════════════════════════════════════════

class _NonceStore:
    """Thread-safe nonce store with automatic expiry."""

    def __init__(self, ttl: float = _NONCE_TTL_S):
        self._store: dict[str, float] = {}
        self._ttl   = ttl
        self._lock  = threading.Lock()

    def check_and_register(self, nonce: str) -> bool:
        """
        Return True if this nonce has not been seen before (and register it).
        Return False if nonce was already used (replay).
        """
        now = time.time()
        with self._lock:
            self._purge_expired(now)
            if nonce in self._store:
                return False
            self._store[nonce] = now + self._ttl
            return True

    def _purge_expired(self, now: float) -> None:
        expired = [k for k, exp in self._store.items() if exp <= now]
        for k in expired:
            del self._store[k]


_nonce_store = _NonceStore()


# ═══════════════════════════════════════════════════════════════════════════════
#  Per-origin rate limiter
# ═══════════════════════════════════════════════════════════════════════════════

class _RateLimiter:
    def __init__(self):
        self._windows: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, origin: str) -> bool:
        limit  = _rate_limit_rpm()
        window = _RATE_WINDOW_S
        now    = time.monotonic()
        with self._lock:
            q = self._windows[origin]
            while q and q[0] < now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


_rate_limiter = _RateLimiter()


# ═══════════════════════════════════════════════════════════════════════════════
#  Signature validation
# ═══════════════════════════════════════════════════════════════════════════════

def _compute_signature(secret: str, timestamp: str, nonce: str, body: bytes) -> str:
    """HMAC-SHA256(secret, f'{timestamp}:{nonce}:{body_hex}')"""
    msg = f"{timestamp}:{nonce}:".encode() + body
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def validate_request(
    body:      bytes,
    timestamp: str,
    nonce:     str,
    signature: str,
    origin:    str = "unknown",
) -> Tuple[bool, str]:
    """
    Full validation: rate limit → size → timestamp → nonce → HMAC.
    Returns (ok: bool, reason: str).
    """
    secret = _get_secret()

    # Rate limit
    if not _rate_limiter.allow(origin):
        return False, "rate_limit_exceeded"

    # Payload size (checked before this function, but guard again)
    if len(body) > _max_payload():
        return False, "payload_too_large"

    # Simple token mode (legacy / local use)
    if _simple_token_mode():
        ok = hmac.compare_digest(
            hashlib.sha256(signature.encode()).digest(),
            hashlib.sha256(secret.encode()).digest(),
        )
        return ok, ("ok" if ok else "invalid_token")

    # Timestamp check
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False, "invalid_timestamp"
    drift = abs(time.time() - ts)
    if drift > _TIMESTAMP_TOLERANCE_S:
        return False, f"timestamp_drift_{int(drift)}s"

    # Nonce (replay prevention)
    if not _nonce_store.check_and_register(nonce):
        return False, "replay_detected"

    # HMAC signature
    if not secret:
        return False, "no_secret_configured"
    expected = _compute_signature(secret, timestamp, nonce, body)
    if not hmac.compare_digest(expected, signature.lower()):
        return False, "invalid_signature"

    return True, "ok"


# ═══════════════════════════════════════════════════════════════════════════════
#  Outbound callback (CF → n8n) — signed
# ═══════════════════════════════════════════════════════════════════════════════

def send_callback(payload: dict, callback_url: Optional[str] = None) -> bool:
    """
    POST a signed result payload to the n8n callback URL.
    Runs in a daemon thread — never blocks generation.
    """
    url = callback_url or _get_callback_url()
    if not url:
        return False

    def _send():
        try:
            body      = json.dumps(payload).encode()
            secret    = _get_secret()
            timestamp = str(int(time.time()))
            nonce     = os.urandom(8).hex()
            sig       = _compute_signature(secret, timestamp, nonce, body)

            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type":    "application/json",
                    "X-CF-Timestamp":  timestamp,
                    "X-CF-Nonce":      nonce,
                    "X-CF-Signature":  sig,
                    "X-CF-Version":    _cf_version(),
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15):
                pass
            log.debug("[n8n] Signed callback sent to %s", url)
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
#  Request / response schema
# ═══════════════════════════════════════════════════════════════════════════════

# Strict allowlist of accepted fields — anything else is silently ignored
_ALLOWED_FIELDS = {
    "prompt", "negative_prompt", "seed", "steps", "cfg",
    "width", "height", "prompt_mode", "style", "callback_url", "job_id",
}

_REQUIRED_FIELDS = {"prompt"}


@dataclass
class WebhookRequest:
    prompt:          str
    negative_prompt: str   = ""
    seed:            int   = -1
    steps:           int   = 30
    cfg:             float = 7.0
    width:           int   = 1024
    height:          int   = 1024
    prompt_mode:     str   = "balanced"
    style:           str   = ""
    callback_url:    str   = ""
    job_id:          str   = ""


def parse_webhook_request(body: bytes) -> WebhookRequest:
    """
    Parse and strictly validate the JSON body.
    Only fields in _ALLOWED_FIELDS are accepted.
    Raises ValueError on missing required fields, type errors, or cost limit violations.
    """
    import random

    try:
        raw = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("Body must be a JSON object")

    # Check required fields
    missing = _REQUIRED_FIELDS - set(raw.keys())
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    # Whitelist-only extraction
    data = {k: v for k, v in raw.items() if k in _ALLOWED_FIELDS}

    # Parse width / height with hard caps
    width  = min(int(data.get("width",  1024)), 2048)
    height = min(int(data.get("height", 1024)), 2048)

    # Cost validation: pixel budget
    pixels = width * height
    max_px = _max_pixels()
    if pixels > max_px:
        limit_side = int(max_px ** 0.5)
        raise ValueError(
            f"Resolution {width}×{height} ({pixels:,} px) exceeds API limit "
            f"({max_px:,} px, ~{limit_side}×{limit_side}). Reduce width or height."
        )

    # Cost validation: step cap
    steps = min(int(data.get("steps", 30)), _max_steps_api())

    return WebhookRequest(
        prompt          = str(data.get("prompt", ""))[:2000],
        negative_prompt = str(data.get("negative_prompt", ""))[:500],
        seed            = int(data.get("seed", random.randint(0, 2**31))),
        steps           = steps,
        cfg             = min(float(data.get("cfg", 7.0)), 30.0),
        width           = width,
        height          = height,
        prompt_mode     = str(data.get("prompt_mode", "balanced")),
        style           = str(data.get("style", ""))[:100],
        callback_url    = str(data.get("callback_url", ""))[:500],
        job_id          = str(data.get("job_id", f"cf-{int(time.time())}"))[:64],
    )


def build_response(
    req:                WebhookRequest,
    status:             str,
    image_path:         Optional[str] = None,
    prompt_trace:       Optional[dict] = None,
    safety:             Optional[dict] = None,
    error_msg:          str = "",
    resource_adjustment: Optional[dict] = None,
) -> dict:
    resp: dict[str, Any] = {
        "job_id":       req.job_id,
        "status":       status,
        "prompt_trace": prompt_trace or {},
        "safety":       safety or {},
        "telemetry":    {},
    }

    if resource_adjustment:
        resp["resource_adjustment"] = resource_adjustment

    try:
        from modules.telemetry import telemetry
        resp["telemetry"] = telemetry.snapshot().get("metrics", {})
    except Exception:
        pass

    if status == "complete" and image_path:
        resp["image_path"] = image_path
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
#  Generation handler
# ═══════════════════════════════════════════════════════════════════════════════

def handle_generate_request(req: WebhookRequest) -> dict:
    """Safety → expand → resource check → queue → respond."""
    from modules.safety import check_prompt
    from modules.prompt_engine import engine, PromptEngine

    safety_result = check_prompt(req.prompt)
    safety_dict   = {
        "allowed":    safety_result.allowed,
        "decision":   safety_result.reason.decision.value,
        "layer":      safety_result.reason.layer,
        "rule":       safety_result.reason.rule,
        "confidence": safety_result.reason.confidence,
    }

    if not safety_result.allowed:
        return build_response(req, "blocked", safety=safety_dict)

    mode   = PromptEngine.mode_from_string(req.prompt_mode)
    result = engine.run(req.prompt, seed=req.seed, mode=mode)
    trace  = {
        "mode":            result.trace.mode.value,
        "mode_used":       result.trace.mode_used.value,
        "fallback_reason": result.trace.fallback_reason,
        "original":        result.trace.original,
        "added":           result.trace.added,
        "notes":           result.trace.notes,
    }

    # Resource pre-check: validate VRAM budget before the job hits the queue.
    # Reports any quality downscaling back to the caller so n8n workflows
    # can make informed decisions (e.g. skip post-processing if steps reduced).
    resource_adjustment = None
    try:
        from modules.generation_controller.resource_manager import governor, GenParams
        params = GenParams(width=req.width, height=req.height, steps=req.steps)
        ok, adjusted = governor.check_and_scale(params)
        if not ok:
            return build_response(
                req, "rejected",
                safety=safety_dict,
                error_msg="Insufficient VRAM — request exceeds available resources.",
            )
        if adjusted.downscaled:
            resource_adjustment = {
                "downscaled": True,
                "original": {
                    "steps":  params.steps,
                    "width":  params.width,
                    "height": params.height,
                },
                "final": {
                    "steps":     adjusted.steps,
                    "width":     adjusted.width,
                    "height":    adjusted.height,
                    "precision": adjusted.precision,
                },
                "note": "Quality was automatically reduced to fit available VRAM.",
            }
    except Exception as exc:
        log.debug("[n8n] Resource pre-check skipped: %s", exc)

    log.info("[n8n] Job %s queued. Prompt: %.60s", req.job_id, req.prompt)
    return build_response(
        req, "queued",
        prompt_trace=trace,
        safety=safety_dict,
        resource_adjustment=resource_adjustment,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Event hooks
# ═══════════════════════════════════════════════════════════════════════════════

class N8nEventHook:
    def __init__(self, callback_url: Optional[str] = None):
        self._url = callback_url or _get_callback_url()

    def on_blocked(self, prompt_hash: str, reason: dict) -> None:
        if not _is_enabled():
            return
        send_callback({"event": "blocked", "prompt_hash": prompt_hash, "reason": reason, "ts": time.time()}, self._url)

    def on_complete(self, job_id: str, image_path: str) -> None:
        if not _is_enabled():
            return
        resp = build_response(WebhookRequest(prompt="", job_id=job_id), "complete", image_path=image_path)
        resp["event"] = "complete"
        send_callback(resp, self._url)

    def on_queue_wait(self, job_id: str, wait_ms: float) -> None:
        if not _is_enabled() or wait_ms < 5000:
            return
        send_callback({"event": "queue_wait", "job_id": job_id, "wait_ms": wait_ms, "ts": time.time()}, self._url)


n8n_hook = N8nEventHook()


# ═══════════════════════════════════════════════════════════════════════════════
#  Gradio / FastAPI route registration
# ═══════════════════════════════════════════════════════════════════════════════

def register_routes(app) -> None:
    """
    Register n8n webhook routes on the FastAPI app backing Gradio.
    Call from webui.py after Gradio app is created:
        from modules.n8n_integration import register_routes
        register_routes(app)
    """
    if not _is_enabled():
        log.info("[n8n] Disabled — routes not registered.")
        return

    try:
        from fastapi import Request, Response

        @app.post("/cf/webhook/generate")
        async def webhook_generate(request: Request):
            origin = request.client.host if request.client else "unknown"

            # Size guard
            body = await request.body()
            if len(body) > _max_payload():
                return Response(
                    content=json.dumps({"error": "payload_too_large"}),
                    status_code=413,
                    media_type="application/json",
                )

            # Signature validation
            ts  = request.headers.get("X-CF-Timestamp", "")
            nc  = request.headers.get("X-CF-Nonce", "")
            sig = request.headers.get("X-CF-Signature", "")
            ok, reason = validate_request(body, ts, nc, sig, origin=origin)
            if not ok:
                log.warning("[n8n] Request rejected from %s: %s", origin, reason)
                return Response(
                    content=json.dumps({"error": "unauthorized", "reason": reason}),
                    status_code=401,
                    media_type="application/json",
                )

            # Parse & cost-validate
            try:
                req = parse_webhook_request(body)
            except ValueError as exc:
                return Response(
                    content=json.dumps({"error": f"invalid_payload: {exc}"}),
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
                    "status":          "ok",
                    "version":         _cf_version(),
                    "n8n":             "enabled",
                    "signing_mode":    "simple_token" if _simple_token_mode() else "hmac_sha256",
                    "limits": {
                        "max_pixels":   _max_pixels(),
                        "max_steps":    _max_steps_api(),
                        "max_frames":   _MAX_FRAMES_DEFAULT,
                    },
                }),
                media_type="application/json",
            )

        log.info("[n8n] Routes registered: POST /cf/webhook/generate, GET /cf/webhook/status")

    except ImportError:
        log.warning("[n8n] FastAPI not available — routes not registered.")
    except Exception as exc:
        log.warning("[n8n] Route registration failed: %s", exc)
