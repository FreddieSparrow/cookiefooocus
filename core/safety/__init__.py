"""
core.safety — 2-layer safety system
=====================================
Wraps modules.safety.

Layer 1: Deterministic (always on, fast, no ML).
Layer 2: ML classifier (DeBERTa v3, runs only when Layer 1 passes).

Public API:
    check_prompt(text)      → SafetyDecision
    check_image(image_path) → ImageSafetyDecision
    SafetyDecision
    ImageSafetyDecision
"""

from modules.safety import (  # noqa: F401
    check_prompt,
    check_image,
    SafetyDecision,
    ImageSafetyDecision,
)
