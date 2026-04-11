"""
Cookie-Fooocus v3 — Core Package
==================================
Environment-agnostic inference logic.

Rules:
  - Nothing in core/ may import from runtime/
  - Nothing in core/ may read CF_MODE
  - Nothing in core/ opens network connections on its own

Sub-packages mirror the functional split from the spec:
    core.pipeline       — SDXL inference, VAE, samplers
    core.vram           — VRAM governor and hardware profiles
    core.safety         — 2-layer safety system
    core.prompt_engine  — 4-mode prompt engine + PromptTrace
    core.cache          — L1/L2 prompt + NSFW cache hierarchy
    core.scheduler      — Priority queue and job lifecycle
"""
