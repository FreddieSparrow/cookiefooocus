# Cookie-Fooocus Prompt Expansion
# ─────────────────────────────────────────────────────────────────────────────
# Selects expansion backend based on hardware capability:
#
#   • Ollama / Gemma 4  — used when hardware meets minimum requirements:
#       Apple Silicon Mac  ≥ 32 GB unified memory
#       PC                 ≥ 26 GB RAM  AND  ≥ 12 GB VRAM
#
#   • GPT-2 local model — original Fooocus V2 expansion engine, used as a
#       fallback when Ollama requirements are not met.
#
# The public interface (__call__(prompt, seed) → str) is identical for both
# backends, so the rest of the codebase requires no changes.
#
# Provided by CookieHostUK — coded with Claude AI assistance.
# ─────────────────────────────────────────────────────────────────────────────

import os
import json
import logging
import random
import urllib.request
import urllib.error

log = logging.getLogger("cookiefooocus.expansion")

_OLLAMA_HOST  = os.environ.get("OLLAMA_HOST",  "http://localhost:11434").rstrip("/")
_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4")

_SYSTEM_PROMPT = (
    "You are a creative assistant that improves image generation prompts. "
    "When given a short or simple prompt, expand it into a rich, detailed description "
    "suitable for a high-quality image generator. "
    "Add specific details about lighting, composition, style, mood, and texture. "
    "Return ONLY the expanded prompt text — no explanations, no preamble, no quotes. "
    "Keep the result under 150 words."
)


def safe_str(x: str) -> str:
    """Strip leading/trailing punctuation and collapse whitespace."""
    x = str(x)
    for _ in range(16):
        x = x.replace("  ", " ")
    return x.strip(",. \r\n")


# ── Ollama backend ─────────────────────────────────────────────────────────────

class _OllamaExpansion:
    """Prompt expansion via a locally-served Ollama model."""

    patcher = None  # No GPU patcher needed — Ollama manages its own memory

    def __init__(self):
        self._reachable = False
        self._check_ollama()

    def _check_ollama(self) -> None:
        try:
            url = f"{_OLLAMA_HOST}/api/tags"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
            models = [m["name"].split(":")[0] for m in data.get("models", [])]
            if _OLLAMA_MODEL not in models:
                log.warning(
                    "[expansion] Model '%s' not found in Ollama. "
                    "Run: ollama pull %s", _OLLAMA_MODEL, _OLLAMA_MODEL
                )
            else:
                log.info("[expansion] Ollama / %s ready at %s", _OLLAMA_MODEL, _OLLAMA_HOST)
                self._reachable = True
        except Exception as exc:
            log.warning(
                "[expansion] Could not reach Ollama at %s (%s). "
                "Prompt expansion will pass through unchanged.", _OLLAMA_HOST, exc
            )

    def __call__(self, prompt: str, seed: int) -> str:
        if not prompt or not prompt.strip():
            return ""
        prompt = safe_str(prompt)
        if not self._reachable:
            return prompt

        rng = random.Random(int(seed))
        temperature = round(0.7 + rng.uniform(-0.1, 0.1), 2)

        payload = json.dumps({
            "model":  _OLLAMA_MODEL,
            "prompt": prompt,
            "system": _SYSTEM_PROMPT,
            "stream": False,
            "options": {
                "temperature": temperature,
                "seed":        int(seed) % (2 ** 31),
                "num_predict": 200,
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
            expanded = safe_str(result.get("response", ""))
            if expanded:
                log.debug("[expansion] '%s' → '%s'", prompt[:60], expanded[:60])
                return expanded
        except urllib.error.URLError as exc:
            log.warning("[expansion] Ollama unreachable: %s — using original prompt.", exc)
        except Exception as exc:
            log.warning("[expansion] Expansion failed: %s — using original prompt.", exc)

        return prompt


# ── GPT-2 fallback backend ─────────────────────────────────────────────────────

class _GPT2Expansion:
    """
    Original Fooocus V2 expansion engine (GPT-2 local model).
    Used as a fallback when hardware requirements for Ollama are not met.
    """

    def __init__(self):
        import modules.config as config
        from ldm_patched.modules.model_patcher import ModelPatcher

        self.patcher = None
        self._model  = None
        self._tokenizer = None
        self._device = None

        try:
            from transformers import GPT2LMHeadModel, GPT2Tokenizer
            import torch

            model_path = config.path_fooocus_expansion
            if not os.path.isdir(model_path):
                log.warning("[expansion-gpt2] Model dir not found: %s", model_path)
                return

            self._tokenizer = GPT2Tokenizer.from_pretrained(model_path)
            self._model     = GPT2LMHeadModel.from_pretrained(model_path)

            if torch.cuda.is_available():
                self._device = torch.device("cuda")
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                self._device = torch.device("mps")
            else:
                self._device = torch.device("cpu")

            self._model.to(self._device).eval()
            log.info("[expansion-gpt2] GPT-2 expansion loaded on %s", self._device)
        except Exception as exc:
            log.warning("[expansion-gpt2] Failed to load GPT-2 model: %s", exc)

    def __call__(self, prompt: str, seed: int) -> str:
        if not prompt or not prompt.strip():
            return ""
        prompt = safe_str(prompt)
        if self._model is None or self._tokenizer is None:
            return prompt

        try:
            import torch
            inputs = self._tokenizer(prompt, return_tensors="pt").to(self._device)
            with torch.no_grad():
                outputs = self._model.generate(
                    **inputs,
                    max_new_tokens=75,
                    do_sample=True,
                    temperature=0.7,
                    pad_token_id=self._tokenizer.eos_token_id,
                    seed=int(seed) % (2 ** 31) if hasattr(torch, "manual_seed") else None,
                )
            text = self._tokenizer.decode(outputs[0], skip_special_tokens=True)
            expanded = safe_str(text)
            if expanded and len(expanded) > len(prompt):
                return expanded
        except Exception as exc:
            log.warning("[expansion-gpt2] Inference failed: %s", exc)

        return prompt


# ── Hardware-gated factory ─────────────────────────────────────────────────────

def _build_expansion():
    """
    Check hardware, log the result, and return the appropriate expansion backend.
    Ollama is used only when hardware requirements are met; GPT-2 otherwise.
    """
    try:
        from modules.hardware_check import check_ollama_capable
        capable, reason = check_ollama_capable()
        log.info("[expansion] Hardware check: %s", reason)
        if capable:
            return _OllamaExpansion()
    except Exception as exc:
        log.warning("[expansion] Hardware check failed (%s) — falling back to GPT-2.", exc)

    return _GPT2Expansion()


# ── Public class (drop-in replacement) ────────────────────────────────────────

class FooocusExpansion:
    """
    Public drop-in for the original FooocusExpansion class.
    Delegates to Ollama or GPT-2 depending on hardware capability.
    Caches expansion results to avoid redundant LLM/GPT-2 calls.
    """

    def __init__(self):
        self._backend = _build_expansion()
        self.patcher  = getattr(self._backend, "patcher", None)
        try:
            from modules.performance import prompt_cache
            self._cache = prompt_cache
        except ImportError:
            self._cache = None

    def __call__(self, prompt: str, seed: int) -> str:
        if self._cache is not None:
            cached = self._cache.get(prompt, seed)
            if cached is not None:
                log.debug("[expansion] Cache hit for prompt: %s…", prompt[:40])
                return cached

        result = self._backend(prompt, seed)

        if self._cache is not None and result != prompt:
            self._cache.put(prompt, seed, result)

        return result
