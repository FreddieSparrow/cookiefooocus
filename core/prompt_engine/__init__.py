"""
core.prompt_engine — 4-mode prompt engine + PromptTrace
=========================================================
Wraps modules.prompt_engine.

Modes:
    RAW      — passthrough, no modification
    BALANCED — deterministic keyword expansion (default)
    STANDARD — original Fooocus GPT-2 expansion
    LLM      — Ollama-powered creative rewrite

Public API:
    engine          — singleton PromptEngine instance
    PromptMode      — enum of available modes
    PromptResult    — result with .expanded, .trace
    PromptTrace     — full audit of what mode ran and why
"""

from modules.prompt_engine import (  # noqa: F401
    engine,
    PromptMode,
    PromptResult,
    PromptTrace,
)
