"""
Cookie-Fooocus — Performance Telemetry
────────────────────────────────────────────────────────────────────────────────
Lightweight, thread-safe telemetry layer that tracks timing and counters for
every stage in the generation pipeline.

Metrics tracked:
  prompt_expand_ms    — time spent in PromptEngine.run()
  safety_check_ms     — time spent in the 2-layer safety filter
  generation_ms       — time spent in SDXL diffusion
  nsfw_check_ms       — time spent in post-generation NSFW classifier
  queue_wait_ms       — time spent waiting for a generation queue slot
  cache_hit_rate      — ratio of cache hits to total lookups
  vram_peak_mb        — peak VRAM usage during generation (if measurable)

Usage:
    from modules.telemetry import telemetry

    telemetry.start("generation")
    # ... run SDXL ...
    telemetry.end("generation")

    snapshot = telemetry.snapshot()
    # → {"generation_ms": {"avg": 4200, "min": 3800, "max": 5100, "count": 12}}

Dashboard output (human-readable):
    print(telemetry.dashboard())

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("cookiefooocus.telemetry")

# Maximum number of samples kept per metric (rolling window)
_WINDOW_SIZE = 100


# ═══════════════════════════════════════════════════════════════════════════════
#  Rolling statistics
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class _RollingStats:
    """Maintains a rolling window of float samples."""
    _samples: deque = field(default_factory=lambda: deque(maxlen=_WINDOW_SIZE))
    _lock:    threading.Lock = field(default_factory=threading.Lock)

    def add(self, value: float) -> None:
        with self._lock:
            self._samples.append(value)

    def snapshot(self) -> dict:
        with self._lock:
            if not self._samples:
                return {"avg": 0.0, "min": 0.0, "max": 0.0, "count": 0}
            samples = list(self._samples)
        return {
            "avg":   round(sum(samples) / len(samples), 2),
            "min":   round(min(samples), 2),
            "max":   round(max(samples), 2),
            "count": len(samples),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  Timer context manager
# ═══════════════════════════════════════════════════════════════════════════════

class _Timer:
    """Context manager returned by Telemetry.timer()."""

    def __init__(self, telemetry: "Telemetry", metric: str):
        self._telemetry = telemetry
        self._metric    = metric
        self._t0: Optional[float] = None

    def __enter__(self) -> "_Timer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *_) -> None:
        if self._t0 is not None:
            elapsed_ms = (time.perf_counter() - self._t0) * 1000
            self._telemetry.record(self._metric, elapsed_ms)


# ═══════════════════════════════════════════════════════════════════════════════
#  Telemetry class
# ═══════════════════════════════════════════════════════════════════════════════

class Telemetry:
    """
    Thread-safe telemetry collector.

    Typical usage:
        with telemetry.timer("generation_ms"):
            image = sdxl.run(...)

        telemetry.record("cache_hit_rate", 0.72)

        print(telemetry.dashboard())
    """

    def __init__(self):
        self._metrics: dict[str, _RollingStats] = defaultdict(_RollingStats)
        self._lock     = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._active:   dict[str, float] = {}   # metric → start time (for start/end API)
        self._active_lock = threading.Lock()

    def record(self, metric: str, value: float) -> None:
        """Record a single value for a metric."""
        self._metrics[metric].add(value)

    def increment(self, counter: str, by: int = 1) -> None:
        """Increment a named counter (e.g. 'blocked_prompts')."""
        with self._lock:
            self._counters[counter] += by

    def start(self, metric: str) -> None:
        """Mark start of a timed operation.  Pair with end()."""
        with self._active_lock:
            self._active[metric] = time.perf_counter()

    def end(self, metric: str) -> float:
        """
        Mark end of a timed operation started with start().
        Records elapsed milliseconds.  Returns elapsed ms.
        """
        with self._active_lock:
            t0 = self._active.pop(metric, None)
        if t0 is None:
            log.debug("[telemetry] end() called without matching start() for '%s'", metric)
            return 0.0
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.record(metric, elapsed_ms)
        return elapsed_ms

    def timer(self, metric: str) -> _Timer:
        """Context manager for timing a block of code."""
        return _Timer(self, metric)

    def snapshot(self) -> dict:
        """Return current stats for all metrics and counters."""
        with self._lock:
            counters = dict(self._counters)
        return {
            "metrics":  {k: v.snapshot() for k, v in self._metrics.items()},
            "counters": counters,
        }

    def dashboard(self) -> str:
        """Return a human-readable performance dashboard string."""
        snap = self.snapshot()
        lines = ["── Performance Dashboard ──────────────────────"]
        for metric, stats in snap["metrics"].items():
            if stats["count"] == 0:
                continue
            label = metric.replace("_", " ").title()
            lines.append(
                f"  {label:<28} avg {stats['avg']:>8.1f}  "
                f"min {stats['min']:>8.1f}  max {stats['max']:>8.1f}  "
                f"n={stats['count']}"
            )
        if snap["counters"]:
            lines.append("── Counters ───────────────────────────────────")
            for name, value in snap["counters"].items():
                lines.append(f"  {name:<32} {value}")
        lines.append("────────────────────────────────────────────────")
        return "\n".join(lines)

    def reset(self) -> None:
        """Clear all metrics and counters (useful between test runs)."""
        with self._lock:
            self._metrics.clear()
            self._counters.clear()
        with self._active_lock:
            self._active.clear()


# Singleton — import and use directly
telemetry = Telemetry()
