"""
Cookie-Fooocus — Moderation Module
────────────────────────────────────
Re-exports the content safety pipeline so other code can import from
`modules.moderation` rather than `modules.content_filter` directly.

This is the "one responsibility" boundary for all content moderation:
  - Prompt safety filtering
  - Image NSFW detection
  - Age-safety checking
  - Input image scanning

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from modules.content_filter import (
    # Core types
    FilterResult,
    Severity,

    # Public filter functions
    check_prompt,
    check_image,
    check_input_image,
    preload_models,

    # Settings
    get_setting,

    # Pattern lists (for testing/inspection)
    BLOCK_PATTERNS,
    ADULT_PATTERNS,
    INTENT_PATTERNS,
    FUZZY_KEYWORDS,
    RISK_CLUSTERS,
    WARN_PATTERNS,
)

__all__ = [
    "FilterResult",
    "Severity",
    "check_prompt",
    "check_image",
    "check_input_image",
    "preload_models",
    "get_setting",
    "BLOCK_PATTERNS",
    "ADULT_PATTERNS",
    "INTENT_PATTERNS",
    "FUZZY_KEYWORDS",
    "RISK_CLUSTERS",
    "WARN_PATTERNS",
]
