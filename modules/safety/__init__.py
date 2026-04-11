"""
Cookie-Fooocus — 2-Layer Safety System
────────────────────────────────────────────────────────────────────────────────
Clean public interface over the existing content_filter.py.

Architecture
─────────────────────────────────────────────────────────────────────────────
  Layer 1 — Deterministic (fast, always-on, no ML)
    Hard rules: CSAM, violence instruction, explicit jailbreak
    Intent detection: undress requests, explicit sexual intent
    Regex only — no probabilistic scoring

  Layer 2 — ML Safety (optional, handles ambiguous cases)
    Transformer-based injection classifier
    Risk scoring for edge cases
    Configurable threshold — not blocking by default, scoring only

  Image moderation is separated from generation:
    Generate first (no mid-pipeline blocking)
    Post-process: NSFW score → ALLOW / WARN / BLOCK
    Result visible at SafetyDecision.image_action

  Structured reasons are returned (without revealing exploitable details):
    {
      "decision": "block",
      "layer": "deterministic",
      "rule": "hard_block",
      "confidence": 1.0
    }

This module is the single authority for all safety decisions.
Callers should import from here, not from content_filter directly.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("cookiefooocus.safety")


# ═══════════════════════════════════════════════════════════════════════════════
#  Result types
# ═══════════════════════════════════════════════════════════════════════════════

class Decision(str, Enum):
    ALLOW = "allow"
    WARN  = "warn"
    BLOCK = "block"


class ImageAction(str, Enum):
    SHOW  = "show"
    BLUR  = "blur"    # warn — show blurred with warning
    HIDE  = "hide"    # block — do not show


@dataclass
class SafetyReason:
    """Structured reason returned by each decision — safe to surface in API/UI."""
    decision:   Decision
    layer:      str         # "deterministic" | "ml" | "image"
    rule:       str         # rule identifier (no internal regex detail)
    confidence: float       # 0.0–1.0


@dataclass
class SafetyDecision:
    """
    Full result of a safety check.

    For prompt checks:
      allowed       — whether generation should proceed
      reason        — structured reason (safe to show)
      generic_msg   — user-facing message (never reveals which rule matched)

    For image post-processing:
      image_action  — SHOW / BLUR / HIDE
      nsfw_score    — raw classifier score (for logging only)
    """
    allowed:      bool
    reason:       SafetyReason
    generic_msg:  str  = "Request blocked by safety policy."
    image_action: Optional[ImageAction] = None
    nsfw_score:   float = 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  Policy loader
# ═══════════════════════════════════════════════════════════════════════════════

def _load_policy() -> dict:
    try:
        import json
        from pathlib import Path
        path = Path(__file__).parent.parent.parent / "safety_policy.json"
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}

def _get_policy_value(*keys, default=None):
    policy = _load_policy()
    node = policy
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k, default)
    return node


# ═══════════════════════════════════════════════════════════════════════════════
#  Layer 1 — Deterministic filter (delegates to content_filter)
# ═══════════════════════════════════════════════════════════════════════════════

def _check_deterministic(prompt: str) -> Optional[SafetyDecision]:
    """
    Run the deterministic layer only.
    Returns SafetyDecision if blocked, None if prompt passes.
    """
    try:
        from modules.content_filter import filter_prompt, Severity
        result = filter_prompt(prompt)

        if result.allowed:
            return None  # pass — let Layer 2 handle edge cases

        # Map severity to our structured reason
        is_critical = result.severity == Severity.CRITICAL

        reason = SafetyReason(
            decision=Decision.BLOCK,
            layer="deterministic",
            rule="hard_block" if is_critical else "content_rule",
            confidence=1.0,
        )
        return SafetyDecision(
            allowed=False,
            reason=reason,
            generic_msg="Request blocked by safety policy.",
        )

    except Exception as exc:
        log.warning("[safety/layer1] Deterministic check failed: %s — blocking as safe default.", exc)
        return SafetyDecision(
            allowed=False,
            reason=SafetyReason(
                decision=Decision.BLOCK,
                layer="deterministic",
                rule="filter_error",
                confidence=1.0,
            ),
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Layer 2 — ML safety classifier (optional, edge cases only)
# ═══════════════════════════════════════════════════════════════════════════════

def _check_ml(prompt: str) -> Optional[SafetyDecision]:
    """
    Run ML classifier for ambiguous prompts.
    Returns SafetyDecision if blocked by ML, None if score below threshold.
    """
    threshold = float(_get_policy_value("prompt_filter", "ml_threshold", default=0.80))

    try:
        from modules.content_filter import _check_ml_classifier
        score = _check_ml_classifier(prompt)
    except Exception:
        return None  # ML unavailable — fail open for this layer

    if score is None or score < threshold:
        return None

    reason = SafetyReason(
        decision=Decision.BLOCK,
        layer="ml",
        rule="ml_classifier",
        confidence=round(float(score), 3),
    )
    return SafetyDecision(
        allowed=False,
        reason=reason,
        generic_msg="Request blocked by safety policy.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Image post-processing moderation
# ═══════════════════════════════════════════════════════════════════════════════

def check_image_post(image_path: str) -> SafetyDecision:
    """
    Post-generation image moderation.  Called AFTER SDXL has produced the image.

    Returns:
      image_action = SHOW  → display normally
      image_action = BLUR  → display blurred with warning
      image_action = HIDE  → do not display at all
    """
    nsfw_block = float(_get_policy_value("image_filter", "nsfw_block_threshold", default=0.65))
    nsfw_warn  = float(_get_policy_value("image_filter", "nsfw_warn_threshold",  default=0.35))

    try:
        from modules.content_filter import check_image, Severity
        result = check_image(image_path)
        score  = float(getattr(result, "score", 0.0))

        if not result.allowed or score >= nsfw_block:
            action = ImageAction.HIDE
            decision = Decision.BLOCK
        elif score >= nsfw_warn:
            action = ImageAction.BLUR
            decision = Decision.WARN
        else:
            action = ImageAction.SHOW
            decision = Decision.ALLOW

        reason = SafetyReason(
            decision=decision,
            layer="image",
            rule="nsfw_classifier",
            confidence=round(score, 3),
        )
        return SafetyDecision(
            allowed=(action == ImageAction.SHOW),
            reason=reason,
            image_action=action,
            nsfw_score=score,
        )

    except Exception as exc:
        log.warning("[safety/image] Post-gen image check failed: %s — showing image.", exc)
        return SafetyDecision(
            allowed=True,
            reason=SafetyReason(
                decision=Decision.ALLOW,
                layer="image",
                rule="check_error",
                confidence=0.0,
            ),
            image_action=ImageAction.SHOW,
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Public entry point
# ═══════════════════════════════════════════════════════════════════════════════

def check_prompt(prompt: str, run_ml: bool = True) -> SafetyDecision:
    """
    Run the 2-layer safety check on a text prompt.

    Layer 1 (deterministic) always runs.
    Layer 2 (ML) only runs if run_ml=True and Layer 1 passed.

    Returns SafetyDecision — check .allowed before proceeding.
    """
    # Layer 1 — fast deterministic
    decision = _check_deterministic(prompt)
    if decision is not None:
        return decision

    # Layer 2 — ML (optional)
    if run_ml:
        decision = _check_ml(prompt)
        if decision is not None:
            return decision

    # All layers passed
    return SafetyDecision(
        allowed=True,
        reason=SafetyReason(
            decision=Decision.ALLOW,
            layer="none",
            rule="pass",
            confidence=1.0,
        ),
    )


__all__ = [
    "check_prompt",
    "check_image_post",
    "Decision",
    "ImageAction",
    "SafetyReason",
    "SafetyDecision",
]
