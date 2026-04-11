"""
Cookie-Fooocus — Resource Manager / VRAM Governor
────────────────────────────────────────────────────────────────────────────────
Pre-execution VRAM budget check with predictive estimation, automatic quality
downscaling, and feedback-corrected coefficient learning.

Before any generation request touches the GPU, this module:
  1. Reads current free VRAM (once per job, not at module import)
  2. Estimates memory required using a predictive model:
       estimated = BASE_MODEL + (megapixels × pixel_cost) + (steps × step_cost)
  3. If budget is tight: scales down resolution / steps / precision
  4. If budget is critically low: rejects the request
  5. After generation, records actual VRAM usage and slowly corrects the
     pixel_cost coefficient using EWA smoothing (alpha=0.1)

Feedback correction design:
  - Predicted vs actual is tracked per completed job
  - Correction factor = alpha × (actual / predicted) + (1 - alpha) × current_factor
  - Clamped to [0.5, 2.0] of baseline — prevents oscillation on outliers
  - Minimum 5 samples before any correction is applied

Decision chain integration:
  check_and_scale() accepts an optional DecisionChain and records what it did.
  If chain=None the function behaves identically but records nothing.

Quality downscaling cascade (applied in order until budget fits):
  Step 1 — reduce steps        (30 → 20)
  Step 2 — reduce resolution   (1024 → 768)
  Step 3 — switch precision    (fp16 → fp8)
  Step 4 — reject              (VRAM below hard floor)

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional, Tuple

log = logging.getLogger("cookiefooocus.resource_manager")

# Minimum free VRAM (GB) below which all generation is rejected
_VRAM_FLOOR_GB = 1.0

# Predictive model constants — empirically derived for SDXL fp16 on typical hardware.
# BASE covers UNet + VAE + CLIP encoders loaded into VRAM.
# PIXEL_COST is activation/KV cache cost per megapixel at fp16.
# STEP_COST is additional VRAM per diffusion step (attention maps, etc.).
_BASE_MODEL_VRAM_GB      = 3.5    # GB — base SDXL model footprint
_PIXEL_COST_FP16_GB      = 0.28   # GB per megapixel at fp16
_STEP_COST_GB            = 0.02   # GB per step (~20 MB/step at 1024×1024)

# EWA feedback correction
_EWA_ALPHA        = 0.1           # slow adaptation — prevents oscillation
_CORRECTION_MIN   = 0.5           # never correct below 50% of baseline
_CORRECTION_MAX   = 2.0           # never correct above 200% of baseline
_MIN_SAMPLES_BEFORE_CORRECTION = 5

# Resolution and step ladders for downscaling
_RESOLUTION_LADDER = [1024, 896, 768, 640, 512]
_STEP_LADDER       = [30, 25, 20, 15]


# ═══════════════════════════════════════════════════════════════════════════════
#  Hardware profile (read once per process)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class HardwareProfile:
    vram_total_gb:     float
    ram_total_gb:      float
    is_apple_silicon:  bool
    has_gpu:           bool
    optimal_batch:     int
    default_precision: str   # "fp16" | "fp8" | "cpu"


@lru_cache(maxsize=1)
def get_hardware_profile() -> HardwareProfile:
    """Read hardware spec once.  Never imports torch at module load time."""
    try:
        from modules.hardware_check import _get_vram_gb, _get_total_ram_gb, _is_apple_silicon
        vram  = _get_vram_gb()
        ram   = _get_total_ram_gb()
        apple = _is_apple_silicon()
    except Exception as exc:
        log.debug("[resources] Hardware detection failed: %s", exc)
        vram, ram, apple = 0.0, 0.0, False

    has_gpu = vram > 0 or apple

    if apple:
        batch     = 4 if ram >= 64 else (2 if ram >= 32 else 1)
        precision = "fp16"
    elif vram >= 16:
        batch, precision = 2, "fp16"
    elif vram >= 8:
        batch, precision = 1, "fp16"
    elif vram >= 4:
        batch, precision = 1, "fp8"
    else:
        batch, precision = 1, "cpu"

    profile = HardwareProfile(
        vram_total_gb=vram,
        ram_total_gb=ram,
        is_apple_silicon=apple,
        has_gpu=has_gpu,
        optimal_batch=batch,
        default_precision=precision,
    )
    log.info(
        "[resources] Hardware: VRAM=%.1fGB RAM=%.1fGB Apple=%s Batch=%d Precision=%s",
        vram, ram, apple, batch, precision,
    )
    return profile


# ═══════════════════════════════════════════════════════════════════════════════
#  VRAM query (live — called per job)
# ═══════════════════════════════════════════════════════════════════════════════

def get_free_vram_gb() -> float:
    """Return current free VRAM in GB.  Returns large value on CPU/Apple (unified memory)."""
    try:
        import torch
        if torch.cuda.is_available():
            free, _ = torch.cuda.mem_get_info()
            return free / (1024 ** 3)
    except Exception:
        pass

    hw = get_hardware_profile()
    if hw.is_apple_silicon:
        return hw.ram_total_gb * 0.6   # conservative estimate of available unified memory
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  Generation parameters (mutable DTO)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GenParams:
    width:      int   = 1024
    height:     int   = 1024
    steps:      int   = 30
    precision:  str   = "fp16"    # "fp16" | "fp8" | "bf16" | "cpu"
    batch:      int   = 1
    downscaled: bool  = False     # True if quality was reduced from original request


# ═══════════════════════════════════════════════════════════════════════════════
#  Feedback-corrected VRAM coefficient
# ═══════════════════════════════════════════════════════════════════════════════

class _VRAMFeedback:
    """
    Tracks predicted vs actual VRAM usage and slowly corrects the pixel_cost
    coefficient using exponentially weighted averaging.

    Design:
      - correction_factor starts at 1.0 (baseline = no correction)
      - After each completed job: factor = α × (actual/predicted) + (1−α) × factor
      - Clamped to [CORRECTION_MIN, CORRECTION_MAX] to prevent oscillation
      - Minimum 5 samples before any factor is applied
    """

    def __init__(self):
        self._factor:  float = 1.0
        self._samples: int   = 0
        self._lock     = threading.Lock()

    def record(self, predicted_gb: float, actual_gb: float) -> None:
        """
        Record one prediction vs actual pair.
        Call this after generation completes with VRAM peak data.
        """
        if predicted_gb <= 0 or actual_gb <= 0:
            return
        ratio = actual_gb / predicted_gb
        with self._lock:
            self._samples += 1
            if self._samples >= _MIN_SAMPLES_BEFORE_CORRECTION:
                new_factor = _EWA_ALPHA * ratio + (1.0 - _EWA_ALPHA) * self._factor
                self._factor = max(_CORRECTION_MIN, min(_CORRECTION_MAX, new_factor))
                log.debug(
                    "[resources] VRAM feedback: predicted=%.2fGB actual=%.2fGB "
                    "ratio=%.2f factor=%.3f (n=%d)",
                    predicted_gb, actual_gb, ratio, self._factor, self._samples,
                )

    @property
    def factor(self) -> float:
        with self._lock:
            return self._factor

    @property
    def samples(self) -> int:
        with self._lock:
            return self._samples

    def stats(self) -> dict:
        with self._lock:
            return {
                "correction_factor": round(self._factor, 4),
                "samples":           self._samples,
                "correction_active": self._samples >= _MIN_SAMPLES_BEFORE_CORRECTION,
            }


_vram_feedback = _VRAMFeedback()


# ═══════════════════════════════════════════════════════════════════════════════
#  VRAM estimation (predictive)
# ═══════════════════════════════════════════════════════════════════════════════

def _estimate_vram_gb(params: GenParams, correction_factor: float = 1.0) -> float:
    """
    Predictive VRAM estimate for given parameters.

    Formula:
        total = base_model + (megapixels × pixel_cost × correction) + (steps × step_cost)

    The correction_factor is updated by _VRAMFeedback as jobs complete.
    At fp8, all variable costs are multiplied by 0.6 (empirical).
    """
    mpix             = (params.width * params.height) / 1_000_000
    precision_factor = 1.0 if params.precision in ("fp16", "bf16") else 0.6
    pixel_cost       = _PIXEL_COST_FP16_GB * precision_factor * correction_factor
    step_cost        = _STEP_COST_GB * precision_factor

    return (
        _BASE_MODEL_VRAM_GB
        + mpix  * pixel_cost * params.batch
        + params.steps * step_cost
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Governor — predictive check with auto-downscale and decision chain logging
# ═══════════════════════════════════════════════════════════════════════════════

class VRAMGovernor:
    """
    Pre-execution VRAM budget check with predictive model and feedback correction.

    Usage:
        params = GenParams(width=1024, height=1024, steps=30)
        ok, params = governor.check_and_scale(params)
        if not ok:
            return "insufficient VRAM"
        # proceed with (possibly adjusted) params

    With decision chain:
        from modules.generation_controller.decision_chain import DecisionChain
        chain = DecisionChain(job_id)
        ok, params = governor.check_and_scale(params, chain=chain)
    """

    def check_and_scale(
        self,
        params: GenParams,
        chain=None,   # Optional[DecisionChain] — avoids circular import
    ) -> Tuple[bool, GenParams]:
        """
        Check if VRAM is sufficient.  Apply downscaling cascade if needed.

        Returns:
            (True,  params)  — generation can proceed (params may be adjusted)
            (False, params)  — insufficient VRAM even after maximum downscaling
        """
        hw             = get_hardware_profile()
        free_gb        = get_free_vram_gb()
        factor         = _vram_feedback.factor
        estimated      = _estimate_vram_gb(params, correction_factor=factor)

        if hw.is_apple_silicon:
            # Unified memory — trust the model management layer (ldm_patched)
            if chain:
                chain.record(
                    stage="vram_model",
                    action="approve",
                    reason="apple_silicon_unified_memory",
                )
            return True, params

        if free_gb >= estimated + 0.5:
            if chain:
                chain.record(
                    stage="vram_model",
                    action="approve",
                    reason=f"comfortable_headroom (free={free_gb:.2f}GB est={estimated:.2f}GB)",
                )
            return True, params  # comfortable headroom

        if free_gb < _VRAM_FLOOR_GB:
            log.warning(
                "[resources] Free VRAM %.2fGB below floor %.2fGB — rejecting job.",
                free_gb, _VRAM_FLOOR_GB,
            )
            if chain:
                chain.record(
                    stage="vram_model",
                    action="reject",
                    reason=f"free_vram_{free_gb:.2f}GB_below_floor_{_VRAM_FLOOR_GB}GB",
                )
            return False, params

        # Downscale cascade
        original = GenParams(
            width=params.width, height=params.height,
            steps=params.steps, precision=params.precision,
            batch=params.batch,
        )
        scaled = GenParams(
            width=params.width, height=params.height,
            steps=params.steps, precision=params.precision,
            batch=params.batch,
        )

        # Step 1 — reduce steps
        for step_option in _STEP_LADDER:
            if step_option < scaled.steps:
                scaled.steps      = step_option
                scaled.downscaled = True
                if free_gb >= _estimate_vram_gb(scaled, correction_factor=factor) + 0.5:
                    log.info("[resources] Steps → %d (free=%.2fGB).", scaled.steps, free_gb)
                    if chain:
                        chain.record(
                            stage="vram_model",
                            action="reduce_steps",
                            reason=f"predicted_vram_exceeds_budget (free={free_gb:.2f}GB est={estimated:.2f}GB)",
                            original={"steps": original.steps, "width": original.width, "height": original.height},
                            final={"steps": scaled.steps,    "width": scaled.width,    "height": scaled.height},
                        )
                    return True, scaled

        # Step 2 — reduce resolution
        for res in _RESOLUTION_LADDER:
            if res < scaled.width:
                scaled.width      = res
                scaled.height     = res
                scaled.downscaled = True
                if free_gb >= _estimate_vram_gb(scaled, correction_factor=factor) + 0.5:
                    log.info("[resources] Resolution → %dx%d.", res, res)
                    if chain:
                        chain.record(
                            stage="vram_model",
                            action="reduce_resolution",
                            reason=f"steps_insufficient_to_fit (free={free_gb:.2f}GB)",
                            original={"steps": original.steps, "width": original.width, "height": original.height},
                            final={"steps": scaled.steps,    "width": scaled.width,    "height": scaled.height},
                        )
                    return True, scaled

        # Step 3 — switch to fp8 precision
        if scaled.precision == "fp16":
            scaled.precision  = "fp8"
            scaled.downscaled = True
            if free_gb >= _estimate_vram_gb(scaled, correction_factor=factor) + 0.5:
                log.info("[resources] Precision → fp8.")
                if chain:
                    chain.record(
                        stage="vram_model",
                        action="reduce_precision",
                        reason="resolution_and_steps_still_exceed_budget",
                        original={"precision": "fp16"},
                        final={"precision": "fp8"},
                    )
                return True, scaled

        # Step 4 — still not enough
        log.warning(
            "[resources] Cannot fit job (%.2fGB free, need ~%.2fGB).",
            free_gb, _estimate_vram_gb(scaled, correction_factor=factor),
        )
        if chain:
            chain.record(
                stage="vram_model",
                action="reject",
                reason="insufficient_vram_after_full_downscale",
            )
        return False, scaled

    def record_actual_vram(self, predicted_gb: float, actual_gb: float) -> None:
        """
        Feed actual VRAM usage back to the correction model.
        Call this after generation completes with VRAM peak reading.
        """
        _vram_feedback.record(predicted_gb, actual_gb)

    def get_recommended_steps(self, requested_steps: int) -> int:
        """
        Suggest a step count based on recent telemetry p95.
        This is a SUGGESTION only — the caller decides whether to apply it.
        Never called automatically; requires explicit opt-in (auto_tune in config).
        """
        try:
            from modules.telemetry import telemetry
            snap      = telemetry.snapshot()
            gen_stats = snap["metrics"].get("generation_ms", {})
            p95       = gen_stats.get("p95", 0.0)
            count     = gen_stats.get("count", 0)

            if count >= 5 and p95 > 8000:
                reduced = max(15, int(requested_steps * 0.7))
                if reduced < requested_steps:
                    log.info(
                        "[resources] get_recommended_steps: %d → %d (p95=%.0fms). "
                        "Apply only if auto_tune is enabled.",
                        requested_steps, reduced, p95,
                    )
                    return reduced
        except Exception:
            pass
        return requested_steps

    def feedback_stats(self) -> dict:
        """Return current correction factor and sample count."""
        return _vram_feedback.stats()

    # ── Calibration ───────────────────────────────────────────────────────────

    def reset_vram_model(self) -> None:
        """
        Reset the VRAM feedback model to baseline (correction_factor = 1.0,
        sample count = 0).  Use this to clear accumulated drift or after
        switching hardware.
        """
        with _vram_feedback._lock:
            _vram_feedback._factor  = 1.0
            _vram_feedback._samples = 0
        log.info("[resources] VRAM model reset to baseline.")

    def calibrate(
        self,
        resolutions: Optional[list] = None,
        step_counts: Optional[list] = None,
    ) -> dict:
        """
        Run a benchmark sequence of synthetic VRAM estimates against known
        fixed parameters to validate current model accuracy.

        This is a DRY RUN — no actual GPU work is performed.  It compares
        the predictive model output against the expected baseline coefficients
        for a range of resolution / step combinations and reports how far the
        model has drifted from its baseline.

        Args:
            resolutions: list of (width, height) tuples.  Defaults to standard ladder.
            step_counts: list of step counts.  Defaults to standard step ladder.

        Returns:
            dict with per-combination predictions and a drift_pct summary.

        Usage:
            report = governor.calibrate()
            if report["max_drift_pct"] > 20:
                governor.reset_vram_model()
        """
        if resolutions is None:
            resolutions = [(512, 512), (768, 768), (1024, 1024)]
        if step_counts is None:
            step_counts = [15, 20, 30]

        factor   = _vram_feedback.factor
        baseline = 1.0     # uncorrected model
        results  = []

        for (w, h) in resolutions:
            for steps in step_counts:
                params_test = GenParams(width=w, height=h, steps=steps)
                est_current  = _estimate_vram_gb(params_test, correction_factor=factor)
                est_baseline = _estimate_vram_gb(params_test, correction_factor=baseline)
                drift_pct    = abs(est_current - est_baseline) / max(est_baseline, 0.001) * 100

                results.append({
                    "resolution":    f"{w}x{h}",
                    "steps":         steps,
                    "baseline_gb":   round(est_baseline, 3),
                    "corrected_gb":  round(est_current, 3),
                    "drift_pct":     round(drift_pct, 1),
                })

        max_drift = max(r["drift_pct"] for r in results) if results else 0.0
        log.info(
            "[resources] Calibration complete — max drift %.1f%% (factor=%.3f, samples=%d)",
            max_drift, factor, _vram_feedback.samples,
        )

        return {
            "correction_factor": round(factor, 4),
            "samples":           _vram_feedback.samples,
            "max_drift_pct":     round(max_drift, 1),
            "combinations":      results,
            "recommendation":    "reset" if max_drift > 25 else "ok",
        }


governor = VRAMGovernor()
