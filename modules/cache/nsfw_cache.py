"""
Cookie-Fooocus — NSFW Score Cache  (L1 memory + L2 SQLite)
────────────────────────────────────────────────────────────────────────────────
Two-tier TTL cache for NSFW classifier scores keyed by file path.

  L1  in-memory dict  — fast, bounded, cleared on restart
  L2  SQLite on disk  — survives restarts; only unexpired entries are loaded

Lifecycle: short — image files are transient (temp output files).
TTL: 300s — images are replaced or deleted quickly; stale scores mislead.

Cache hierarchy:
  get():  L1 hit (not expired) → return
          L1 miss → check L2 → if valid, promote to L1, return
          L2 miss or expired → return None

  put():  write to L1 synchronously
          write to L2 in a daemon thread (never blocks generation)

Failure policy:
  Any SQLite error is silently swallowed at DEBUG level.
  L2 failure must never fail a generation job.

TTL note:
  L1 uses monotonic time internally (no drift).
  L2 stores wall-clock expiry so entries survive restarts correctly.
  The load path converts wall_expiry → monotonic_expiry.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("cookiefooocus.cache.nsfw")

_TTL_SECONDS    = 300     # 5 minutes
_MAX_ENTRIES    = 1_000   # L1 safety cap
_CLEANUP_PERIOD = 60      # seconds between background L1 purge runs


def _default_persist_path() -> Optional[str]:
    """Return data/cache/nsfw_cache.db relative to project root, or None if not writable."""
    try:
        path = Path(__file__).parent.parent.parent / "data" / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return str(path / "nsfw_cache.db")
    except Exception:
        return None


@dataclass
class _Entry:
    score:      float
    expires_at: float   # monotonic — for L1 expiry checks


class NSFWCache:
    """
    Thread-safe two-tier TTL cache for NSFW classifier scores.

    L1: in-memory (fast, expires via monotonic time, background cleanup)
    L2: SQLite (optional, persists wall-clock expiry, loaded on startup)
    """

    def __init__(
        self,
        ttl:          float         = _TTL_SECONDS,
        maxsize:      int           = _MAX_ENTRIES,
        persist_path: Optional[str] = "auto",
    ):
        self._store:  dict[str, _Entry] = {}
        self._ttl    = ttl
        self._max    = maxsize
        self._lock   = threading.Lock()
        self._hits_l1  = 0
        self._hits_l2  = 0
        self._misses   = 0

        if persist_path == "auto":
            persist_path = _default_persist_path()
        self._db_path: Optional[str] = persist_path

        if self._db_path:
            self._init_db()
            self._load_from_db()

        self._start_cleanup()

    # ── Key ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _key(path: str) -> str:
        return hashlib.sha256(path.encode()).hexdigest()[:24]

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, path: str) -> Optional[float]:
        k   = self._key(path)
        now = time.monotonic()

        with self._lock:
            entry = self._store.get(k)
            if entry:
                if entry.expires_at > now:
                    self._hits_l1 += 1
                    return entry.score
                del self._store[k]   # expired in L1

        # L2 fallback
        if self._db_path:
            result = self._read_db(k)
            if result is not None:
                score, wall_exp = result
                mono_exp = now + (wall_exp - time.time())
                if mono_exp > now:
                    with self._lock:
                        self._hits_l2 += 1
                        self._l1_insert(k, score, mono_exp)
                    return score

        with self._lock:
            self._misses += 1
        return None

    def put(self, path: str, score: float) -> None:
        k        = self._key(path)
        mono_exp = time.monotonic() + self._ttl
        wall_exp = time.time()      + self._ttl   # for L2 storage
        with self._lock:
            if len(self._store) >= self._max:
                self._evict_oldest()
            self._l1_insert(k, score, mono_exp)
        if self._db_path:
            self._async_write_db(k, score, wall_exp)

    # ── L1 helpers ────────────────────────────────────────────────────────────

    def _l1_insert(self, key: str, score: float, expires_at: float) -> None:
        """Insert or update in L1.  Must hold self._lock."""
        self._store[key] = _Entry(score=score, expires_at=expires_at)

    def _evict_oldest(self) -> None:
        """Evict the entry with the earliest expiry.  Must hold self._lock."""
        if not self._store:
            return
        oldest = min(self._store, key=lambda k: self._store[k].expires_at)
        del self._store[oldest]

    def _cleanup(self) -> None:
        """Remove all expired L1 entries."""
        now = time.monotonic()
        with self._lock:
            expired = [k for k, v in self._store.items() if v.expires_at <= now]
            for k in expired:
                del self._store[k]
        if expired:
            log.debug("[nsfw_cache] L1 purged %d expired entries.", len(expired))

    def _start_cleanup(self) -> None:
        def _loop():
            while True:
                time.sleep(_CLEANUP_PERIOD)
                try:
                    self._cleanup()
                except Exception as exc:
                    log.debug("[nsfw_cache] Cleanup error: %s", exc)

        threading.Thread(target=_loop, daemon=True, name="nsfw-cache-cleanup").start()

    # ── L2 helpers ────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS nsfw_cache (
                        key        TEXT PRIMARY KEY,
                        score      REAL NOT NULL,
                        expires_at REAL NOT NULL
                    )
                """)
                conn.commit()
            log.debug("[nsfw_cache] L2 initialised at %s", self._db_path)
        except Exception as exc:
            log.debug("[nsfw_cache] L2 init failed: %s — L2 disabled.", exc)
            self._db_path = None

    def _load_from_db(self) -> None:
        """Warm L1 from unexpired L2 entries on startup."""
        wall_now = time.time()
        mono_now = time.monotonic()
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT key, score, expires_at FROM nsfw_cache WHERE expires_at > ?",
                    (wall_now,),
                ).fetchall()
            loaded = 0
            with self._lock:
                for key, score, wall_exp in rows:
                    mono_exp = mono_now + (wall_exp - wall_now)
                    if mono_exp > mono_now:
                        self._l1_insert(key, score, mono_exp)
                        loaded += 1
            log.debug("[nsfw_cache] L1 warmed with %d unexpired entries from L2.", loaded)
        except Exception as exc:
            log.debug("[nsfw_cache] L2 warm load failed: %s", exc)

    def _read_db(self, key: str) -> Optional[tuple]:
        """Return (score, wall_expires_at) or None."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT score, expires_at FROM nsfw_cache WHERE key = ? AND expires_at > ?",
                    (key, time.time()),
                ).fetchone()
            return (row[0], row[1]) if row else None
        except Exception as exc:
            log.debug("[nsfw_cache] L2 read failed: %s", exc)
            return None

    def _async_write_db(self, key: str, score: float, wall_exp: float) -> None:
        """Write to SQLite in a daemon thread — never blocks the caller."""
        def _write():
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO nsfw_cache (key, score, expires_at) "
                        "VALUES (?, ?, ?)",
                        (key, score, wall_exp),
                    )
                    conn.commit()
            except Exception as exc:
                log.debug("[nsfw_cache] L2 write failed: %s", exc)

        threading.Thread(target=_write, daemon=True, name="nsfw-cache-l2").start()

    # ── Stats / maintenance ───────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            total = self._hits_l1 + self._hits_l2 + self._misses
            return {
                "hits_l1":    self._hits_l1,
                "hits_l2":    self._hits_l2,
                "misses":     self._misses,
                "hit_rate":   round((self._hits_l1 + self._hits_l2) / total, 3) if total else 0.0,
                "l1_size":    len(self._store),
                "l1_capacity": self._max,
                "ttl_s":      self._ttl,
                "l2_enabled": self._db_path is not None,
            }

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._hits_l1 = self._hits_l2 = self._misses = 0
        # Note: does not clear L2 — intentional (persistent scores survive clear())


nsfw_cache = NSFWCache()
