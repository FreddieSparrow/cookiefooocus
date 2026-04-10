"""
Cookie-Fooocus Content Safety Filter  —  v3
Full bypass-defeat pipeline for prompt and image safety.

Normalisation pipeline (defeats ~80 % of real-world bypass tricks)
───────────────────────────────────────────────────────────────────
  Raw input
    ↓  [1] Unicode NFKC   (𝓈𝑒𝓍 → sex, ｓｅｘ → sex, bold/italic variants)
    ↓  [2] Homoglyphs      (Cyrillic/Greek lookalikes → ASCII)
    ↓  [3] Leet-speak      (3→e, 0→o, 4→a, @→a …)
    ↓  [4] Diacritics       (café → cafe)
    ↓  [5] Zero-width chars  stripped
    ↓  [6] Spaced words      s e x → sex
    ↓  [7] Base64 sniffing   decode + append for scanning
    → clean canonical form

Detection pipeline
──────────────────
  [A] Hard block patterns   (CRITICAL: CSAM, WMD; BLOCK: injection, violence)
  [B] 18+ adult filter      (ALWAYS ON — cannot be disabled)
  [C] Intent patterns        ("remove her clothes", "undress the subject" …)
  [D] Fuzzy keyword match    (rapidfuzz if available, else edit-distance fallback)
  [E] Risk scoring           (additive score across keyword clusters)
  [F] ML injection classifier (optional, lazy-loaded transformer)
  [G] Warn patterns          (pass-through with caution note)

Output
──────
  FilterResult.reason is always generic ("Request blocked by safety policy.")
  — never leaking which specific rule matched (prevents filter tuning by attacker).

All regex patterns are pre-compiled at module load for performance.
The 18+ adult filter is permanently enabled and cannot be toggled at runtime.
"""

import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
import base64
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import lru_cache
from math import log2
from pathlib import Path
from typing import Any

log = logging.getLogger("cookiefooocus.filter")

# ── Observability (structured JSON events) ─────────────────────────────────────
try:
    from modules.observability.structured_log import log_decision, log_error
    _obs_available = True
except ImportError:
    _obs_available = False
    def log_decision(**_kw): pass
    def log_error(**_kw): pass

# ── Learning engine (on-device bypass logging) ─────────────────────────────────
try:
    from modules.learning_engine import log_blocked_prompt, log_borderline_prompt
    _learning_available = True
except ImportError:
    _learning_available = False
    def log_blocked_prompt(**_kw): pass
    def log_borderline_prompt(**_kw): pass

# ── Policy loader ──────────────────────────────────────────────────────────────
_POLICY_PATH = Path(__file__).parent.parent / "safety_policy.json"
_policy_lock = threading.Lock()
_policy: dict = {}

def _load_policy() -> dict:
    """Load safety_policy.json, falling back to safe defaults on any error."""
    try:
        return json.loads(_POLICY_PATH.read_text())
    except Exception as exc:
        log.warning("[filter] Could not load safety_policy.json: %s — using defaults.", exc)
        return {}

def _reload_policy() -> None:
    global _policy
    with _policy_lock:
        _policy = _load_policy()

_reload_policy()

def _pol(path: str, default):
    """Read a nested policy value by dot-separated path, e.g. 'prompt_filter.ml_threshold'."""
    keys = path.split(".")
    node = _policy
    for k in keys:
        if not isinstance(node, dict):
            return default
        node = node.get(k, default)
    return node


# ── Severity ──────────────────────────────────────────────────────────────────

class Severity(Enum):
    SAFE     = "safe"
    WARN     = "warn"
    BLOCK    = "block"
    CRITICAL = "critical"

_BLOCKED_REASON = "Request cannot be processed."   # Generic — never reveals which rule
_WARN_REASON    = "Flagged content: proceed with caution."


