"""
Cookie-Fooocus — Performance Telemetry
────────────────────────────────────────────────────────────────────────────────
Lightweight, thread-safe telemetry layer that tracks timing, counters, and
threshold alerts for every stage in the generation pipeline.

Metrics tracked:
  prompt_expand_ms    — time in PromptEngine.run()
  safety_check_ms     — time in the 2-layer safety filter
  generation_ms       — time in SDXL diffusion
  nsfw_check_ms       — time in post-generation NSFW classifier
  queue_wait_ms       — time waiting for a generation queue slot
  vram_peak_mb        — peak VRAM during generation (if measurable)

Threshold monitoring:
  Call set_threshold(metric, p95_warn, p95_crit) to configure alert levels.
  A background thread checks thresholds every 30 seconds.
  On breach: log warning + increment counter + append to alerts list.

Auto-tune (opt-in, off by default):
  If safety_policy.json sets "telemetry": {"auto_tune": true}, the threshold
  monitor is allowed to call external callbacks (e.g. suggest step reduction).
  Without this flag, telemetry is purely passive — it records and alerts but
  NEVER modifies system behaviour.

  "Predictive systems may suggest.  Only validators may enforce."

Rule: telemetry never blocks execution.  All recording is non-locking writes.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("cookiefooocus.telemetry")

# Maximum number of samples kept per metric (rolling window)
_WINDOW_SIZE      = 100
# Minimum samples before threshold checks fire
_MIN_SAMPLES      = 5
# How often the background monitor checks thresholds (seconds)
_MONITOR_INTERVAL = 30
# Maximum alerts kept in memory
_MAX_ALERTS       = 100


# ═══════════════════════════════════════════════════════════════════════════════
#  Config helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _load_telemetry_config() -> dict:
    try:
        path = Path(__file__).parent.parent / "safety_policy.json"
        with open(path) as f:
            return json.load(f).get("telemetry", {})
    except Exception:
        return {}


def _auto_tune_enabled() -> bool:
    """Returns True only if safety_policy.json explicitly sets auto_tune: true."""
    return bool(_load_telemetry_config().get("auto_tune", False))


# ═══════════════════════════════════════════════════════════════════════════════
#  Rolling statistics
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class _RollingStats:
    """Maintains a rolling window of float samples with percentile support."""
    _samples: deque = field(default_factory=lambda: deque(maxlen=_WINDOW_SIZE))
    _lock:    threading.Lock = field(default_factory=threading.Lock)

    def add(self, value: float) -> None:
        with self._lock:
            self._samples.append(value)

    @staticmethod
    def _percentile(sorted_vals: list, p: float) -> float:
        if not sorted_vals:
            return 0.0
        k  = (len(sorted_vals) - 1) * p / 100.0
        lo = int(k)
        hi = min(lo + 1, len(sorted_vals) - 1)
        return round(sorted_vals[lo] * (1 - (k - lo)) + sorted_vals[hi] * (k - lo), 2)

    def snapshot(self) -> dict:
        with self._lock:
            if not self._samples:
                return {"avg": 0.0, "min": 0.0, "max": 0.0, "p50": 0.0, "p95": 0.0, "count": 0}
            samples = sorted(self._samples)
        return {
            "avg":   round(sum(samples) / len(samples), 2),
            "min":   round(samples[0], 2),
            "max":   round(samples[-1], 2),
            "p50":   self._percentile(samples, 50),
            "p95":   self._percentile(samples, 95),
            "count": len(samples),
        }


# ═══════════════════════════════════════════════════════════════════════════════
#  Timer context manager
# ═══════════════════════════════════════════════════════════════════════════════

class _Timer:
    def __init__(self, telemetry: "Telemetry", metric: str):
        self._telemetry = telemetry
        self._metric    = metric
        self._t0: Optional[float] = None

    def __enter__(self) -> "_Timer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *_) -> None:
        if self._t0 is not None:
            self._telemetry.record(self._metric, (time.perf_counter() - self._t0) * 1000)


# ═══════════════════════════════════════════════════════════════════════════════
#  Threshold config
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ThresholdConfig:
    """Warning and critical p95 thresholds for a single metric."""
    p95_warn:  float   # ms or MB — logs warning + increments counter
    p95_crit:  float   # ms or MB — logs critical + increments counter


# ═══════════════════════════════════════════════════════════════════════════════
#  Telemetry class
# ═══════════════════════════════════════════════════════════════════════════════

class Telemetry:
    """
    Thread-safe telemetry collector with threshold monitoring.

    Typical usage:
        with telemetry.timer("generation_ms"):
            image = sdxl.run(...)

        telemetry.record("vram_peak_mb", peak_mb)
        print(telemetry.dashboard())

    Threshold monitoring:
        # Configured by default for key metrics.  Runs in background.
        telemetry.set_threshold("generation_ms", p95_warn=6000, p95_crit=10000)

        # Read recent alerts:
        alerts = telemetry.get_alerts()
    """

    def __init__(self):
        self._metrics:  dict[str, _RollingStats] = defaultdict(_RollingStats)
        self._lock      = threading.Lock()
        self._counters: dict[str, int]   = defaultdict(int)
        self._active:   dict[str, float] = {}
        self._active_lock = threading.Lock()

        # Threshold state
        self._thresholds: dict[str, ThresholdConfig] = {}
        self._threshold_lock = threading.Lock()
        self._alerts: list[dict] = []

        # Auto-tune callbacks — only called when auto_tune is enabled in config
        # Signature: callback(metric: str, p95: float, level: str) -> None
        self._auto_tune_callbacks: list[Callable] = []
        self._callback_lock = threading.Lock()

        # Install sensible defaults
        self._install_default_thresholds()
        self._start_threshold_monitor()

    # ── Recording ─────────────────────────────────────────────────────────────

    def record(self, metric: str, value: float) -> None:
        """Record a single value for a metric.  Non-blocking."""
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
        """Mark end; records elapsed ms.  Returns elapsed ms."""
        with self._active_lock:
            t0 = self._active.pop(metric, None)
        if t0 is None:
            log.debug("[telemetry] end() without matching start() for '%s'", metric)
            return 0.0
        elapsed_ms = (time.perf_counter() - t0) * 1000
        self.record(metric, elapsed_ms)
        return elapsed_ms

    def timer(self, metric: str) -> _Timer:
        """Context manager for timing a block of code."""
        return _Timer(self, metric)

    def record_vram(self) -> Optional[float]:
        """
        Sample current peak VRAM and record as 'vram_peak_mb'.
        Returns the sampled value, or None if unavailable.
        Call from inside a generation slot for meaningful readings.
        """
        try:
            import torch
            if torch.cuda.is_available():
                peak_bytes = torch.cuda.max_memory_allocated()
                torch.cuda.reset_peak_memory_stats()
                mb = peak_bytes / (1024 ** 2)
                self.record("vram_peak_mb", mb)
                return mb
        except Exception:
            pass
        return None

    # ── Snapshots ─────────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """Return current stats for all metrics and counters."""
        with self._lock:
            counters = dict(self._counters)
        return {
            "metrics":  {k: v.snapshot() for k, v in self._metrics.items()},
            "counters": counters,
        }

    def dashboard(self) -> str:
        """Human-readable performance dashboard."""
        snap  = self.snapshot()
        lines = ["── Performance Dashboard ──────────────────────────────────────"]
        for metric, stats in snap["metrics"].items():
            if stats["count"] == 0:
                continue
            label = metric.replace("_", " ").title()
            unit  = "MB" if "mb" in metric else "ms"
            lines.append(
                f"  {label:<28}  avg {stats['avg']:>8.1f}{unit}"
                f"  p50 {stats.get('p50', 0):>8.1f}"
                f"  p95 {stats.get('p95', 0):>8.1f}"
                f"  max {stats['max']:>8.1f}  n={stats['count']}"
            )
        if snap["counters"]:
            lines.append("── Counters ────────────────────────────────────────────────────")
            for name, value in snap["counters"].items():
                lines.append(f"  {name:<36} {value}")
        recent = self.get_alerts(limit=3)
        if recent:
            lines.append("── Recent Alerts ───────────────────────────────────────────────")
            for a in recent:
                lines.append(
                    f"  [{a['level'].upper():<8}] {a['metric']}  p95={a['p95']:.0f}  "
                    f"threshold={a['threshold']:.0f}"
                )
        lines.append("────────────────────────────────────────────────────────────────")
        return "\n".join(lines)

    # ── Threshold monitoring ──────────────────────────────────────────────────

    def set_threshold(
        self,
        metric:   str,
        p95_warn: float,
        p95_crit: float,
    ) -> None:
        """Configure alert thresholds for a metric."""
        with self._threshold_lock:
            self._thresholds[metric] = ThresholdConfig(p95_warn=p95_warn, p95_crit=p95_crit)

    def get_alerts(self, limit: int = 20) -> list[dict]:
        """Return the most recent threshold breach alerts."""
        with self._threshold_lock:
            return list(self._alerts[-limit:])

    def register_auto_tune_callback(self, callback: Callable) -> None:
        """
        Register a callback that fires on threshold breaches when auto_tune is enabled.
        Signature: callback(metric: str, p95: float, level: str) -> None

        The callback is ONLY called when safety_policy.json has:
            "telemetry": {"auto_tune": true}

        Without that flag this method registers the callback but it will never fire.
        """
        with self._callback_lock:
            self._auto_tune_callbacks.append(callback)

    def _check_thresholds(self) -> None:
        """Run one round of threshold checks.  Called by background monitor."""
        snap = self.snapshot()
        with self._threshold_lock:
            thresholds = dict(self._thresholds)

        auto_tune = _auto_tune_enabled()

        for metric, cfg in thresholds.items():
            stats = snap["metrics"].get(metric, {})
            p95   = stats.get("p95", 0.0)
            count = stats.get("count", 0)

            if count < _MIN_SAMPLES:
                continue

            level: Optional[str] = None
            threshold_value: float = 0.0

            if p95 >= cfg.p95_crit:
                level, threshold_value = "critical", cfg.p95_crit
            elif p95 >= cfg.p95_warn:
                level, threshold_value = "warning",  cfg.p95_warn

            if level:
                alert = {
                    "metric":    metric,
                    "level":     level,
                    "p95":       p95,
                    "threshold": threshold_value,
                    "ts":        time.time(),
                }
                with self._threshold_lock:
                    self._alerts.append(alert)
                    if len(self._alerts) > _MAX_ALERTS:
                        self._alerts = self._alerts[-_MAX_ALERTS:]

                self.increment(f"threshold_{level}_{metric}")
                log.warning(
                    "[telemetry] %s threshold breach: %s p95=%.0f >= %.0f",
                    level.upper(), metric, p95, threshold_value,
                )

                # Auto-tune callbacks — only fire when explicitly enabled
                if auto_tune:
                    with self._callback_lock:
                        callbacks = list(self._auto_tune_callbacks)
                    for cb in callbacks:
                        try:
                            cb(metric, p95, level)
                        except Exception as exc:
                            log.debug("[telemetry] Auto-tune callback error: %s", exc)
                else:
                    log.debug(
                        "[telemetry] Auto-tune disabled — breach recorded but "
                        "no behaviour change triggered for %s.", metric,
                    )

    def _start_threshold_monitor(self) -> None:
        def _loop():
            while True:
                time.sleep(_MONITOR_INTERVAL)
                try:
                    self._check_thresholds()
                except Exception as exc:
                    log.debug("[telemetry] Threshold monitor error: %s", exc)

        threading.Thread(
            target=_loop, daemon=True, name="telemetry-threshold-monitor"
        ).start()

    def _install_default_thresholds(self) -> None:
        """Install sensible defaults for known pipeline metrics."""
        self.set_threshold("generation_ms",   p95_warn=6_000,  p95_crit=10_000)
        self.set_threshold("safety_check_ms", p95_warn=200,    p95_crit=500)
        self.set_threshold("nsfw_check_ms",   p95_warn=500,    p95_crit=1_000)
        self.set_threshold("queue_wait_ms",   p95_warn=30_000, p95_crit=60_000)
        self.set_threshold("vram_peak_mb",    p95_warn=7_500,  p95_crit=9_500)

    # ── Maintenance ───────────────────────────────────────────────────────────

    def reset(self) -> None:
        """Clear all metrics and counters.  Useful between test runs."""
        with self._lock:
            self._metrics.clear()
            self._counters.clear()
        with self._active_lock:
            self._active.clear()
        with self._threshold_lock:
            self._alerts.clear()


# Singleton — import and use directly
telemetry = Telemetry()
