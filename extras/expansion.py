# Cookie-Fooocus Prompt Expansion — Ollama / Gemma 4 backend
# Replaces the original GPT-2 Fooocus V2 expansion engine with a local
# Ollama-served Gemma 4 model.  The public interface is identical so the
# rest of the codebase requires no changes.
#
# Requirements:
#   • Ollama running locally  (https://ollama.com)
#   • Gemma 4 pulled:  ollama pull gemma4
#
# The Ollama server is expected at http://localhost:11434 by default.
# Override with the environment variable OLLAMA_HOST, e.g.:
#   OLLAMA_HOST=http://192.168.1.10:11434 python entry_with_update.py

import os
import json
import random
import urllib.request
import urllib.error
import logging

log = logging.getLogger("cookiefooocus.expansion")

_OLLAMA_HOST  = os.environ.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
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


class FooocusExpansion:
    """
    Drop-in replacement for the original GPT-2 FooocusExpansion.
    Uses Ollama with Gemma 4 for prompt expansion.

    The `patcher` attribute is set to None — the Ollama server manages its
    own memory.  default_pipeline.py skips GPU loading when patcher is None.
    """

    patcher = None  # No GPU patcher needed — Ollama manages its own memory

    def __init__(self):
        self._check_ollama()

    def _check_ollama(self) -> None:
        """Verify Ollama is reachable and the model is available at startup."""
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
        except Exception as exc:
            log.warning(
                "[expansion] Could not reach Ollama at %s (%s). "
                "Prompt expansion will pass through unchanged.", _OLLAMA_HOST, exc
            )

    def __call__(self, prompt: str, seed: int) -> str:
        """
        Expand prompt using Gemma 4 via Ollama.
        Returns the original prompt unchanged if Ollama is unavailable.
        """
        if not prompt or not prompt.strip():
            return ""

        prompt = safe_str(prompt)

        # Use seed to influence temperature slightly for reproducibility
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
