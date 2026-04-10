"""
Cookie-Fooocus Diffusers Pipeline
Minimal Stable Diffusion 1.x / SDXL pipeline backed by HuggingFace diffusers.

Key design decisions
────────────────────
• One global pipeline instance — never re-loaded per request (huge perf win).
• Server manager chooses power mode at first-run; applies correct dtype,
  device, and memory optimisations automatically.
• All inference runs under torch.no_grad() — no gradient graph allocated.
• Model hash is verified before loading (see model_verification.py).

Supported power modes
─────────────────────
  "gpu"      — Full FP16 on CUDA.  Requires ≥ 4 GB VRAM.
  "low_vram" — FP16 + model CPU offload + attention slicing.  ≥ 2 GB VRAM.
  "cpu"      — FP32 on CPU/RAM.  Slow but works anywhere.
  "auto"     — Detect best mode from available hardware.
"""

import logging
import threading
from pathlib import Path
from typing import Optional

import torch

log = logging.getLogger("cookiefooocus.pipeline")

# ── Default model (SD 1.5, ~4 GB download, runs at 512×512) ──────────────────
DEFAULT_MODEL_ID = "runwayml/stable-diffusion-v1-5"

# ── Global singleton state ────────────────────────────────────────────────────
_pipeline        = None
_pipeline_lock   = threading.Lock()
_pipeline_mode   = None
_pipeline_model  = None

# ── Request concurrency guard (prevents GPU/CPU exhaustion) ──────────────────
_MAX_CONCURRENT  = 2
_active_sema     = threading.Semaphore(_MAX_CONCURRENT)


def _detect_mode() -> str:
    """Pick the safest mode that matches the current hardware."""
    if not torch.cuda.is_available():
        log.info("[pipeline] No CUDA GPU detected — using CPU mode.")
        return "cpu"
    vram_bytes = torch.cuda.get_device_properties(0).total_memory
    vram_gb    = vram_bytes / (1024 ** 3)
    if vram_gb >= 4.0:
        log.info("[pipeline] GPU detected (%.1f GB VRAM) — using full GPU mode.", vram_gb)
        return "gpu"
    log.info("[pipeline] Low VRAM GPU (%.1f GB) — using low_vram mode.", vram_gb)
    return "low_vram"


def _validate_mode(mode: str) -> str:
    valid = {"gpu", "low_vram", "cpu", "auto"}
    if mode not in valid:
        raise ValueError(f"Invalid pipeline mode {mode!r}. Must be one of {valid}.")
    return mode


def load_pipeline(
    mode: str = "auto",
    model_id: str = DEFAULT_MODEL_ID,
    local_model_path: Optional[str] = None,
) -> None:
    """
    Load the Stable Diffusion pipeline once and cache it globally.
    Safe to call multiple times — subsequent calls are no-ops unless
    mode or model changed.

    Parameters
    ----------
    mode            : "gpu" | "low_vram" | "cpu" | "auto"
    model_id        : HuggingFace model repo ID (used when no local path given)
    local_model_path: Path to a local diffusers model directory or .safetensors
    """
    global _pipeline, _pipeline_mode, _pipeline_model

    _validate_mode(mode)
    resolved_mode = _detect_mode() if mode == "auto" else mode

    with _pipeline_lock:
        if _pipeline is not None and _pipeline_mode == resolved_mode and _pipeline_model == (local_model_path or model_id):
            return  # Already loaded, nothing to do

        log.info("[pipeline] Loading pipeline — mode=%s model=%s", resolved_mode, local_model_path or model_id)

        try:
            from diffusers import StableDiffusionPipeline, DiffusionPipeline
        except ImportError as exc:
            raise RuntimeError(
                "diffusers is not installed. Run: pip install diffusers transformers accelerate"
            ) from exc

        # ── Verify model hash if loading a local file ─────────────────────
        if local_model_path is not None:
            from modules.model_verification import verify_model
            path = Path(local_model_path)
            verify_model(path)  # Raises or warns if hash unknown

        # ── Dtype and device selection ────────────────────────────────────
        if resolved_mode == "cpu":
            dtype  = torch.float32
            device = "cpu"
        else:
            dtype  = torch.float16
            device = "cuda"

        # ── Build load kwargs ──────────────────────────────────────────────
        source  = local_model_path or model_id
        kwargs  = {"torch_dtype": dtype, "safety_checker": None}

        # Single-file .safetensors checkpoint
        if local_model_path and Path(local_model_path).suffix in (".safetensors", ".ckpt"):
            pipe = StableDiffusionPipeline.from_single_file(source, **kwargs)
        else:
            pipe = StableDiffusionPipeline.from_pretrained(source, **kwargs)

        # ── Memory optimisations per mode ─────────────────────────────────
        if resolved_mode == "cpu":
            pipe.enable_attention_slicing(1)   # Minimal peak memory
            pipe.enable_vae_slicing()
            # Sequential offload not needed on CPU but keeps API consistent
        elif resolved_mode == "low_vram":
            pipe.enable_model_cpu_offload()    # Moves layers to CPU between steps
            pipe.enable_attention_slicing(1)
            pipe.enable_vae_slicing()
        else:  # "gpu"
            pipe = pipe.to(device)
            pipe.enable_attention_slicing()    # Still helps on 4–8 GB cards

        # ── Disable gradient computation globally ─────────────────────────
        torch.set_grad_enabled(False)

        _pipeline       = pipe
        _pipeline_mode  = resolved_mode
        _pipeline_model = local_model_path or model_id

        log.info("[pipeline] Pipeline ready (mode=%s).", resolved_mode)


def get_pipeline():
    """Return the loaded pipeline, raising if not yet initialised."""
    if _pipeline is None:
        raise RuntimeError(
            "Pipeline not loaded. Call load_pipeline() during startup."
        )
    return _pipeline


def generate(
    prompt:          str,
    negative_prompt: str  = "",
    width:           int  = 512,
    height:          int  = 512,
    steps:           int  = 20,
    guidance_scale:  float = 7.5,
    seed:            Optional[int] = None,
    num_images:      int  = 1,
) -> list:
    """
    Run inference and return a list of PIL Images.

    Thread-safe: at most _MAX_CONCURRENT requests run simultaneously.
    Excess callers block (not rejected) so the server stays responsive
    rather than crashing under load.

    Parameters are intentionally minimal — extend as needed.
    """
    # Concurrency guard — prevents GPU/CPU exhaustion from parallel requests
    acquired = _active_sema.acquire(blocking=True, timeout=120)
    if not acquired:
        raise TimeoutError("Generation timed out waiting for a free slot.")

    try:
        pipe = get_pipeline()

        generator = None
        if seed is not None:
            device = "cpu" if _pipeline_mode == "cpu" else "cuda"
            generator = torch.Generator(device=device).manual_seed(seed)

        with torch.no_grad():
            result = pipe(
                prompt=prompt,
                negative_prompt=negative_prompt or None,
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=guidance_scale,
                generator=generator,
                num_images_per_prompt=num_images,
            )

        return result.images

    finally:
        _active_sema.release()
