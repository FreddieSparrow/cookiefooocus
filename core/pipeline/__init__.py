"""
core.pipeline — SDXL inference, VAE, samplers, attention
==========================================================
Re-exports the existing pipeline modules so runtime code can import from
core.pipeline without knowing the internal modules/ layout.

Nothing here is environment-aware — local vs server differences live in
runtime/ only.
"""

from modules.pipeline import (          # noqa: F401
    prepare_text_encoder,
    prepare_model,
    clip_encode_single,
    clip_separate,
    clip_pool_and_concat,
    clip_encode,
    set_enabled_loras,
    calculate_sigmas,
    forge_hook,
    process_diffusion,
)

from modules.default_pipeline import (  # noqa: F401
    refresh_base_model,
    refresh_refiner_model,
    refresh_loras,
    synthesize_refiner_model,
    assert_model_integrity,
    patch_and_clean,
    patch_discrete,
    patch_samplers,
    process_patch,
    process,
    handler,
)
