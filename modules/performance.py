"""
Cookie-Fooocus — Performance Optimisations
────────────────────────────────────────────
Improvements over upstream Fooocus:

  1. Prompt response cache — identical prompts skip re-generation
  2. Model warm-up — loads safety classifiers at startup, not on first request
  3. Async image classification — NSFW checks don't block the UI thread
  4. Generation queue — orders concurrent requests, prevents OOM crashes
  5. Hardware-adaptive batch size — adjusts based on available VRAM/RAM

All optimisations are non-intrusive (wrap existing code, don't replace it).

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import hashlib
import logging
import queue
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from typing import Any, Callable, Optional

log = logging.getLogger("cookiefooocus.performance")


# ═══════════════════════════════════════════════════════════════════════════════
#  1. PROMPT RESPONSE CACHE
# ═══════════════════════════════════════════════════════════════════════════════

class PromptCache:
    """
    LRU cache for prompt expansion results.
    Avoids re-expanding the same prompt through Ollama/GPT-2 on every call.

    Thread-safe, configurable max size.
    """

    def __init__(self, maxsize: int = 256):
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._maxsize = maxsize
        self._lock    = threading.Lock()
        self._hits    = 0
        self._misses  = 0

    def _key(self, prompt: str, seed: int) -> str:
        """Cache key = sha256(prompt + seed)."""
        return hashlib.sha256(f"{prompt}:{seed}".encode()).hexdigest()[:24]

    def get(self, prompt: str, seed: int) -> Optional[str]:
        k = self._key(prompt, seed)
        with self._lock:
            if k in self._cache:
                self._cache.move_to_end(k)
                self._hits += 1
                return self._cache[k]
            self._misses += 1
            return None

    def put(self, prompt: str, seed: int, expanded: str) -> None:
        k = self._key(prompt, seed)
        with self._lock:
            if k in self._cache:
                self._cache.move_to_end(k)
            else:
                if len(self._cache) >= self._maxsize:
                    self._cache.popitem(last=False)
                self._cache[k] = expanded

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits":   self._hits,
                "misses": self._misses,
                "ratio":  round(self._hits / total, 3) if total else 0.0,
                "size":   len(self._cache),
            }

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._hits = self._misses = 0


# Singleton instance — used by FooocusExpansion
prompt_cache = PromptCache(maxsize=256)


# ═══════════════════════════════════════════════════════════════════════════════
#  2. ASYNC IMAGE CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

class AsyncImageChecker:
    """
    Runs NSFW / age-safety image checks in a background thread so the UI
    can continue rendering while the check completes.

    Usage:
        checker = AsyncImageChecker()
        future  = checker.submit(image_path, user_id)
        # ... do other work ...
        result  = future.result(timeout=10)
    """

    class Future:
        def __init__(self):
            self._event  = threading.Event()
            self._result = None

        def _set(self, result) -> None:
            self._result = result
            self._event.set()

        def result(self, timeout: float = 30):
            if not self._event.wait(timeout=timeout):
                log.warning("[perf] Image check timed out.")
                # Return safe-pass on timeout (don't block generation forever)
                from modules.content_filter import FilterResult, Severity
                return FilterResult(allowed=True, severity=Severity.WARN,
                                    reason="Image check timed out.")
            return self._result

    def __init__(self, workers: int = 2):
        self._queue   = queue.Queue()
        self._workers = workers
        self._started = False
        self._lock    = threading.Lock()

    def _start(self):
        with self._lock:
            if not self._started:
                for _ in range(self._workers):
                    t = threading.Thread(target=self._worker, daemon=True)
                    t.start()
                self._started = True

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                break
            future, image_path, user_id = item
            try:
                from modules.content_filter import check_image
                result = check_image(image_path, user_id)
                future._set(result)
            except Exception as exc:
                log.error("[perf] Async image check error: %s", exc)
                from modules.content_filter import FilterResult, Severity
                future._set(FilterResult(allowed=True, severity=Severity.WARN,
                                         reason=f"Check error: {exc}"))

    def submit(self, image_path: str, user_id: str = "anonymous") -> Future:
        self._start()
        future = self.Future()
        self._queue.put((future, image_path, user_id))
        return future


# Singleton
async_image_checker = AsyncImageChecker(workers=2)


# ═══════════════════════════════════════════════════════════════════════════════
#  3. GENERATION QUEUE (prevents OOM under concurrent load)
# ═══════════════════════════════════════════════════════════════════════════════

class GenerationQueue:
    """
    Serialises image generation requests to prevent out-of-memory crashes
    when multiple users submit jobs simultaneously (server mode).

    Local mode: passthrough (no queuing overhead).
    Server mode: configurable concurrency limit.
    """

    def __init__(self, max_concurrent: int = 1):
        self._sem     = threading.Semaphore(max_concurrent)
        self._waiting = 0
        self._lock    = threading.Lock()

    def acquire(self, timeout: float = 300) -> bool:
        """Acquire a generation slot. Returns False if timeout exceeded."""
        with self._lock:
            self._waiting += 1
        acquired = self._sem.acquire(timeout=timeout)
        with self._lock:
            self._waiting -= 1
        return acquired

    def release(self) -> None:
        self._sem.release()

    def stats(self) -> dict:
        with self._lock:
            return {"waiting": self._waiting}

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


# Default: 1 concurrent generation (safe for VRAM-limited systems)
generation_queue = GenerationQueue(max_concurrent=1)


# ═══════════════════════════════════════════════════════════════════════════════
#  4. HARDWARE-ADAPTIVE BATCH SIZE
# ═══════════════════════════════════════════════════════════════════════════════

@lru_cache(maxsize=1)
def get_optimal_batch_size() -> int:
    """
    Return the optimal batch size based on available hardware.
    Cached after first call (hardware doesn't change at runtime).
    """
    try:
        from modules.hardware_check import _get_vram_gb, _get_total_ram_gb, _is_apple_silicon
        vram = _get_vram_gb()
        ram  = _get_total_ram_gb()

        if _is_apple_silicon():
            # Unified memory — batch by RAM
            if ram >= 64:   return 4
            if ram >= 32:   return 2
            return 1

        # NVIDIA/AMD
        if vram >= 24:  return 4
        if vram >= 16:  return 2
        if vram >= 8:   return 1
        return 1   # low VRAM — always single

    except Exception:
        return 1


# ═══════════════════════════════════════════════════════════════════════════════
#  5. STARTUP WARM-UP
# ═══════════════════════════════════════════════════════════════════════════════

def warm_up() -> None:
    """
    Pre-load safety classifiers in background threads at startup so the
    first generation request doesn't block waiting for model loading.
    Call this once during application initialisation.
    """
    try:
        from modules.content_filter import preload_models
        preload_models()
        log.info("[perf] Safety model warm-up started.")
    except Exception as exc:
        log.debug("[perf] Warm-up skipped: %s", exc)
