"""
Cookie-Fooocus — Generation Controller (package)
────────────────────────────────────────────────────────────────────────────────
Façade over the split sub-modules.  Import the controller singleton from here
and use it — never import sub-modules directly in application code.

Sub-modules (each owns one responsibility):
  scheduler        — priority queue, job lifecycle, starvation, cancellation
  resource_manager — VRAM governor, hardware profile, predictive downscaling
  decision_chain   — per-job audit log of every parameter decision
  cache (package)  — L1 memory + L2 SQLite, separate lifecycles per cache type

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Tuple

from modules.generation_controller.scheduler        import scheduler, Job, JobState, TooManyJobsError
from modules.generation_controller.resource_manager import governor, get_hardware_profile, GenParams
from modules.generation_controller.decision_chain   import DecisionChain

log = logging.getLogger("cookiefooocus.controller")


class GenerationController:
    """
    High-level façade used by callers (webui, n8n handler, video router).
    Owns NO GPU state.  Only co-ordinates scheduling, caching, and resource checks.
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

    def expand_prompt(
        self,
        prompt: str,
        seed:   int,
        mode:   str = "balanced",
        chain:  Optional[DecisionChain] = None,
    ) -> str:
        """
        Return expanded prompt, using L1/L2 cache to avoid redundant LLM calls.
        Records cache hit/miss into the decision chain if provided.
        """
        cached = self._prompt_cache.get(prompt, seed, mode)
        if cached is not None:
            log.debug("[ctrl] Prompt cache hit.")
            if chain:
                chain.record(stage="prompt_cache", action="hit", reason="l1_or_l2_cache")
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
            if chain:
                chain.record(
                    stage="prompt_engine", action="fallback",
                    reason=f"engine_error: {exc}",
                )

        elapsed_ms = (time.perf_counter() - t0) * 1000
        self._prompt_cache.put(prompt, seed, mode, expanded)

        if self._telemetry:
            self._telemetry.record("prompt_expand_ms", elapsed_ms)

        if chain:
            chain.record(stage="prompt_cache", action="miss", reason="recomputed_and_cached")

        return expanded

    # ── VRAM governor ──────────────────────────────────────────────────────────

    def check_resources(
        self,
        width:     int   = 1024,
        height:    int   = 1024,
        steps:     int   = 30,
        precision: str   = "fp16",
        chain:     Optional[DecisionChain] = None,
    ) -> Tuple[bool, GenParams]:
        """
        Pre-flight VRAM check with decision chain logging.
        Returns (ok: bool, params: GenParams).
        If ok=False the request must be rejected — not queued.
        """
        params = GenParams(width=width, height=height, steps=steps, precision=precision)
        ok, adjusted = self._governor.check_and_scale(params, chain=chain)

        if chain and ok and not adjusted.downscaled:
            chain.record(
                stage="cost_validator",
                action="approve",
                reason="within_budget",
            )
        elif chain and not ok:
            chain.record(
                stage="cost_validator",
                action="reject",
                reason="insufficient_vram",
            )

        return ok, adjusted

    # ── Scheduling ─────────────────────────────────────────────────────────────

    @property
    def queue(self):
        return self._scheduler

    def slot(
        self,
        priority:  int   = 0,
        job_id:    Optional[str] = None,
        timeout_s: float = 600.0,
        user_id:   str   = "",
        chain:     Optional[DecisionChain] = None,
    ):
        """
        Acquire a generation slot.  Raises TooManyJobsError if the per-user
        limit is exceeded.  Records the scheduling decision into the chain.
        """
        if chain:
            chain.record(
                stage="scheduler",
                action="acquire_slot",
                reason=f"priority={priority} user={user_id or 'anonymous'}",
            )
        return self._scheduler.slot(
            priority=priority, job_id=job_id, timeout_s=timeout_s, user_id=user_id
        )

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
                "vram_total_gb":     hw.vram_total_gb,
                "ram_total_gb":      hw.ram_total_gb,
                "is_apple_silicon":  hw.is_apple_silicon,
                "optimal_batch":     hw.optimal_batch,
                "default_precision": hw.default_precision,
            },
            "vram_feedback": self._governor.feedback_stats(),
            "telemetry":     self._telemetry.snapshot() if self._telemetry else {},
        }


# Singleton — import and use directly
controller = GenerationController()

__all__ = [
    "controller", "GenerationController",
    "scheduler", "governor",
    "DecisionChain",
    "Job", "JobState", "TooManyJobsError", "GenParams",
]
