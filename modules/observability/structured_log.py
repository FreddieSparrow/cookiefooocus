"""
Cookie-Fooocus — Structured Observability Logger
──────────────────────────────────────────────────
Emits structured JSON events for every filter decision, error, and metric.
All data is written locally — no network calls.

Log format (one JSON object per line, JSONL):
  {
    "ts":        "2025-01-01T00:00:00.000Z",
    "event":     "decision",
    "module":    "moderation",
    "decision":  "block",
    "reasons":   ["adult_filter"],
    "score":     0.0,
    "category":  "adult-nudity",
    "user_hash": "a1b2c3d4",
    "trace":     [...]           <- only if debug_trace=true
  }

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import hashlib
import json
import logging
import threading
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("cookiefooocus.observability")

# ── Log paths ──────────────────────────────────────────────────────────────────
_BASE_DIR   = Path.home() / ".local" / "share" / "cookiefooocus"
_OBS_LOG    = _BASE_DIR / "observability.jsonl"
_METRICS_DB = _BASE_DIR / "metrics.json"
_obs_lock   = threading.Lock()
_met_lock   = threading.Lock()


# ── In-memory metrics counters ─────────────────────────────────────────────────
_metrics: dict[str, int] = defaultdict(int)


@dataclass
class ObservabilityEvent:
    event:     str                     # "decision" | "error" | "metric"
    module:    str                     # "moderation" | "security" | "pipeline"
    decision:  str          = ""       # "allow" | "block" | "warn"
    reasons:   list[str]    = field(default_factory=list)
    score:     float        = 0.0
    category:  str          = ""
    user_hash: str          = ""       # truncated SHA-256 of user_id
    trace:     list[dict]   = field(default_factory=list)
    extra:     dict         = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _user_hash(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()[:12]


def _write(event_dict: dict) -> None:
    """Append one JSON line to the observability log (thread-safe)."""
    try:
        _OBS_LOG.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event_dict, default=str) + "\n"
        with _obs_lock:
            with _OBS_LOG.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception as exc:
        log.debug("[obs] Write failed: %s", exc)


def _flush_metrics() -> None:
    """Persist in-memory counters to metrics.json (best-effort)."""
    try:
        _METRICS_DB.parent.mkdir(parents=True, exist_ok=True)
        with _met_lock:
            snapshot = dict(_metrics)
        _METRICS_DB.write_text(json.dumps(snapshot, indent=2))
    except Exception as exc:
        log.debug("[obs] Metrics flush failed: %s", exc)


# ── Public API ─────────────────────────────────────────────────────────────────

def log_decision(
    *,
    module:    str,
    decision:  str,
    reasons:   list[str],
    score:     float      = 0.0,
    category:  str        = "",
    user_id:   str        = "anonymous",
    trace:     list[dict] = None,
    extra:     dict       = None,
) -> None:
    """
    Log a filter decision (allow/block/warn) as a structured JSON event.

    Example output:
      {"ts":"...","event":"decision","module":"moderation","decision":"block",
       "reasons":["adult_filter"],"score":0.0,"category":"adult-nudity",
       "user_hash":"a1b2c3d4"}
    """
    # Update in-memory counters
    with _met_lock:
        _metrics[f"decision.{decision}"] += 1
        _metrics[f"module.{module}.{decision}"] += 1
        if category:
            _metrics[f"category.{category}"] += 1

    evt = {
        "ts":        _now(),
        "event":     "decision",
        "module":    module,
        "decision":  decision,
        "reasons":   reasons,
        "score":     round(score, 4),
        "category":  category,
        "user_hash": _user_hash(user_id),
    }
    if trace:
        evt["trace"] = trace
    if extra:
        evt.update(extra)

    _write(evt)

    # Flush metrics every 50 events (cheap enough)
    if sum(_metrics.values()) % 50 == 0:
        threading.Thread(target=_flush_metrics, daemon=True).start()


def log_error(
    *,
    module:  str,
    error:   str,
    context: str = "",
) -> None:
    """Log a module-level error."""
    with _met_lock:
        _metrics[f"error.{module}"] += 1

    _write({
        "ts":      _now(),
        "event":   "error",
        "module":  module,
        "error":   error,
        "context": context,
    })


def log_metric(key: str, value: int = 1) -> None:
    """Increment an arbitrary named counter."""
    with _met_lock:
        _metrics[key] += value


def get_metrics_snapshot() -> dict[str, int]:
    """Return a copy of current in-memory counters (for CLI/debug)."""
    with _met_lock:
        return dict(_metrics)
