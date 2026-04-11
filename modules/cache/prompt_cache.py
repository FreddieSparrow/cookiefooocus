"""
Cookie-Fooocus — Prompt Cache
────────────────────────────────────────────────────────────────────────────────
LRU cache for prompt expansion results.

Lifecycle: deterministic — same input always yields same output.
Memory model: bounded by entry count (prompts are small strings).
TTL: none needed (output is deterministic for a given prompt+seed+mode).

Separate from embedding_cache and nsfw_cache — they have different
lifecycles and eviction needs.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import OrderedDict
from typing import Optional

log = logging.getLogger("cookiefooocus.cache.prompt")

_DEFAULT_MAXSIZE = 512   # entries — prompts are small, 512 is safe


class PromptCache:
    """
    Thread-safe LRU cache for expanded prompts.
    No TTL — expansion is deterministic for (prompt, seed, mode).
    """

    def __init__(self, maxsize: int = _DEFAULT_MAXSIZE):
        self._store:  OrderedDict[str, str] = OrderedDict()
        self._max    = maxsize
        self._lock   = threading.Lock()
        self._hits   = 0
        self._misses = 0

    @staticmethod
    def _key(prompt: str, seed: int, mode: str) -> str:
        raw = f"{mode}:{seed}:{prompt}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def get(self, prompt: str, seed: int, mode: str) -> Optional[str]:
        k = self._key(prompt, seed, mode)
        with self._lock:
            if k in self._store:
                self._store.move_to_end(k)
                self._hits += 1
                return self._store[k]
            self._misses += 1
            return None

    def put(self, prompt: str, seed: int, mode: str, expanded: str) -> None:
        k = self._key(prompt, seed, mode)
        with self._lock:
            if k in self._store:
                self._store.move_to_end(k)
            else:
                if len(self._store) >= self._max:
                    self._store.popitem(last=False)
            self._store[k] = expanded

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits":     self._hits,
                "misses":   self._misses,
                "hit_rate": round(self._hits / total, 3) if total else 0.0,
                "size":     len(self._store),
                "capacity": self._max,
            }

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._hits = self._misses = 0


prompt_cache = PromptCache()
