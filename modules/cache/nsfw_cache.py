"""
Cookie-Fooocus — NSFW Score Cache
────────────────────────────────────────────────────────────────────────────────
Short-lived cache for NSFW classifier scores keyed by file path.

Lifecycle: short — image files are transient (temp output files).
TTL: 300s — images are replaced or deleted quickly; stale scores mislead.
Memory: bounded by MB, not entry count (paths are tiny; entries are cheap).
Cleanup: background thread purges expired entries every 60s.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("cookiefooocus.cache.nsfw")

_TTL_SECONDS    = 300     # 5 minutes
_MAX_ENTRIES    = 1_000   # safety cap — paths are strings, this is plenty
_CLEANUP_PERIOD = 60      # seconds between background purge runs


@dataclass
class _Entry:
    score:      float
    expires_at: float


class NSFWCache:
    """
    Thread-safe TTL cache for NSFW classifier scores.
    Automatically expires entries and runs a background cleanup thread.
    """

    def __init__(self, ttl: float = _TTL_SECONDS, maxsize: int = _MAX_ENTRIES):
        self._store:   dict[str, _Entry] = {}
        self._ttl    = ttl
        self._max    = maxsize
        self._lock   = threading.Lock()
        self._hits   = 0
        self._misses = 0
        self._start_cleanup()

    @staticmethod
    def _key(path: str) -> str:
        return hashlib.sha256(path.encode()).hexdigest()[:24]

    def get(self, path: str) -> Optional[float]:
        k = self._key(path)
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(k)
            if entry and entry.expires_at > now:
                self._hits += 1
                return entry.score
            if entry:
                del self._store[k]   # expired
            self._misses += 1
            return None

    def put(self, path: str, score: float) -> None:
        k = self._key(path)
        with self._lock:
            if len(self._store) >= self._max:
                self._evict_oldest()
            self._store[k] = _Entry(score=score, expires_at=time.monotonic() + self._ttl)

    def _evict_oldest(self) -> None:
        """Evict the entry with the earliest expiry. Must hold self._lock."""
        if not self._store:
            return
        oldest_key = min(self._store, key=lambda k: self._store[k].expires_at)
        del self._store[oldest_key]

    def _cleanup(self) -> None:
        """Remove all expired entries."""
        now = time.monotonic()
        with self._lock:
            expired = [k for k, v in self._store.items() if v.expires_at <= now]
            for k in expired:
                del self._store[k]
        if expired:
            log.debug("[nsfw_cache] Purged %d expired entries.", len(expired))

    def _start_cleanup(self) -> None:
        def _loop():
            while True:
                time.sleep(_CLEANUP_PERIOD)
                try:
                    self._cleanup()
                except Exception as exc:
                    log.debug("[nsfw_cache] Cleanup error: %s", exc)

        t = threading.Thread(target=_loop, daemon=True, name="nsfw-cache-cleanup")
        t.start()

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits":     self._hits,
                "misses":   self._misses,
                "hit_rate": round(self._hits / total, 3) if total else 0.0,
                "size":     len(self._store),
                "capacity": self._max,
                "ttl_s":    self._ttl,
            }

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._hits = self._misses = 0


nsfw_cache = NSFWCache()
