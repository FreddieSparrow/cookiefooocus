"""
Cookie-Fooocus — Generation Controller
────────────────────────────────────────────────────────────────────────────────
Single authority for everything that surrounds a generation request:

  • Queue        — one slot per GPU, priority levels, OOM prevention
  • Cache        — single unified LRU for prompt expansions and embeddings
  • VRAM budget  — read-once hardware profile, consumed by callers
  • Telemetry    — delegates to modules.telemetry for timing/metrics

This module replaces the scattered caching in performance.py, the expansion
cache in extras/expansion.py, and the ad-hoc queue in async_worker.py.
Those remain in place for backwards compatibility; this controller is the
preferred entry point for new code.

Priority levels (lower number = higher priority):
  0 — user foreground request
  1 — batch / background generation
  2 — background checks (NSFW, pattern analysis)

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import hashlib
import heapq
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Optional

log = logging.getLogger("cookiefooocus.generation_controller")


# ═══════════════════════════════════════════════════════════════════════════════
#  Unified LRU cache
# ═══════════════════════════════════════════════════════════════════════════════

class UnifiedCache:
    """
    Single global LRU cache for:
      - prompt → expanded text           (namespace "prompt")
      - prompt → CLIP embedding          (namespace "embed")
      - image_path → NSFW float score    (namespace "nsfw")

    All namespaces share one maxsize budget.  Keys are namespaced internally.
    Thread-safe.
    """

    def __init__(self, maxsize: int = 512):
        self._store:   OrderedDict[str, Any] = OrderedDict()
        self._maxsize  = maxsize
        self._lock     = threading.Lock()
        self._hits     = 0
        self._misses   = 0

    def _key(self, namespace: str, *parts: str) -> str:
        raw = ":".join([namespace] + list(parts))
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def get(self, namespace: str, *parts: str) -> Optional[Any]:
        k = self._key(namespace, *parts)
        with self._lock:
            if k in self._store:
                self._store.move_to_end(k)
                self._hits += 1
                return self._store[k]
            self._misses += 1
            return None

    def put(self, value: Any, namespace: str, *parts: str) -> None:
        k = self._key(namespace, *parts)
        with self._lock:
            if k in self._store:
                self._store.move_to_end(k)
            else:
                if len(self._store) >= self._maxsize:
                    self._store.popitem(last=False)
            self._store[k] = value

    def invalidate(self, namespace: str, *parts: str) -> None:
        k = self._key(namespace, *parts)
        with self._lock:
            self._store.pop(k, None)

    def clear(self, namespace: Optional[str] = None) -> None:
        with self._lock:
            if namespace is None:
                self._store.clear()
                self._hits = self._misses = 0
            else:
                prefix = namespace + ":"
                keys = [k for k in self._store if k.startswith(prefix)]
                for k in keys:
                    del self._store[k]

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits":      self._hits,
                "misses":    self._misses,
                "hit_rate":  round(self._hits / total, 3) if total else 0.0,
                "size":      len(self._store),
                "capacity":  self._maxsize,
            }


# ═══════════════════════════════════════════════════════════════════════════════
#  Priority queue slot manager
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(order=True)
class _QueueEntry:
    priority:   int
    seq:        int          # tie-break — FIFO within same priority
    task_id:    str = field(compare=False)
    semaphore:  threading.Event = field(compare=False)


class PriorityGenerationQueue:
    """
    Priority-aware generation queue with a single GPU slot.

    Priorities:
      0 — user foreground request   (highest)
      1 — batch / background job
      2 — background check          (lowest)

    Usage:
        with queue.slot(priority=0, task_id="req-42"):
            run_sdxl(...)
    """

    def __init__(self, max_concurrent: int = 1):
        self._max_concurrent = max_concurrent
        self._active         = 0
        self._heap: list[_QueueEntry] = []
        self._lock           = threading.Lock()
        self._seq            = 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def acquire(self, priority: int = 0, task_id: str = "unknown", timeout: float = 600.0) -> bool:
        """
        Block until a generation slot is available.
        Returns True if acquired, False if timeout exceeded.
        """
        event = threading.Event()
        entry = _QueueEntry(priority=priority, seq=self._next_seq(),
                            task_id=task_id, semaphore=event)

        with self._lock:
            heapq.heappush(self._heap, entry)
            self._try_dispatch()

        acquired = event.wait(timeout=timeout)
        if not acquired:
            with self._lock:
                try:
                    self._heap.remove(entry)
                    import heapq as hq
                    hq.heapify(self._heap)
                except ValueError:
                    pass
            log.warning("[queue] Task %s timed out waiting for a slot.", task_id)
        return acquired

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)
            self._try_dispatch()

    def _try_dispatch(self) -> None:
        """Must be called with self._lock held."""
        while self._heap and self._active < self._max_concurrent:
            entry = heapq.heappop(self._heap)
            self._active += 1
            entry.semaphore.set()

    def stats(self) -> dict:
        with self._lock:
            return {
                "active":  self._active,
                "waiting": len(self._heap),
            }

    class _Slot:
        def __init__(self, queue: "PriorityGenerationQueue", priority: int, task_id: str):
            self._queue    = queue
            self._priority = priority
            self._task_id  = task_id

        def __enter__(self):
            if not self._queue.acquire(self._priority, self._task_id):
                raise TimeoutError(f"Queue slot timeout for task {self._task_id}")
            return self

        def __exit__(self, *_):
            self._queue.release()

    def slot(self, priority: int = 0, task_id: str = "unknown") -> "_Slot":
        """Context manager that acquires and releases a generation slot."""
        return self._Slot(self, priority, task_id)


# ═══════════════════════════════════════════════════════════════════════════════
#  VRAM / hardware profile (read once)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class HardwareProfile:
    vram_gb:           float
    ram_gb:            float
    is_apple_silicon:  bool
    optimal_batch:     int
    has_gpu:           bool


@lru_cache(maxsize=1)
def get_hardware_profile() -> HardwareProfile:
    """Read hardware spec once and cache forever.  Never call torch at import time."""
    try:
        from modules.hardware_check import _get_vram_gb, _get_total_ram_gb, _is_apple_silicon
        vram = _get_vram_gb()
        ram  = _get_total_ram_gb()
        apple = _is_apple_silicon()
    except Exception:
        vram, ram, apple = 0.0, 0.0, False

    has_gpu = vram > 0 or apple

    if apple:
        batch = 4 if ram >= 64 else (2 if ram >= 32 else 1)
    elif vram >= 24:
        batch = 4
    elif vram >= 16:
        batch = 2
    else:
        batch = 1

    return HardwareProfile(
        vram_gb=vram,
        ram_gb=ram,
        is_apple_silicon=apple,
        optimal_batch=batch,
        has_gpu=has_gpu,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Generation Controller (the public façade)
# ═══════════════════════════════════════════════════════════════════════════════

class GenerationController:
    """
    Central co-ordinator for all generation infrastructure.

    Attributes:
        cache    — unified LRU cache (prompts, embeddings, NSFW scores)
        queue    — priority generation queue
        hardware — read-once hardware profile

    Typical usage:
        ctrl = GenerationController()

        # Expand a prompt (cache-aware)
        expanded = ctrl.expand_prompt(prompt, seed, mode)

        # Generate with queue + telemetry
        with ctrl.queue.slot(priority=0, task_id="user-1"):
            ctrl.telemetry.start("generation")
            image = sdxl.run(expanded)
            ctrl.telemetry.end("generation")
    """

    def __init__(self, cache_size: int = 512, max_concurrent: int = 1):
        self.cache    = UnifiedCache(maxsize=cache_size)
        self.queue    = PriorityGenerationQueue(max_concurrent=max_concurrent)
        self.hardware = get_hardware_profile()

        try:
            from modules.telemetry import Telemetry
            self.telemetry = Telemetry()
        except ImportError:
            self.telemetry = None

        log.info(
            "[ctrl] Initialised.  VRAM=%.1fGB RAM=%.1fGB Apple=%s Batch=%d",
            self.hardware.vram_gb, self.hardware.ram_gb,
            self.hardware.is_apple_silicon, self.hardware.optimal_batch,
        )

    def expand_prompt(
        self,
        prompt: str,
        seed:   int,
        mode:   str = "balanced",
    ) -> str:
        """
        Expand prompt using the PromptEngine, with cache.
        mode: 'raw' | 'balanced' | 'llm'
        Returns the expanded string.
        """
        # Check cache first
        cached = self.cache.get("prompt", prompt, str(seed), mode)
        if cached is not None:
            log.debug("[ctrl] Prompt cache hit.")
            return cached

        t0 = time.perf_counter()
        try:
            from modules.prompt_engine import engine, PromptEngine
            pm = PromptEngine.mode_from_string(mode)
            result = engine.run(prompt, seed=seed, mode=pm)
            expanded = result.expanded
        except Exception as exc:
            log.warning("[ctrl] PromptEngine failed (%s) — using raw prompt.", exc)
            expanded = prompt

        elapsed = time.perf_counter() - t0
        self.cache.put(expanded, "prompt", prompt, str(seed), mode)

        if self.telemetry:
            self.telemetry.record("prompt_expand_ms", elapsed * 1000)

        return expanded

    def check_nsfw_cached(self, image_path: str) -> Optional[float]:
        """Return cached NSFW score for an image, or None if not cached."""
        return self.cache.get("nsfw", image_path)

    def store_nsfw_score(self, image_path: str, score: float) -> None:
        """Store NSFW score in the unified cache."""
        self.cache.put(score, "nsfw", image_path)

    def status(self) -> dict:
        """Full status snapshot for dashboards and health checks."""
        return {
            "cache":    self.cache.stats(),
            "queue":    self.queue.stats(),
            "hardware": {
                "vram_gb":          self.hardware.vram_gb,
                "ram_gb":           self.hardware.ram_gb,
                "is_apple_silicon": self.hardware.is_apple_silicon,
                "optimal_batch":    self.hardware.optimal_batch,
            },
            "telemetry": self.telemetry.snapshot() if self.telemetry else {},
        }


# Singleton — import and use directly
controller = GenerationController()
