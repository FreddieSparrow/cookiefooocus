"""
Cookie-Fooocus — 4-Mode Prompt Engine
────────────────────────────────────────────────────────────────────────────────
Replaces the previous opaque expansion system with four explicit, selectable
modes.  Each call returns a PromptResult with the expanded prompt AND a full
PromptTrace so users can see exactly what changed and why.

  Mode A — RAW       Passes the prompt directly to SDXL unchanged.
                     For advanced users who write their own prompt syntax.

  Mode B — BALANCED  Deterministic keyword-based structured expansion.
                     No LLM required.  Always reproducible for the same input.
                     Default mode.

  Mode C — LLM       Optional Ollama / local LLM with constrained JSON output.
                     Falls back silently to BALANCED if LLM unavailable.

  Mode D — STANDARD  Original Fooocus GPT-2 expansion engine.
                     Loaded on first use.  Falls back to BALANCED if the
                     GPT-2 model is unavailable.

Architecture
────────────
  PromptEngine.run(prompt, seed, mode) → PromptResult
    PromptResult.expanded   — the final string sent to SDXL
    PromptResult.trace      — PromptTrace (added / removed / mode / reason)

All expansion is done in this single module.  The cache is provided by the
GenerationController — this module is stateless and pure.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("cookiefooocus.prompt_engine")


# ═══════════════════════════════════════════════════════════════════════════════
#  Data structures
# ═══════════════════════════════════════════════════════════════════════════════

class PromptMode(str, Enum):
    RAW      = "raw"       # Mode A — no modification
    BALANCED = "balanced"  # Mode B — deterministic structured expansion
    LLM      = "llm"       # Mode C — LLM-enhanced constrained JSON output
    STANDARD = "standard"  # Mode D — original Fooocus GPT-2 expansion


@dataclass
class PromptTrace:
    """Full record of what the prompt engine did — shown in the UI Trace View."""
    mode:           PromptMode
    original:       str
    mode_used:      PromptMode = PromptMode.BALANCED  # actual mode executed (may differ from requested)
    fallback_reason: str = ""                          # non-empty if mode was changed due to fallback
    added:          list[str] = field(default_factory=list)
    removed:        list[str] = field(default_factory=list)
    notes:          list[str] = field(default_factory=list)

    def display(self) -> str:
        """Return a human-readable trace string for the UI."""
        _MODE_LABELS = {
            PromptMode.RAW:      "RAW",
            PromptMode.BALANCED: "BALANCED",
            PromptMode.LLM:      "LLM",
            PromptMode.STANDARD: "STANDARD (GPT-2)",
        }
        lines = [
            f"Requested: {_MODE_LABELS.get(self.mode, self.mode.value.upper())}",
            f"Executed:  {_MODE_LABELS.get(self.mode_used, self.mode_used.value.upper())}",
        ]
        if self.fallback_reason:
            lines.append(f"Fallback:  {self.fallback_reason}")
        lines.append(f"Original:  {self.original!r}")
        if self.added:
            lines.append("Added:    " + " | ".join(f"+ {t}" for t in self.added))
        if self.removed:
            lines.append("Removed:  " + " | ".join(f"- {t}" for t in self.removed))
        if self.notes:
            lines.extend(f"Note: {n}" for n in self.notes)
        return "\n".join(lines)


@dataclass
class PromptResult:
    """Output of PromptEngine.run()."""
    expanded: str         # Final string sent to SDXL
    trace:    PromptTrace # What changed and why


# ═══════════════════════════════════════════════════════════════════════════════
#  Mode A — RAW (pass-through)
# ═══════════════════════════════════════════════════════════════════════════════

def _expand_raw(prompt: str) -> PromptResult:
    trace = PromptTrace(
        mode=PromptMode.RAW,
        mode_used=PromptMode.RAW,
        original=prompt,
        notes=["Prompt passed directly to SDXL without modification."],
    )
    return PromptResult(expanded=prompt, trace=trace)


# ═══════════════════════════════════════════════════════════════════════════════
#  Mode B — BALANCED (deterministic keyword-based structured expansion)
# ═══════════════════════════════════════════════════════════════════════════════

# Keyword → expansion tags.  Matched case-insensitively against the prompt.
# Only the first matching entry per category wins (deterministic).
_SUBJECT_RULES: list[tuple[re.Pattern, dict]] = []

_SUBJECT_TABLE: list[tuple[str, dict]] = [
    # pattern                 lighting                style                          composition
    (r"portrait|face|person|people|woman|man|girl|boy|human",
     {"lighting": "soft studio lighting, natural light",
      "style":    "cinematic, photorealistic, sharp focus",
      "composition": "close-up, shallow depth of field"}),

    (r"landscape|mountain|forest|ocean|lake|river|nature",
     {"lighting": "golden hour, volumetric light",
      "style":    "epic landscape, ultra-detailed, 8K",
      "composition": "wide angle, rule of thirds, panoramic"}),

    (r"city|urban|street|architecture|building|skyline",
     {"lighting": "dramatic city lights, ambient occlusion",
      "style":    "architectural photography, high detail",
      "composition": "wide angle, leading lines, perspective"}),

    (r"cyberpunk|neon|futur|sci.?fi|space|robot|android",
     {"lighting": "neon glow, volumetric fog, rim lighting",
      "style":    "cinematic, ultra-detailed, concept art",
      "composition": "wide angle, dramatic perspective, dystopian"}),

    (r"fantasy|dragon|magic|wizard|elf|medieval|castle",
     {"lighting": "epic fantasy lighting, god rays",
      "style":    "fantasy art, highly detailed, matte painting",
      "composition": "epic scale, dynamic angle"}),

    (r"anime|manga|cartoon|illustration|artwork|painting",
     {"lighting": "dynamic lighting",
      "style":    "high quality illustration, vivid colours",
      "composition": "balanced composition, detailed background"}),

    (r"food|dish|meal|cuisine|restaurant",
     {"lighting": "soft diffused light, food photography",
      "style":    "commercial photography, mouth-watering detail",
      "composition": "top-down or 45-degree angle, macro"}),

    (r"animal|wildlife|bird|cat|dog|horse",
     {"lighting": "natural daylight, golden hour",
      "style":    "wildlife photography, sharp detail",
      "composition": "subject centred, blurred background"}),
]

# Compile patterns once
for _pat, _tags in _SUBJECT_TABLE:
    _SUBJECT_RULES.append((re.compile(_pat, re.IGNORECASE), _tags))

# Universal quality boosters always appended in BALANCED mode
_QUALITY_SUFFIX = ["masterpiece", "best quality", "highly detailed"]


def _expand_balanced(prompt: str) -> PromptResult:
    stripped = prompt.strip().rstrip(",.")
    added: list[str] = []
    matched_tags: dict = {}

    for pattern, tags in _SUBJECT_RULES:
        if pattern.search(stripped):
            matched_tags = tags
            break

    expansion_parts = [stripped]

    for key in ("lighting", "style", "composition"):
        value = matched_tags.get(key)
        if value:
            expansion_parts.append(value)
            added.append(f"{key}: {value}")

    expansion_parts.extend(_QUALITY_SUFFIX)
    added.extend(_QUALITY_SUFFIX)

    expanded = ", ".join(expansion_parts)

    trace = PromptTrace(
        mode=PromptMode.BALANCED,
        mode_used=PromptMode.BALANCED,
        original=prompt,
        added=added,
        notes=["Deterministic structured expansion applied.  No LLM required."],
    )
    return PromptResult(expanded=expanded, trace=trace)


# ═══════════════════════════════════════════════════════════════════════════════
#  Mode C — LLM (Ollama with constrained JSON output)
# ═══════════════════════════════════════════════════════════════════════════════

_OLLAMA_HOST  = os.environ.get("OLLAMA_HOST",  "http://localhost:11434").rstrip("/")
_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4")

_LLM_SYSTEM_PROMPT = """\
You are an image prompt assistant. Given a short user prompt, output ONLY valid JSON with these fields:
{
  "subject": "detailed subject description",
  "style": "visual style and medium",
  "lighting": "lighting description",
  "composition": "framing and composition"
}
No extra text. No explanations. No markdown. Output only the JSON object.\
"""


def _call_ollama(prompt: str, seed: int) -> Optional[dict]:
    """Call Ollama and return parsed JSON dict, or None on failure."""
    import random
    rng = random.Random(int(seed))
    temperature = round(0.65 + rng.uniform(-0.05, 0.05), 3)

    payload = json.dumps({
        "model":  _OLLAMA_MODEL,
        "prompt": prompt,
        "system": _LLM_SYSTEM_PROMPT,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
            "seed":        int(seed) % (2 ** 31),
            "num_predict": 300,
        },
    }).encode()

    try:
        req = urllib.request.Request(
            f"{_OLLAMA_HOST}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        raw = result.get("response", "")
        return json.loads(raw)
    except (urllib.error.URLError, json.JSONDecodeError, Exception) as exc:
        log.debug("[prompt_engine] LLM call failed: %s", exc)
        return None


def _expand_llm(prompt: str, seed: int) -> PromptResult:
    data = _call_ollama(prompt, seed)
    fallback_notes: list[str] = []

    if data and isinstance(data, dict):
        subject     = str(data.get("subject", prompt)).strip()
        style       = str(data.get("style", "")).strip()
        lighting    = str(data.get("lighting", "")).strip()
        composition = str(data.get("composition", "")).strip()

        parts  = [subject]
        added  = []
        for label, value in (("style", style), ("lighting", lighting), ("composition", composition)):
            if value:
                parts.append(value)
                added.append(f"{label}: {value}")
        parts.extend(_QUALITY_SUFFIX)
        added.extend(_QUALITY_SUFFIX)

        expanded = ", ".join(p for p in parts if p)
        trace = PromptTrace(
            mode=PromptMode.LLM,
            mode_used=PromptMode.LLM,
            original=prompt,
            added=added,
            notes=["LLM expansion via Ollama with constrained JSON output."],
        )
        return PromptResult(expanded=expanded, trace=trace)

    # LLM unavailable — fall back to BALANCED with explicit reason
    result = _expand_balanced(prompt)
    result.trace.mode            = PromptMode.LLM       # what was requested
    result.trace.mode_used       = PromptMode.BALANCED   # what actually ran
    result.trace.fallback_reason = "Ollama unavailable or returned invalid JSON — fell back to BALANCED."
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  Mode D — STANDARD (original Fooocus GPT-2 expansion)
# ═══════════════════════════════════════════════════════════════════════════════

_gpt2_expansion = None  # lazy singleton — GPT-2 model loaded on first STANDARD call


def _get_gpt2_expansion():
    """Return the GPT-2 FooocusExpansion instance, loading it on first call."""
    global _gpt2_expansion
    if _gpt2_expansion is not None:
        return _gpt2_expansion
    try:
        from extras.expansion import FooocusExpansion
        _gpt2_expansion = FooocusExpansion()
        log.info("[prompt_engine] GPT-2 expansion model loaded for STANDARD mode.")
        return _gpt2_expansion
    except Exception as exc:
        log.warning("[prompt_engine] GPT-2 model unavailable (%s) — STANDARD will fall back to BALANCED.", exc)
        return None


def _expand_standard(prompt: str, seed: int) -> PromptResult:
    """
    Mode D — STANDARD: original Fooocus V2 GPT-2 expansion.
    Falls back to BALANCED if the GPT-2 model is unavailable.
    """
    expansion = _get_gpt2_expansion()

    if expansion is not None:
        try:
            expanded = expansion(prompt, seed)
            if expanded and expanded.strip() and expanded.strip() != prompt.strip():
                # Determine what was added
                original_tokens = set(t.strip() for t in prompt.split(",") if t.strip())
                expanded_tokens = set(t.strip() for t in expanded.split(",") if t.strip())
                added = sorted(expanded_tokens - original_tokens)

                trace = PromptTrace(
                    mode=PromptMode.STANDARD,
                    mode_used=PromptMode.STANDARD,
                    original=prompt,
                    added=added[:10],   # cap display to avoid overwhelming output
                    notes=["Original Fooocus GPT-2 V2 expansion applied."],
                )
                return PromptResult(expanded=expanded, trace=trace)
        except Exception as exc:
            log.warning("[prompt_engine] GPT-2 expansion failed: %s — falling back to BALANCED.", exc)

    # Fallback
    result = _expand_balanced(prompt)
    result.trace.mode            = PromptMode.STANDARD
    result.trace.mode_used       = PromptMode.BALANCED
    result.trace.fallback_reason = "GPT-2 model unavailable or failed — fell back to BALANCED."
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  Public interface
# ═══════════════════════════════════════════════════════════════════════════════

class PromptEngine:
    """
    Stateless prompt expansion engine.

    Usage:
        engine = PromptEngine()
        result = engine.run("a cyberpunk city", seed=12345, mode=PromptMode.BALANCED)
        print(result.expanded)       # → "a cyberpunk city, neon glow, ..."
        print(result.trace.display()) # → human-readable trace for UI
    """

    def run(
        self,
        prompt: str,
        seed:   int  = 0,
        mode:   PromptMode = PromptMode.BALANCED,
    ) -> PromptResult:
        """
        Expand a prompt according to the selected mode.

        Args:
            prompt: Raw user input.
            seed:   Generation seed (used for deterministic LLM temperature).
            mode:   RAW, BALANCED, or LLM.

        Returns:
            PromptResult with expanded string and trace.
        """
        if not prompt or not prompt.strip():
            trace = PromptTrace(mode=mode, original=prompt, notes=["Empty prompt — passed through."])
            return PromptResult(expanded=prompt, trace=trace)

        if mode == PromptMode.RAW:
            return _expand_raw(prompt)
        elif mode == PromptMode.LLM:
            return _expand_llm(prompt, seed)
        elif mode == PromptMode.STANDARD:
            return _expand_standard(prompt, seed)
        else:
            return _expand_balanced(prompt)

    @staticmethod
    def mode_from_string(s: str) -> PromptMode:
        """Parse 'raw' / 'balanced' / 'llm' → PromptMode."""
        mapping = {
            "raw":      PromptMode.RAW,
            "balanced": PromptMode.BALANCED,
            "llm":      PromptMode.LLM,
            "standard": PromptMode.STANDARD,
        }
        return mapping.get(s.lower().strip(), PromptMode.BALANCED)


# Singleton — import and use directly
engine = PromptEngine()
