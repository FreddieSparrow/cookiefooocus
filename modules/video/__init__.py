"""
Cookie-Fooocus — Video Generation Module
────────────────────────────────────────────────────────────────────────────────
Extends Cookie-Fooocus with switchable video output without introducing a
separate application.  The same prompt, safety, and queue system is reused —
video is just a different output type from the same generation pipeline.

Architecture
─────────────────────────────────────────────────────────────────────────────
  Prompt
    ↓
  Media Router (modules/video/router.py)
    ├── mode=image → existing SDXL pipeline (unchanged)
    └── mode=video → Video Pipeline (this module)
                       ↓ Safety (shared — no duplication)
                       ↓ Prompt Expansion (shared PromptEngine)
                       ↓ Frame Generator (SDXL + motion module)
                       ↓ Consistency Engine (optical flow, seed anchoring)
                       ↓ FFmpeg Encoder
                       ↓ Post-gen NSFW (per-frame, shared safety layer)
                       ↓ Output Handler (MP4 / GIF / frame sequence)

Backends
─────────────────────────────────────────────────────────────────────────────
  SVD   — Stable Video Diffusion (img2vid, best stability, easiest MVP)
  AnimateDiff — text2vid via motion modules on SDXL (most flexible)

User controls (what surfaces in the UI — nothing else):
  • Prompt (shared with image mode)
  • Duration:  2s | 4s | 6s | 10s
  • FPS:       12 | 24
  • Motion:    smooth | cinematic | handheld | zoom | orbit | parallax
  • Mode:      image-to-video | text-to-video
  • Animate button on any existing image result

Status
─────────────────────────────────────────────────────────────────────────────
  Phase 1 (MVP):    SVD img2vid + FFmpeg + local CLI          [roadmap]
  Phase 2:          AnimateDiff + motion presets + consistency [roadmap]
  Phase 3:          Scene builder + timeline editor            [roadmap]
  Phase 4:          Multi-shot + character consistency         [roadmap]

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class MediaMode(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class VideoBackend(str, Enum):
    SVD         = "svd"          # Stable Video Diffusion
    ANIMATEDIFF = "animatediff"  # AnimateDiff motion modules


class MotionPreset(str, Enum):
    SMOOTH      = "smooth"
    CINEMATIC   = "cinematic"
    HANDHELD    = "handheld"
    ZOOM        = "zoom"
    ORBIT       = "orbit"
    PARALLAX    = "parallax"
    DOLLY       = "dolly"
    DRONE       = "drone"


MOTION_PRESET_PROMPTS: dict[MotionPreset, str] = {
    MotionPreset.SMOOTH:    "smooth motion, stable camera, fluid movement",
    MotionPreset.CINEMATIC: "cinematic camera movement, rack focus, cinematic dolly",
    MotionPreset.HANDHELD:  "handheld camera, slight shake, naturalistic movement",
    MotionPreset.ZOOM:      "slow zoom in, focal length pull, telephoto compression",
    MotionPreset.ORBIT:     "orbital camera, 360 rotation, turntable motion",
    MotionPreset.PARALLAX:  "parallax depth, foreground-background separation",
    MotionPreset.DOLLY:     "dolly zoom, Vertigo effect, depth distortion",
    MotionPreset.DRONE:     "aerial drone shot, descending, bird's-eye perspective",
}


def is_video_available() -> bool:
    """Check whether the video pipeline dependencies are installed."""
    try:
        import diffusers  # noqa: F401
        return True
    except ImportError:
        return False


def get_motion_prompt(preset: MotionPreset) -> str:
    """Return the motion-specific prompt suffix for a preset."""
    return MOTION_PRESET_PROMPTS.get(preset, "")


__all__ = [
    "MediaMode",
    "VideoBackend",
    "MotionPreset",
    "MOTION_PRESET_PROMPTS",
    "is_video_available",
    "get_motion_prompt",
]
