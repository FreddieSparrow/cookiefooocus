"""
Cookie-Fooocus — Resource Manager / VRAM Governor
────────────────────────────────────────────────────────────────────────────────
Pre-execution VRAM budget check with automatic quality downscaling.

Before any generation request touches the GPU, this module:
  1. Reads current free VRAM (once per job, not at module import)
  2. Estimates memory required for the requested quality parameters
  3. If budget is tight: scales down resolution / steps / precision
  4. If budget is critically low: rejects the request entirely

This prevents OOM crashes without requiring callers to know anything about
hardware state.

Quality downscaling cascade (applied in order until budget fits):
  Step 1 — reduce steps        (e.g. 30 → 20)
  Step 2 — reduce resolution   (e.g. 1024 → 768)
  Step 3 — switch precision    (fp16 → fp8 if available)
  Step 4 — reject              (VRAM below hard floor)

All hardware reads happen here — no other module queries GPU state.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Tuple

log = logging.getLogger("cookiefooocus.resource_manager")

# Minimum free VRAM (GB) below which all generation is rejected
_VRAM_FLOOR_GB = 1.0

# Estimated VRAM per megapixel at fp16 (rough empirical constant for SDXL)
_VRAM_PER_MPIX_FP16_GB = 0.28

# Resolution ladder for downscaling (width × height pairs — always square for simplicity)
_RESOLUTION_LADDER = [1024, 896, 768, 640, 512]

# Step ladder for downscaling
_STEP_LADDER = [30, 25, 20, 15]


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
    """Read hardware spec once.  Never import torch at module load time."""
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
    """Return current free VRAM in GB.  Returns 99.0 on CPU/Apple (unified memory)."""
    try:
        import torch
        if torch.cuda.is_available():
            free, _ = torch.cuda.mem_get_info()
            return free / (1024 ** 3)
    except Exception:
        pass

    hw = get_hardware_profile()
    if hw.is_apple_silicon:
        return hw.ram_total_gb * 0.6   # conservative estimate
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
#  Generation parameters (mutable DTO)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GenParams:
    width:     int   = 1024
    height:    int   = 1024
    steps:     int   = 30
    precision: str   = "fp16"   # "fp16" | "fp8" | "bf16" | "cpu"
    batch:     int   = 1
    downscaled: bool = False    # True if quality was reduced from original request


# ═══════════════════════════════════════════════════════════════════════════════
#  VRAM estimation
# ═══════════════════════════════════════════════════════════════════════════════

def _estimate_vram_gb(params: GenParams) -> float:
    """Rough VRAM estimate for given parameters."""
    mpix       = (params.width * params.height) / 1_000_000
    precision_factor = 1.0 if params.precision in ("fp16", "bf16") else 0.6  # fp8
    return mpix * _VRAM_PER_MPIX_FP16_GB * precision_factor * params.batch


# ═══════════════════════════════════════════════════════════════════════════════
#  Governor — pre-execution check with auto-downscale
# ═══════════════════════════════════════════════════════════════════════════════

class VRAMGovernor:
    """
    Pre-execution VRAM budget check.

    Usage:
        params = GenParams(width=1024, height=1024, steps=30)
        ok, params = governor.check_and_scale(params)
        if not ok:
            return "insufficient VRAM"
        # proceed with (possibly adjusted) params
    """

    def check_and_scale(
        self,
        params: GenParams,
    ) -> Tuple[bool, GenParams]:
        """
        Check if VRAM is sufficient.  Apply downscaling cascade if needed.

        Returns:
            (True, params)   — generation can proceed (params may be adjusted)
            (False, params)  — insufficient VRAM even after maximum downscaling
        """
        hw      = get_hardware_profile()
        free_gb = get_free_vram_gb()

        if hw.is_apple_silicon:
            # Unified memory — trust the model management layer (ldm_patched)
            return True, params

        estimated = _estimate_vram_gb(params)
        if free_gb >= estimated + 0.5:
            return True, params  # comfortable headroom

        if free_gb < _VRAM_FLOOR_GB:
            log.warning(
                "[resources] Free VRAM %.2fGB below floor %.2fGB — rejecting job.",
                free_gb, _VRAM_FLOOR_GB,
            )
            return False, params

        # Downscale cascade
        scaled = GenParams(
            width=params.width, height=params.height,
            steps=params.steps, precision=params.precision,
            batch=params.batch,
        )

        # Step 1 — reduce steps
        for step_option in _STEP_LADDER:
            if step_option < scaled.steps:
                scaled.steps    = step_option
                scaled.downscaled = True
                if free_gb >= _estimate_vram_gb(scaled) + 0.5:
                    log.info("[resources] Downscaled steps to %d (free=%.2fGB).", scaled.steps, free_gb)
                    return True, scaled

        # Step 2 — reduce resolution
        for res in _RESOLUTION_LADDER:
            if res < scaled.width:
                scaled.width  = res
                scaled.height = res
                scaled.downscaled = True
                if free_gb >= _estimate_vram_gb(scaled) + 0.5:
                    log.info("[resources] Downscaled resolution to %dx%d.", res, res)
                    return True, scaled

        # Step 3 — switch to fp8 precision
        if scaled.precision == "fp16":
            scaled.precision  = "fp8"
            scaled.downscaled = True
            if free_gb >= _estimate_vram_gb(scaled) + 0.5:
                log.info("[resources] Downscaled precision to fp8.")
                return True, scaled

        # Step 4 — still not enough
        log.warning(
            "[resources] Cannot fit job in available VRAM (%.2fGB free, need %.2fGB).",
            free_gb, _estimate_vram_gb(scaled),
        )
        return False, scaled


governor = VRAMGovernor()
