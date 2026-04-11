"""
core.cache — L1/L2 cache hierarchy
====================================
Wraps modules.cache.

Two physically separate caches with different lifecycles:

    prompt_cache  — LRU, no TTL (deterministic: same input = same output)
    nsfw_cache    — TTL 300s, background cleanup (scores can go stale)

L1: in-memory (microseconds, lost on restart)
L2: SQLite on disk at data/cache/ (milliseconds, survives restart)

Public API:
    cache_manager   — singleton CacheManager
    PromptCache     — L1/L2 prompt cache
    NSFWCache       — TTL-based image score cache
"""

from modules.cache import (  # noqa: F401
    cache_manager,
    PromptCache,
    NSFWCache,
)
