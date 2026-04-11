"""
Cookie-Fooocus — Cache Manager
────────────────────────────────────────────────────────────────────────────────
Three physically separate caches with different lifecycle policies.
Do NOT merge them into a single store — they have incompatible eviction needs.

  prompt_cache   LRU, no TTL   — deterministic expansions, stable strings
  nsfw_cache     TTL=300s      — transient image scores, temp file paths

Import directly from the sub-module, or use the helpers here.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from modules.cache.prompt_cache import prompt_cache, PromptCache
from modules.cache.nsfw_cache   import nsfw_cache,  NSFWCache

__all__ = [
    "prompt_cache", "PromptCache",
    "nsfw_cache",   "NSFWCache",
]


def all_stats() -> dict:
    """Return stats for every cache in one call."""
    return {
        "prompt": prompt_cache.stats(),
        "nsfw":   nsfw_cache.stats(),
    }


def clear_all() -> None:
    """Flush all caches — useful in tests."""
    prompt_cache.clear()
    nsfw_cache.clear()
