"""
Cookie-Fooocus — Generation Controller (package)
────────────────────────────────────────────────────────────────────────────────
Façade over the split sub-modules.  Import the controller singleton from here
and use it — never import sub-modules directly in application code.

Sub-modules (each owns one responsibility):
  scheduler        — priority queue, job lifecycle, starvation, cancellation
  resource_manager — VRAM governor, hardware profile, quality auto-scaling
  cache (package)  — split caches with separate lifecycles

Worker model:
  Only the GPU worker process touches the diffusion pipeline.
  The controller sits in the API / UI process and communicates parameters.
  In local mode the "worker" is the same process; in server mode it can be
  a subprocess (future: multiprocessing worker for CUDA process isolation).

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from modules.generation_controller.scheduler       import scheduler, Job, JobState
from modules.generation_controller.resource_manager import governor, get_hardware_profile, GenParams

log = logging.getLogger("cookiefooocus.controller")


class GenerationController:
    """
    High-level façade used by callers (webui, n8n handler, video router).

    Owns NO GPU state.  Only co-ordinates scheduling and cache lookups.
    """

    def __init__(self):
        self._scheduler = scheduler
        self._governor  = governor

        try:
            from modules.telemetry import telemetry
            self._telemetry = telemetry
        except ImportError:
            self._telemetry = None

        from modules.cache import prompt_cache, nsfw_cache
        self._prompt_cache = prompt_cache
        self._nsfw_cache   = nsfw_cache

    # ── Prompt expansion (cache-aware) ─────────────────────────────────────────

    def expand_prompt(self, prompt: str, seed: int, mode: str = "balanced") -> str:
        """
        Return expanded prompt, using cache to avoid redundant LLM calls.
        The prompt cache has no TTL — expansion is deterministic.
        """
        cached = self._prompt_cache.get(prompt, seed, mode)
        if cached is not None:
            log.debug("[ctrl] Prompt cache hit.")
            return cached

        t0 = time.perf_counter()
        try:
            from modules.prompt_engine import engine, PromptEngine
            pm     = PromptEngine.mode_from_string(mode)
            result = engine.run(prompt, seed=seed, mode=pm)
            expanded = result.expanded
        except Exception as exc:
            log.warning("[ctrl] PromptEngine error (%s) — using raw prompt.", exc)
            expanded = prompt

        elapsed_ms = (time.perf_counter() - t0) * 1000
        self._prompt_cache.put(prompt, seed, mode, expanded)

        if self._telemetry:
            self._telemetry.record("prompt_expand_ms", elapsed_ms)

        return expanded

    # ── VRAM governor ──────────────────────────────────────────────────────────

    def check_resources(
        self,
        width:     int   = 1024,
        height:    int   = 1024,
        steps:     int   = 30,
        precision: str   = "fp16",
    ):
        """
        Pre-flight VRAM check.  Returns (ok: bool, params: GenParams).
        If ok=False, the request should be rejected — not queued.
        """
        params = GenParams(width=width, height=height, steps=steps, precision=precision)
        return self._governor.check_and_scale(params)

    # ── Scheduling ─────────────────────────────────────────────────────────────

    @property
    def queue(self):
        """Expose scheduler for direct slot acquisition."""
        return self._scheduler

    def slot(self, priority: int = 0, job_id: Optional[str] = None, timeout_s: float = 600.0):
        """Convenience: scheduler.slot(...)"""
        return self._scheduler.slot(priority=priority, job_id=job_id, timeout_s=timeout_s)

    # ── NSFW cache ─────────────────────────────────────────────────────────────

    def get_nsfw_score(self, image_path: str) -> Optional[float]:
        return self._nsfw_cache.get(image_path)

    def store_nsfw_score(self, image_path: str, score: float) -> None:
        self._nsfw_cache.put(image_path, score)

    # ── Status snapshot ────────────────────────────────────────────────────────

    def status(self) -> dict:
        from modules.cache import all_stats as cache_stats
        hw = get_hardware_profile()
        return {
            "queue":    self._scheduler.stats(),
            "cache":    cache_stats(),
            "hardware": {
                "vram_total_gb":    hw.vram_total_gb,
                "ram_total_gb":     hw.ram_total_gb,
                "is_apple_silicon": hw.is_apple_silicon,
                "optimal_batch":    hw.optimal_batch,
                "default_precision": hw.default_precision,
            },
            "telemetry": self._telemetry.snapshot() if self._telemetry else {},
        }


# Singleton — import and use directly
controller = GenerationController()

__all__ = ["controller", "GenerationController", "scheduler", "governor", "Job", "JobState", "GenParams"]
