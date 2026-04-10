"""
Cookie-Fooocus Observability Module
────────────────────────────────────
Structured JSON event logging, metrics, and decision tracing.

All logs are written locally — no telemetry, no network calls.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from modules.observability.structured_log import (
    log_decision,
    log_error,
    log_metric,
    get_metrics_snapshot,
    ObservabilityEvent,
)

__all__ = [
    "log_decision",
    "log_error",
    "log_metric",
    "get_metrics_snapshot",
    "ObservabilityEvent",
]
