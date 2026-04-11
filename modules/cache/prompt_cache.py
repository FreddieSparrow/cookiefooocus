"""
Cookie-Fooocus — Prompt Cache  (L1 memory + L2 SQLite)
────────────────────────────────────────────────────────────────────────────────
Two-tier cache for prompt expansion results.

  L1  in-memory LRU   — sub-microsecond reads, bounded by entry count
  L2  SQLite on disk  — survives restarts, populated lazily from L1 misses

Cache hierarchy:
  get():  L1 hit → return immediately
          L1 miss, L2 hit → populate L1, return
          L2 miss → return None (caller must recompute, then call put())

  put():  write to L1 synchronously
          write to L2 in a daemon thread (never blocks generation)

Failure policy:
  Any SQLite error is logged at DEBUG and silently swallowed.
  L2 failure must never fail a generation job.
  If persist_path is None (default), L2 is disabled entirely.

Lifecycle: deterministic — same (prompt, seed, mode) → same expansion.
No TTL needed: expansion output is stable for a given input tuple.

Cache lifecycle hardening (v2.5+):
  Size-based eviction  — L2 is pruned when row count exceeds soft/hard caps.
  Age-based pruning    — entries older than max_age_days are removed.
  Periodic compaction  — VACUUM runs after eviction to reclaim disk space.
  Eviction metrics     — eviction_count tracked and exposed in stats().

Default persist path:
  data/cache/prompt_cache.db  (created automatically if writable)
  Disable by passing persist_path=None to PromptCache().

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional

log = logging.getLogger("cookiefooocus.cache.prompt")

_DEFAULT_MAXSIZE     = 512    # L1 entry cap — prompts are small strings
_L2_SOFT_CAP         = 5_000  # start evicting when L2 exceeds this row count
_L2_HARD_CAP         = 10_000 # never exceed this; evict aggressively if reached
_L2_MAX_AGE_DAYS     = 30     # prune entries older than this
_L2_COMPACT_INTERVAL = 3600   # seconds between VACUUM runs


def _default_persist_path() -> Optional[str]:
    """Return data/cache/prompt_cache.db relative to project root, or None if not writable."""
    try:
        path = Path(__file__).parent.parent.parent / "data" / "cache"
        path.mkdir(parents=True, exist_ok=True)
        return str(path / "prompt_cache.db")
    except Exception:
        return None


class PromptCache:
    """
    Thread-safe two-tier prompt expansion cache.

    L1: in-memory LRU (fast, bounded, cleared on restart)
    L2: SQLite on disk (persistent across restarts, optional)
    """

    def __init__(
        self,
        maxsize:      int            = _DEFAULT_MAXSIZE,
        persist_path: Optional[str]  = "auto",   # "auto" = use default path
    ):
        self._store:  OrderedDict[str, str] = OrderedDict()
        self._max    = maxsize
        self._lock   = threading.Lock()
        self._hits_l1     = 0
        self._hits_l2     = 0
        self._misses      = 0
        self._evictions   = 0
        self._stale_count = 0
        self._last_compact: float = time.time()

        # Resolve persist path
        if persist_path == "auto":
            persist_path = _default_persist_path()
        self._db_path: Optional[str] = persist_path

        if self._db_path:
            self._init_db()
            self._load_from_db()

    # ── Key ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _key(prompt: str, seed: int, mode: str) -> str:
        raw = f"{mode}:{seed}:{prompt}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, prompt: str, seed: int, mode: str) -> Optional[str]:
        """
        Return cached expansion, or None on miss.
        Checks L1 first, then L2.  L2 hits are promoted into L1.
        """
        k = self._key(prompt, seed, mode)

        # L1 check
        with self._lock:
            if k in self._store:
                self._store.move_to_end(k)
                self._hits_l1 += 1
                return self._store[k]

        # L2 check
        if self._db_path:
            expanded = self._read_db(k)
            if expanded is not None:
                with self._lock:
                    self._misses -= 1   # undo miss increment below
                    self._hits_l2 += 1
                    self._lru_insert(k, expanded)
                return expanded

        with self._lock:
            self._misses += 1
        return None

    def put(self, prompt: str, seed: int, mode: str, expanded: str) -> None:
        """Write to L1 synchronously; async write to L2."""
        k = self._key(prompt, seed, mode)
        with self._lock:
            self._lru_insert(k, expanded)
        if self._db_path:
            self._async_write_db(k, expanded)

    # ── L1 helpers ────────────────────────────────────────────────────────────

    def _lru_insert(self, key: str, value: str) -> None:
        """Insert into OrderedDict LRU.  Must hold self._lock."""
        if key in self._store:
            self._store.move_to_end(key)
        else:
            if len(self._store) >= self._max:
                self._store.popitem(last=False)
        self._store[key] = value

    # ── L2 helpers ────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS prompt_cache (
                        key        TEXT PRIMARY KEY,
                        expanded   TEXT NOT NULL,
                        created_at REAL NOT NULL
                    )
                """)
                conn.commit()
            log.debug("[prompt_cache] L2 initialised at %s", self._db_path)
        except Exception as exc:
            log.debug("[prompt_cache] L2 init failed: %s — L2 disabled.", exc)
            self._db_path = None

    def _load_from_db(self) -> None:
        """Warm L1 from the most recent L2 entries on startup."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT key, expanded FROM prompt_cache "
                    "ORDER BY created_at DESC LIMIT ?",
                    (self._max,),
                ).fetchall()
            with self._lock:
                for key, expanded in reversed(rows):   # oldest first → LRU order
                    self._lru_insert(key, expanded)
            log.debug("[prompt_cache] L1 warmed with %d entries from L2.", len(rows))
        except Exception as exc:
            log.debug("[prompt_cache] L2 warm load failed: %s", exc)

    def _read_db(self, key: str) -> Optional[str]:
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT expanded FROM prompt_cache WHERE key = ?", (key,)
                ).fetchone()
            return row[0] if row else None
        except Exception as exc:
            log.debug("[prompt_cache] L2 read failed: %s", exc)
            return None

    def _async_write_db(self, key: str, expanded: str) -> None:
        """Write to SQLite in a daemon thread — never blocks the caller."""
        def _write():
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO prompt_cache (key, expanded, created_at) "
                        "VALUES (?, ?, ?)",
                        (key, expanded, time.time()),
                    )
                    conn.commit()
            except Exception as exc:
                log.debug("[prompt_cache] L2 write failed: %s", exc)

        threading.Thread(target=_write, daemon=True, name="prompt-cache-l2").start()

    # ── Stats / maintenance ───────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            total = self._hits_l1 + self._hits_l2 + self._misses
            return {
                "hits_l1":      self._hits_l1,
                "hits_l2":      self._hits_l2,
                "misses":       self._misses,
                "hit_rate":     round((self._hits_l1 + self._hits_l2) / total, 3) if total else 0.0,
                "evictions":    self._evictions,
                "stale_pruned": self._stale_count,
                "l1_size":      len(self._store),
                "l1_capacity":  self._max,
                "l2_enabled":   self._db_path is not None,
            }

    def prune(self) -> int:
        """
        Remove stale L2 entries (age > max_age_days) and enforce size caps.
        Returns number of rows removed.  Safe to call from background threads.
        """
        if not self._db_path:
            return 0
        removed = 0
        try:
            cutoff = time.time() - (_L2_MAX_AGE_DAYS * 86400)
            with sqlite3.connect(self._db_path) as conn:
                # Age-based pruning
                cur = conn.execute(
                    "DELETE FROM prompt_cache WHERE created_at < ?", (cutoff,)
                )
                removed += cur.rowcount

                # Size-based eviction — remove oldest rows above soft cap
                count = conn.execute("SELECT COUNT(*) FROM prompt_cache").fetchone()[0]
                if count > _L2_SOFT_CAP:
                    excess = count - _L2_SOFT_CAP
                    cur = conn.execute(
                        "DELETE FROM prompt_cache WHERE key IN "
                        "(SELECT key FROM prompt_cache ORDER BY created_at ASC LIMIT ?)",
                        (excess,),
                    )
                    removed += cur.rowcount

                conn.commit()

            if removed:
                with self._lock:
                    self._stale_count += removed
                    self._evictions   += removed
                log.debug("[prompt_cache] Pruned %d stale/excess L2 entries", removed)
                self._compact_if_due()

        except Exception as exc:
            log.debug("[prompt_cache] Prune failed: %s", exc)
        return removed

    def _compact_if_due(self) -> None:
        """Run VACUUM if the compaction interval has elapsed."""
        if not self._db_path:
            return
        now = time.time()
        if now - self._last_compact < _L2_COMPACT_INTERVAL:
            return
        self._last_compact = now

        def _do_vacuum() -> None:
            try:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute("VACUUM")
                log.debug("[prompt_cache] L2 VACUUM complete")
            except Exception as exc:
                log.debug("[prompt_cache] VACUUM failed: %s", exc)

        threading.Thread(target=_do_vacuum, daemon=True, name="prompt-cache-vacuum").start()

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._hits_l1 = self._hits_l2 = self._misses = 0
        # Note: does not clear L2 — intentional (persistent cache survives clear())


prompt_cache = PromptCache()
