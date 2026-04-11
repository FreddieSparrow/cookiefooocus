"""
Cookie-Fooocus — Media Router
────────────────────────────────────────────────────────────────────────────────
Single entry point that routes a generation request to either the image
pipeline or the video pipeline.

  generate(prompt, mode="image", **kwargs) → GenerationJob

The caller does not need to know which pipeline is active.  The response
object always has the same shape — output type (image/video) is indicated by
job.media_type.

This is the only file that imports both pipelines.  Everything else is
isolated behind it.

Video cost control
─────────────────────────────────────────────────────────────────────────────
  Video frames scale as duration_s × fps.  Without a cap, one request can
  trigger 100+ sequential SDXL inference passes.

  Hard limits (applied before the job hits the queue):
    MAX_TOTAL_FRAMES = 96   — hard frame ceiling
    MAX_DURATION_S   = 10.0 — never allow duration beyond this
    MAX_FPS          = 24   — cap FPS at the UI maximum

  If the requested combination exceeds the frame cap, duration_s is reduced
  proportionally.  The job is never rejected for cost — only clamped.
  The actual limits used are recorded in job.metadata["frame_cap"].

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from modules.video import MediaMode, MotionPreset

log = logging.getLogger("cookiefooocus.video.router")

# Video cost hard limits — prevent one request from burning the GPU.
# 4s @ 24fps = 96 frames (reference point for the cap).
_MAX_TOTAL_FRAMES = 96
_MAX_DURATION_S   = 10.0
_MAX_FPS          = 24


@dataclass
class GenerationJob:
    """Unified job object returned by the media router."""
    job_id:       str
    media_type:   MediaMode
    status:       str                # "queued" | "running" | "complete" | "blocked" | "error"
    prompt:       str
    expanded:     str                # prompt after expansion
    output_path:  Optional[str] = None   # image path or video path
    error:        str            = ""
    metadata:     dict           = field(default_factory=dict)
    created_at:   float          = field(default_factory=time.time)


def generate(
    prompt:          str,
    mode:            str = "image",
    seed:            int = -1,
    steps:           int = 30,
    cfg:             float = 7.0,
    negative_prompt: str = "",
    prompt_mode:     str = "balanced",
    style:           str = "",
    # Video-specific
    duration_s:      float = 4.0,
    fps:             int   = 24,
    motion_preset:   str   = "cinematic",
    input_image:     Optional[str] = None,
    backend:         str   = "svd",
    job_id:          Optional[str] = None,
) -> GenerationJob:
    """
    Route a generation request to the appropriate pipeline.

    Args:
        prompt:         User prompt text.
        mode:           "image" or "video".
        seed:           RNG seed (-1 = random).
        steps:          Diffusion steps.
        cfg:            Classifier-free guidance scale.
        negative_prompt: Negative prompt.
        prompt_mode:    "raw" | "balanced" | "llm".
        style:          Fooocus style preset name.
        duration_s:     Video duration in seconds (video mode only).
        fps:            Frames per second (video mode only).
        motion_preset:  Motion style preset (video mode only).
        input_image:    Path to input image (img2vid or img2img).
        backend:        Video backend: "svd" | "animatediff".
        job_id:         Optional job identifier.

    Returns:
        GenerationJob with status and output_path when complete.
    """
    import random
    if seed < 0:
        seed = random.randint(0, 2 ** 31)

    if job_id is None:
        job_id = f"cf-{int(time.time())}-{seed}"

    media = MediaMode(mode) if mode in (m.value for m in MediaMode) else MediaMode.IMAGE

    # Safety check (shared — identical for image and video)
    from modules.safety import check_prompt
    safety = check_prompt(prompt)
    if not safety.allowed:
        log.info("[router] Job %s blocked by safety layer.", job_id)
        return GenerationJob(
            job_id=job_id,
            media_type=media,
            status="blocked",
            prompt=prompt,
            expanded=prompt,
            metadata={
                "safety_decision": safety.reason.decision.value,
                "safety_rule":     safety.reason.rule,
            },
        )

    # Prompt expansion (shared)
    from modules.generation_controller import controller
    expanded = controller.expand_prompt(prompt, seed=seed, mode=prompt_mode)

    job = GenerationJob(
        job_id=job_id,
        media_type=media,
        status="queued",
        prompt=prompt,
        expanded=expanded,
    )

    if media == MediaMode.IMAGE:
        return _route_image(job, seed, steps, cfg, negative_prompt, style)
    else:
        return _route_video(
            job, seed, steps, cfg, negative_prompt,
            duration_s, fps, motion_preset, input_image, backend,
        )


def _route_image(
    job: GenerationJob,
    seed: int, steps: int, cfg: float,
    negative_prompt: str, style: str,
) -> GenerationJob:
    """Delegate to the existing SDXL async_worker pipeline."""
    job.status   = "queued"
    job.metadata.update({
        "seed":            seed,
        "steps":           steps,
        "cfg":             cfg,
        "negative_prompt": negative_prompt,
        "style":           style,
    })
    log.info("[router] Image job %s queued (seed=%d steps=%d).", job.job_id, seed, steps)
    return job


def _route_video(
    job: GenerationJob,
    seed: int, steps: int, cfg: float,
    negative_prompt: str,
    duration_s: float, fps: int,
    motion_preset: str, input_image: Optional[str],
    backend: str,
) -> GenerationJob:
    """Delegate to the video pipeline, enforcing frame cost limits."""
    from modules.video import is_video_available, MotionPreset, get_motion_prompt

    if not is_video_available():
        job.status = "error"
        job.error  = "Video dependencies not installed.  Run: pip install diffusers[video]"
        log.warning("[router] Video pipeline unavailable: diffusers not installed.")
        return job

    # ── Cost control ───────────────────────────────────────────────────────────
    # Cap inputs to hard maximums, then check the resulting frame count.
    fps       = min(int(fps), _MAX_FPS)
    duration_s = min(float(duration_s), _MAX_DURATION_S)

    total_frames = int(duration_s * fps)
    frame_cap_applied = False
    if total_frames > _MAX_TOTAL_FRAMES:
        original_duration = duration_s
        duration_s        = _MAX_TOTAL_FRAMES / fps
        total_frames      = int(duration_s * fps)
        frame_cap_applied = True
        log.info(
            "[router] Video job %s: duration clamped %.1fs → %.1fs "
            "(%d frames, limit=%d).",
            job.job_id, original_duration, duration_s, total_frames, _MAX_TOTAL_FRAMES,
        )
    # ──────────────────────────────────────────────────────────────────────────

    # Append motion prompt to expanded prompt
    try:
        preset = MotionPreset(motion_preset)
    except ValueError:
        preset = MotionPreset.CINEMATIC

    motion_suffix = get_motion_prompt(preset)
    if motion_suffix:
        job.expanded = job.expanded.rstrip(",") + ", " + motion_suffix

    job.status   = "queued"
    job.metadata.update({
        "seed":          seed,
        "steps":         steps,
        "cfg":           cfg,
        "negative":      negative_prompt,
        "duration_s":    duration_s,
        "fps":           fps,
        "total_frames":  total_frames,
        "motion":        motion_preset,
        "input_image":   input_image,
        "backend":       backend,
        "frame_cap": {
            "applied":      frame_cap_applied,
            "max_frames":   _MAX_TOTAL_FRAMES,
            "actual_frames": total_frames,
        },
    })
    log.info(
        "[router] Video job %s queued (backend=%s duration=%.1fs fps=%d frames=%d).",
        job.job_id, backend, duration_s, fps, total_frames,
    )
    return job