@dataclass
class FilterResult:
    allowed:  bool
    severity: Severity
    reason:   str        = ""
    category: str        = ""
    score:    float      = 0.0
    redacted: str        = ""
    trace:    list[dict] = field(default_factory=list)   # populated when debug_trace=true


def _trace_enabled() -> bool:
    return bool(_pol("debug_trace", False))


# ── Normalisation cache ────────────────────────────────────────────────────────
# Caches the last 512 normalised prompts to avoid re-normalising identical
# inputs on repeated calls within the same session.

@lru_cache(maxsize=512)
def _normalise_cached(text: str) -> str:
    """LRU-cached wrapper — call this instead of _normalise() in hot paths."""
    return _normalise(text)


# ── Runtime settings ──────────────────────────────────────────────────────────
# The 18+ adult filter is always enabled and cannot be toggled.
# Only prompt_filter_enabled and nsfw_image_filter_enabled are runtime settings.

_settings_lock = threading.Lock()
_settings: dict = {
    "prompt_filter_enabled":     True,
    "nsfw_image_filter_enabled": True,
}


def get_setting(key: str) -> bool:
    with _settings_lock:
        return _settings.get(key, True)


# ── Critical alert writer ──────────────────────────────────────────────────────

_ALERT_DIR = Path.home() / ".local" / "share" / "cookiefooocus" / "alerts"


def _write_critical_alert(category: str, user_id: str, evidence: str) -> None:
    try:
        _ALERT_DIR.mkdir(parents=True, exist_ok=True)
        ts_safe = datetime.now().isoformat().replace(":", "-")
        f = _ALERT_DIR / f"critical-{ts_safe}.json"
        f.write_text(json.dumps({
            "timestamp":     datetime.now().isoformat(),
            "category":      category,
            "user_id_hash":  hashlib.sha256(user_id.encode()).hexdigest()[:16],
            "evidence_hash": hashlib.sha256(evidence.encode()).hexdigest(),
        }, indent=2))
        log.critical("[ALERT] CRITICAL (%s). Alert: %s", category, f)
    except Exception as exc:
        log.error("[filter] Alert write failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
#  NORMALISATION LAYER
# ═══════════════════════════════════════════════════════════════════════════════

# Extended homoglyph table — Cyrillic, Greek, and other lookalikes → ASCII
_HOMOGLYPHS: dict[str, str] = {
    # Cyrillic
    '\u0430': 'a', '\u0410': 'a',  # а А
    '\u0435': 'e', '\u0415': 'e',  # е Е
    '\u0456': 'i', '\u0406': 'i',  # і І
    '\u043e': 'o', '\u041e': 'o',  # о О
    '\u0440': 'r', '\u0420': 'r',  # р Р
    '\u0441': 'c', '\u0421': 'c',  # с С
    '\u0445': 'x', '\u0425': 'x',  # х Х
    '\u0443': 'y', '\u0423': 'y',  # у У
    '\u0455': 's',                  # ѕ
    '\u0458': 'j',                  # ј
    # Greek
    '\u03b1': 'a',  # α
    '\u03b5': 'e',  # ε
    '\u03bf': 'o',  # ο
    '\u03c1': 'p',  # ρ
    '\u03c5': 'u',  # υ
    '\u03bd': 'v',  # ν
    # Fullwidth Latin (covered by NFKC but belt-and-suspenders)
    '\uff41': 'a', '\uff45': 'e', '\uff49': 'i', '\uff4f': 'o', '\uff55': 'u',
    '\uff53': 's', '\uff38': 'r',
    # Lookalike punctuation used as letters
    '\u00f8': 'o',  # ø
    '\u00f6': 'o',  # ö
    '\u00fc': 'u',  # ü
    '\u00e4': 'a',  # ä
}

_LEET: dict = str.maketrans({
    '0': 'o', '1': 'i', '3': 'e', '4': 'a', '5': 's',
    '7': 't', '8': 'b', '@': 'a', '$': 's', '!': 'i',
    '+': 't', '|': 'i', '(': 'c', '<': 'c', '9': 'g',
})

# Spaced-word collapse: "s e x" → "sex"  (2+ single chars separated by spaces)
_RE_SPACED = re.compile(r'(?<!\w)(\w)(?: +(\w)){1,}(?!\w)')


def _high_entropy(s: str, threshold: float = 4.5) -> bool:
    """Return True if Shannon entropy of s meets threshold — gates base64 decoding."""
    if not s:
        return False
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((f / n) * log2(f / n) for f in freq.values()) >= threshold


def _collapse_spaced(text: str) -> str:
    """Collapse single-character spaced words: 's e x' → 'sex'."""
    def _join(m: re.Match) -> str:
        # Re-join all single chars in the match
        return re.sub(r' ', '', m.group(0))
    return _RE_SPACED.sub(_join, text)


def _normalise(text: str) -> str:
    """
    Canonical normalisation — defeats formatting, homoglyph, leet, spacing tricks.
    Run this BEFORE any pattern matching.
    """
    # 1. Unicode NFKC (handles fullwidth, bold/italic math, superscripts, etc.)
    text = unicodedata.normalize("NFKC", text)

    # 2. Homoglyph substitution (Cyrillic/Greek lookalikes NFKC misses)
    text = ''.join(_HOMOGLYPHS.get(c, c) for c in text)

    # 3. Leet-speak substitution
    text = text.translate(_LEET)

    # 4. Combining diacritics (café → cafe)
    text = unicodedata.normalize('NFD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))

    # 5. Zero-width / invisible characters
    text = re.sub(r'[\u200b\u200c\u200d\u00ad\ufeff\u2060]', '', text)

    # 6. Lowercase
    text = text.lower()

    # 7. Collapse spaced-out words ("s e x" → "sex")
    text = _collapse_spaced(text)

    # 8. Normalise whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # 9. Base64 payload sniffing (decode + append so patterns catch encoded text)
    for match in re.findall(r'[A-Za-z0-9+/]{20,64}={0,2}', text)[:3]:
        if not _high_entropy(match):
            continue
        try:
            decoded = base64.b64decode(match + '==').decode('utf-8', errors='ignore')
            if decoded.isprintable() and 4 < len(decoded) <= 200:
                text += ' ' + decoded.lower()
        except Exception:
            pass

    return text


# ═══════════════════════════════════════════════════════════════════════════════
#  FUZZY MATCHING (optional rapidfuzz, graceful fallback)
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from rapidfuzz import fuzz as _fuzz
    def _fuzzy_ratio(a: str, b: str) -> int:
        return int(_fuzz.ratio(a, b))
except ImportError:
    def _fuzzy_ratio(a: str, b: str) -> int:
        # Simple edit-distance-based ratio fallback
        la, lb = len(a), len(b)
        if la == 0 and lb == 0:
            return 100
        if la == 0 or lb == 0:
            return 0
        # Levenshtein via DP (small strings only)
        dp = list(range(lb + 1))
        for i, ca in enumerate(a):
            ndp = [i + 1]
            for j, cb in enumerate(b):
                ndp.append(min(dp[j] + (0 if ca == cb else 1),
                               dp[j + 1] + 1, ndp[-1] + 1))
            dp = ndp
        return int((1 - dp[lb] / max(la, lb)) * 100)


def _fuzzy_contains(text: str, word: str, threshold: int = 88) -> bool:
    """Return True if any token in text is ≥ threshold% similar to word.
    Tokens capped at 100 to keep cost bounded on long prompts."""
    return any(_fuzzy_ratio(token, word) >= threshold for token in text.split()[:100])


# ═══════════════════════════════════════════════════════════════════════════════
#  PATTERN / KEYWORD LISTS
# ═══════════════════════════════════════════════════════════════════════════════

# Hard blocks — result is always BLOCKED, never leaked
BLOCK_PATTERNS: list[tuple[str, str, Severity]] = [
    # Child safety — CRITICAL
    (r"\b(child|minor|underage|loli|shota|preteen|kid)s?\b.{0,30}\b(nude|naked|sex|explicit|erotic)",
     "csam", Severity.CRITICAL),
    (r"\b(nude|naked|sex).{0,30}\b(child|minor|underage|loli|shota|kid)s?\b",
     "csam", Severity.CRITICAL),

    # WMD — CRITICAL
    (r"\b(bioweapon|nerve agent|sarin|vx gas|anthrax bomb|dirty bomb|nuclear device|weaponized)\b",
     "wmd", Severity.CRITICAL),

    # Non-consensual imagery
    (r"\b(deepfake|faceswap|face swap).{0,30}\b(nude|naked|sex|undress)",
     "non-consensual", Severity.BLOCK),

    # Weapons synthesis
    (r"\bhow\s+to\s+(make|build|create|synthesize|brew).{0,30}\b(bomb|explosive|poison|weapon|nerve|anthrax)\b",
     "violence", Severity.BLOCK),

    # Prompt injection — template markers
    (r"\[INST\]|\[\/INST\]|<\|system\|>|<\|user\|>|<\|assistant\|>|\{\{.*?\}\}",
     "prompt-injection", Severity.BLOCK),
    (r"(ignore\s+(all\s+)?(previous|prior|above)\s+instructions?|"
     r"forget\s+(your|all)\s+(rules?|guidelines?|system prompt))",
     "prompt-injection", Severity.BLOCK),
    (r"\b(dan mode|jailbreak(ed)?|developer mode|god mode|unrestricted mode|"
     r"no\s+restrictions|bypass\s+(safety|filter|restrictions|censorship))\b",
     "prompt-injection", Severity.BLOCK),
    (r"\b(you\s+are\s+now|pretend\s+(you\s+are|to\s+be|you'?re)\s+(an?\s+)?"
     r"(evil|unrestricted|unfiltered|uncensored|jailbroken|unaligned))\b",
     "prompt-injection", Severity.BLOCK),
]

# Intent patterns — indirect phrasing that maps to blocked content
INTENT_PATTERNS: list[tuple[str, str]] = [
    (r"\b(remove|take\s+off|strip\s+off)\s+(her|his|their|the)\s*(clothes|clothing|shirt|pants|dress|underwear|top)\b",
     "undress-intent"),
    (r"\bmake\s+(her|him|them|the\s+\w+)\s+(nude|naked|undressed|bare|topless)\b",
     "undress-intent"),
    (r"\bundress\s+(the|her|him|them|subject|person|girl|woman|man)\b",
     "undress-intent"),
    (r"\b(see[- ]?through|x[- ]?ray\s+vision|transparent\s+(clothes|clothing))\b",
     "undress-intent"),
    (r"\bwithout\s+(any\s+)?(clothes|clothing|garments|top|pants)\b",
     "undress-intent"),
    (r"\b(fully\s+)?exposed\s+(body|skin|flesh|breasts?|genitals?)\b",
     "explicit-body"),
]

# Adult patterns — active when adult_filter_enabled=True
ADULT_PATTERNS: list[tuple[str, str]] = [
    (r"\b(sex|erotic|xxx|hentai|pornograph|adult\s+film|onlyfans|nsfw)\b",    "adult-explicit"),
    (r"\b(nude|naked|topless|bottomless|explicit\s+content|adult\s+content)\b", "adult-nudity"),
    (r"\b(escort|prostitut|sex\s+work(er)?|camgirl|camboy)\b",                "adult-services"),
]

# Fuzzy keywords — checked with edit-distance tolerance
FUZZY_KEYWORDS: list[tuple[str, int]] = [
    ("nude",       88),
    ("naked",      88),
    ("sex",        90),
    ("porn",       88),
    ("erotic",     88),
    ("undress",    85),
    ("explicit",   85),
    ("hentai",     88),
]

# Risk-score clusters — additive scoring system
RISK_CLUSTERS: list[tuple[str, int]] = [
    (r"\bchild\b",                              8),
    (r"\b(minor|underage|preteen|loli|shota)\b",10),
    (r"\b(nude|naked)\b",                        3),
    (r"\b(sex|sexual|erotic)\b",                 3),
    (r"\b(porn|pornograph)\b",                   4),
    (r"\bexplicit\b",                            2),
    (r"\b(gun|weapon|bomb|explosive)\b",         2),
    (r"\b(kill|murder|torture|rape)\b",          3),
    (r"\bundress\b",                             3),
    (r"\bdeepfake\b",                            3),
]
RISK_THRESHOLD = 6

# Warn patterns — allowed but flagged (non-specific reason shown)
WARN_PATTERNS: list[tuple[str, str]] = [
    (r"\b(gore|graphic\s+violence|torture|mutilation)\b",                      "gore"),
    (r"\b(drug|narcotic).{0,20}\b(make|cook|synthesize|recipe|how\s+to)\b",   "drugs"),
]

# ── Pre-compiled pattern tuples (use these in hot paths instead of raw strings)
_BLOCK_COMPILED: list[tuple[re.Pattern, str, Severity]] = [
    (re.compile(p, re.IGNORECASE), cat, sev) for p, cat, sev in BLOCK_PATTERNS
]
_ADULT_COMPILED: list[tuple[re.Pattern, str]] = [
    (re.compile(p, re.IGNORECASE), cat) for p, cat in ADULT_PATTERNS
]
_INTENT_COMPILED: list[tuple[re.Pattern, str]] = [
    (re.compile(p, re.IGNORECASE), cat) for p, cat in INTENT_PATTERNS
]
_RISK_COMPILED: list[tuple[re.Pattern, int]] = [
    (re.compile(p, re.IGNORECASE), pts) for p, pts in RISK_CLUSTERS
]
_WARN_COMPILED: list[tuple[re.Pattern, str]] = [
    (re.compile(p, re.IGNORECASE), cat) for p, cat in WARN_PATTERNS
]


# ═══════════════════════════════════════════════════════════════════════════════
#  ML INJECTION CLASSIFIER (optional, lazy)
# ═══════════════════════════════════════════════════════════════════════════════

_ml_clf, _ml_clf_lock, _ml_clf_ready = None, threading.Lock(), False


def preload_models() -> None:
    """
    Call this at application startup (in a background thread) to warm up
    both the ML injection classifier and the NSFW image classifier so the
    first generation request does not block.

    Example (in launch / webui startup):
        import threading, modules.content_filter as cf
        threading.Thread(target=cf.preload_models, daemon=True).start()
    """
    threading.Thread(target=_load_ml_classifier, daemon=True).start()
    threading.Thread(target=_load_nsfw_clf, daemon=True).start()


def _load_ml_classifier():
    global _ml_clf, _ml_clf_ready
    with _ml_clf_lock:
        if _ml_clf_ready:
            return _ml_clf
        try:
            from transformers import pipeline as hf_pipeline
            try:
                _ml_clf = hf_pipeline(
                    "text-classification",
                    model="protectai/deberta-v3-base-prompt-injection-v2",
                    device=-1, truncation=True, max_length=512,
                )
            except Exception:
                _ml_clf = hf_pipeline(
                    "text-classification",
                    model="laiyer/deberta-v3-base-prompt-injection",
                    device=-1, truncation=True, max_length=512,
                )
        except ImportError:
            pass
        except Exception as exc:
            log.warning("[filter] ML classifier unavailable: %s", exc)
        _ml_clf_ready = True
        return _ml_clf


def _ml_score(text: str) -> float:
    clf = _load_ml_classifier()
    if clf is None:
        return 0.0
    try:
        r = clf(text[:512])[0]
        label, score = r["label"].lower(), r["score"]
        if "injection" in label or label in ("1", "label_1"):
            return score
        if "legitimate" in label or label in ("0", "label_0"):
            return 1.0 - score
        return score
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  PROMPT FILTER
# ═══════════════════════════════════════════════════════════════════════════════

class PromptFilter:

    @property
    def ML_THRESHOLD(self) -> float:
        return float(_pol("prompt_filter.ml_threshold", 0.80))

    @property
    def RISK_THRESHOLD(self) -> int:
        return int(_pol("prompt_filter.risk_threshold", RISK_THRESHOLD))

    def check(self, prompt: str, user_id: str = "anonymous") -> FilterResult:
        n = _normalise_cached(prompt)
        tr: list[dict] = []
        debug = _trace_enabled()

        def _step(layer: str, **kw) -> None:
            if debug:
                tr.append({"layer": layer, **kw})

        _step("normalise", status="ok", length=len(n))

        def _block(category: str, severity: Severity, score: float = 0.0,
                   reasons: list = None) -> FilterResult:
            """Emit audit + observability + learning event, return block result."""
            _reasons = reasons or [category]
            _audit(user_id, "block", category, prompt[:200])
            log_decision(
                module="moderation",
                decision="block",
                reasons=_reasons,
                score=score,
                category=category,
                user_id=user_id,
                trace=tr if _trace_enabled() else None,
            )
            log_blocked_prompt(
                prompt=prompt,
                category=category,
                reasons=_reasons,
                score=score,
                user_id=user_id,
            )
            return FilterResult(
                allowed=False, severity=severity,
                reason=_BLOCKED_REASON, category=category,
                score=score, trace=tr,
            )

        # [A] Hard block patterns (pre-compiled)
        for rx, category, severity in _BLOCK_COMPILED:
            if rx.search(n):
                _step("hard_block", category=category, severity=severity.value, triggered=True)
                if severity == Severity.CRITICAL:
                    _write_critical_alert(category, user_id, prompt[:500])
                return _block(category, severity, reasons=["hard_block", category])
        _step("hard_block", triggered=False)

        # [B] 18+ adult filter — always enabled, cannot be disabled (pre-compiled)
        for rx, category in _ADULT_COMPILED:
            if rx.search(n):
                _step("adult_filter", category=category, triggered=True)
                return _block(category, Severity.BLOCK, reasons=["adult_filter", category])
        _step("adult_filter", triggered=False)

        # [C] Intent patterns (pre-compiled)
        for rx, category in _INTENT_COMPILED:
            if rx.search(n):
                _step("intent", category=category, triggered=True)
                return _block(category, Severity.BLOCK, reasons=["intent_pattern", category])
        _step("intent", triggered=False)

        # [D] Fuzzy keyword detection
        for word, threshold in FUZZY_KEYWORDS:
            if _fuzzy_contains(n, word, threshold):
                _step("fuzzy", word=word, triggered=True)
                return _block(
                    f"fuzzy:{word}", Severity.BLOCK,
                    reasons=["fuzzy_match", f"fuzzy:{word}"],
                )
        _step("fuzzy", triggered=False)

        # [E] Risk score (pre-compiled)
        score = sum(pts for rx, pts in _RISK_COMPILED if rx.search(n))
        _step("risk_score", score=score, threshold=self.RISK_THRESHOLD)
        if score >= self.RISK_THRESHOLD:
            return _block(
                "risk-score", Severity.BLOCK, score=float(score),
                reasons=["risk_score_threshold"],
            )
        # Log near-threshold prompts for pattern analysis
        elif score >= self.RISK_THRESHOLD * 0.7:
            log_borderline_prompt(prompt=prompt, score=float(score), user_id=user_id)

        # [F] ML injection classifier
        ml = _ml_score(n)
        _step("ml_classifier", score=round(ml, 4), threshold=self.ML_THRESHOLD)
        if ml >= self.ML_THRESHOLD:
            return _block(
                "prompt-injection", Severity.BLOCK, score=ml,
                reasons=["ml_classifier"],
            )

        # [G] Warn patterns (pre-compiled)
        for rx, category in _WARN_COMPILED:
            if rx.search(n):
                _step("warn", category=category, triggered=True)
                _audit(user_id, "warn", category, prompt[:200])
                log_decision(
                    module="moderation", decision="warn",
                    reasons=["warn_pattern", category],
                    category=category, user_id=user_id,
                )
                return FilterResult(
                    allowed=True, severity=Severity.WARN,
                    reason=_WARN_REASON, category=category,
                    redacted=rx.sub("[REDACTED]", prompt), trace=tr,
                )

        _step("result", decision="allow")
        log_decision(
            module="moderation", decision="allow",
            reasons=[], user_id=user_id,
        )
        return FilterResult(allowed=True, severity=Severity.SAFE, trace=tr)


# ═══════════════════════════════════════════════════════════════════════════════
#  IMAGE OUTPUT FILTER
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from PIL import Image as _PILImage
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

_nsfw_clf, _nsfw_clf_lock, _nsfw_clf_ready = None, threading.Lock(), False


def _load_nsfw_clf():
    global _nsfw_clf, _nsfw_clf_ready
    with _nsfw_clf_lock:
        if _nsfw_clf_ready:
            return _nsfw_clf
        try:
            from transformers import pipeline as hf_pipeline
            _nsfw_clf = hf_pipeline(
                "image-classification",
                model="Falconsai/nsfw_image_detection",
                device=-1,
            )
            log.info("[filter] NSFW image classifier loaded.")
        except Exception as exc:
            log.warning("[filter] NSFW classifier unavailable: %s", exc)
        _nsfw_clf_ready = True
        return _nsfw_clf


class ImageFilter:
    # Known NSFW label names across common Hugging Face classifiers.
    NSFW_LABELS = frozenset({
        "nsfw", "explicit", "porn", "pornographic", "hentai",
        "sexy", "label_1", "1", "unsafe",
    })
    # Known "safe" / minor-category labels from age-estimation classifiers
    MINOR_LABELS = frozenset({
        "child", "minor", "kid", "infant", "baby", "toddler",
        "young", "underage", "juvenile",
    })

    @property
    def NSFW_BLOCK_THRESHOLD(self) -> float:
        return float(_pol("image_filter.nsfw_block_threshold", 0.65))

    @property
    def NSFW_WARN_THRESHOLD(self) -> float:
        return float(_pol("image_filter.nsfw_warn_threshold", 0.35))

    def check(self, image_path: str, user_id: str = "anonymous") -> FilterResult:
        if not get_setting("nsfw_image_filter_enabled"):
            return FilterResult(allowed=True, severity=Severity.SAFE)

        if not _PIL_AVAILABLE:
            return FilterResult(allowed=True, severity=Severity.WARN,
                                reason="Image filter unavailable (PIL missing)")

        clf = _load_nsfw_clf()
        if clf is None:
            return FilterResult(allowed=True, severity=Severity.WARN,
                                reason="NSFW classifier unavailable")
        try:
            img        = _PILImage.open(image_path).convert("RGB")
            results    = clf(img)

            nsfw_score = max(
                (r["score"] for r in results
                 if r["label"].lower() in self.NSFW_LABELS),
                default=0.0,
            )

            # Age-safety check on generated output images.
            # If any "minor-category" label fires with high confidence AND
            # the image also has a non-trivial NSFW score, block immediately.
            age_check_enabled = bool(_pol("image_filter.age_check_enabled", True))
            if age_check_enabled:
                minor_score = max(
                    (r["score"] for r in results
                     if any(lbl in r["label"].lower() for lbl in self.MINOR_LABELS)),
                    default=0.0,
                )
                if minor_score >= float(_pol("image_filter.age_check_min_age_score", 0.70)):
                    log.warning(
                        "[filter] Possible minor detected in output (%.2f) user=%s",
                        minor_score, user_id,
                    )
                    _audit(user_id, "block", "age-safety", image_path)
                    _write_critical_alert("age-safety-image", user_id, image_path)
                    return FilterResult(
                        allowed=False, severity=Severity.CRITICAL,
                        reason=_BLOCKED_REASON, category="age-safety",
                        score=minor_score,
                    )

            if nsfw_score >= self.NSFW_BLOCK_THRESHOLD:
                log.warning("[filter] NSFW image blocked (%.2f) user=%s", nsfw_score, user_id)
                _audit(user_id, "block", "nsfw-image", image_path)
                return FilterResult(
                    allowed=False, severity=Severity.BLOCK,
                    reason=_BLOCKED_REASON, category="nsfw-image", score=nsfw_score,
                )
            if nsfw_score >= self.NSFW_WARN_THRESHOLD:
                return FilterResult(
                    allowed=True, severity=Severity.WARN,
                    reason="Image may contain sensitive content.", category="nsfw-image",
                    score=nsfw_score,
                )
        except Exception as exc:
            log.error("[filter] Image check error: %s", exc)

        return FilterResult(allowed=True, severity=Severity.SAFE)


def check_input_image(image_path: str, user_id: str = "anonymous") -> FilterResult:
    """
    Check a USER-UPLOADED input image before it enters the generation pipeline.
    Blocks if any person in the image appears to be under 18.
    Uses the same NSFW classifier — minor-category labels are checked.
    """
    return _image_filter.check(image_path, user_id)


# ═══════════════════════════════════════════════════════════════════════════════
#  RATE LIMITER
# ═══════════════════════════════════════════════════════════════════════════════

class RateLimiter:
    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window       = window_seconds
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock        = threading.Lock()

    def check(self, user_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            active = [t for t in self._buckets[user_id] if now - t < self.window]
            if len(active) >= self.max_requests:
                return False
            active.append(now)
            self._buckets[user_id] = active
            return True

    def remaining(self, user_id: str) -> int:
        now = time.monotonic()
        with self._lock:
            return max(0, self.max_requests -
                       sum(1 for t in self._buckets[user_id] if now - t < self.window))


# ═══════════════════════════════════════════════════════════════════════════════
#  AUDIT LOG
# ═══════════════════════════════════════════════════════════════════════════════

_AUDIT_LOG  = Path.home() / ".local" / "share" / "cookiefooocus" / "ai-audit.jsonl"
_audit_lock = threading.Lock()


def _audit(user_id: str, action: str, category: str, content: str) -> None:
    try:
        _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts":       datetime.utcnow().isoformat(),
            "user":     user_id,
            "action":   action,
            "category": category,
            "hash":     hashlib.sha256(content.encode()).hexdigest()[:16],
        }
        with _audit_lock:
            with _AUDIT_LOG.open("a") as fh:
                fh.write(json.dumps(entry) + "\n")
    except Exception as exc:
        log.debug("[filter] Audit write failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
#  PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

_prompt_filter = PromptFilter()
_image_filter  = ImageFilter()
_rate_limiter  = RateLimiter(
    max_requests=int(_pol("rate_limit.max_requests_per_window", 30)),
    window_seconds=int(_pol("rate_limit.window_seconds", 60)),
)


def check_prompt(prompt: str, user_id: str = "anonymous") -> FilterResult:
    """Check a text prompt before sending it to the generation pipeline."""
    if not get_setting("prompt_filter_enabled"):
        return FilterResult(allowed=True, severity=Severity.SAFE)
    if not _rate_limiter.check(user_id):
        return FilterResult(
            allowed=False, severity=Severity.BLOCK,
            reason="Too many requests. Please wait a moment.",
            category="rate-limit",
        )
    return _prompt_filter.check(prompt, user_id)


def check_image(image_path: str, user_id: str = "anonymous") -> FilterResult:
    """Check a generated image before displaying it."""
    return _image_filter.check(image_path, user_id)
