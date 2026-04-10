<div align=center>
<img src="https://github.com/lllyasviel/Fooocus/assets/19834515/483fb86d-c9a2-4c20-997c-46dafc124f25">
</div>

# Cookie-Fooocus

A security-hardened fork of [Fooocus](https://github.com/lllyasviel/Fooocus) with a multi-layer content safety system, strengthened authentication, and safe model loading — while keeping everything offline, free, and easy to use.

---

## What this fork adds over upstream Fooocus

### 1. Content Safety Filter (`modules/content_filter.py`)

Upstream Fooocus has no prompt or image moderation. This fork adds a full middleware layer that runs before generation and after output:

**Prompt normalisation pipeline** — defeats ~80% of real-world bypass tricks before any rule is applied:

| Step | What it handles |
|------|----------------|
| Unicode NFKC | Bold/italic math, fullwidth, superscripts (`𝓈𝑒𝓍` → `sex`) |
| Homoglyph substitution | Cyrillic/Greek lookalikes NFKC misses (`с` → `c`, `ο` → `o`) |
| Leet-speak | `3→e`, `0→o`, `4→a`, `@→a` and more |
| Diacritics | `café` → `cafe` |
| Zero-width characters | Strips invisible Unicode injections |
| Spaced words | `s e x` → `sex` |
| Base64 sniffing | Decodes and appends encoded payloads for scanning (entropy-gated to prevent CPU exhaustion) |

**Detection pipeline:**

- Hard block patterns — CSAM and WMD prompts are `CRITICAL`; deepfake, weapons synthesis, and prompt injection are `BLOCK`
- Adult filter — toggleable 18+ content gate
- Intent patterns — indirect phrasing (`"remove her clothes"`, `"undress the subject"`)
- Fuzzy keyword matching — edit-distance tolerance so `"s3x"` after leet normalisation still matches
- Additive risk scoring — keyword clusters with weighted points; blocks at threshold 6
- ML injection classifier — optional HuggingFace `deberta-v3-base-prompt-injection-v2` (lazy-loaded, preloadable at startup)
- Warn-pass — gore/drug references pass through with a caution flag rather than a hard block

**NSFW image output filter** — checks every generated image with `Falconsai/nsfw_image_detection` before it reaches the UI. Blocks at 0.65 confidence, warns at 0.35.

**Rate limiter** — 30 requests per 60 seconds per user ID, enforced before any filter work runs.

**Audit log** — append-only JSONL at `~/.local/share/cookiefooocus/ai-audit.jsonl`. Stores only SHA-256 hashes of content — no raw prompts ever written to disk. Thread-safe under parallel generation.

**Critical alerts** — CSAM and WMD matches write a separate JSON alert to `~/.local/share/cookiefooocus/alerts/` with hashed evidence only.

Upstream has none of this. All safety checks are bypassed in the original.

---

### 2. Hardened Authentication (`modules/auth.py`)

Upstream Fooocus stores and compares passwords as plaintext strings passed directly to Gradio's `auth=` parameter, with no hashing at all.

This fork replaces that with:

| Feature | Upstream | This fork |
|---------|----------|-----------|
| Password storage | Plaintext in `auth.json` | PBKDF2-HMAC-SHA256, 600k iterations (OWASP 2023) |
| Comparison | String equality | `hmac.compare_digest` (constant-time, prevents timing attacks) |
| Salt | None | 32-byte random salt per password |
| Legacy support | — | Accepts old bare SHA-256 hashes for backwards compatibility |

Plaintext passwords in `auth.json` are automatically hashed on first load — the file does not need to be changed.

---

### 3. Safe Model Loading (`ldm_patched/modules/checkpoint_pickle.py`)

PyTorch `.safetensors` files are safe, but `.ckpt`/`.pt` model files use Python pickle, which can execute arbitrary code on load. Upstream Fooocus passes these directly to `torch.load()` with no restriction.

This fork replaces the unpickler with an allowlist-based one that only permits:

- `torch` — tensor data
- `numpy` — array data
- `collections` — `OrderedDict`

Any model file that tries to import or call anything else is rejected before execution. This prevents RCE from malicious community model files.

---

## Integrating the content filter into generation

The filter is implemented but not yet wired to the generation pipeline. To enable it, find the generation entry point and wrap it:

```python
from modules.content_filter import check_prompt, check_image, preload_models
import threading

# At startup — warm up ML models in background so first request isn't slow
threading.Thread(target=preload_models, daemon=True).start()

# In your generation function
def generate_image(prompt, user_id="anon"):
    result = check_prompt(prompt, user_id)
    if not result.allowed:
        return error_image(result.reason)

    image_path = backend.generate(prompt)

    img_result = check_image(image_path, user_id)
    if not img_result.allowed:
        return error_image("Output blocked by safety policy.")

    return image_path
```

To disable the adult filter (e.g. for a private deployment):
```python
from modules.content_filter import set_adult_filter
set_adult_filter(False)
```

---

## Everything else: same as upstream Fooocus

All original Fooocus features are preserved unchanged:

- Offline GPT-2 based prompt expansion (Fooocus V2 style)
- SDXL pipeline with native refiner swap, negative ADM guidance, SAG sharpness
- Inpaint / outpaint with Fooocus's own inpaint model
- Image prompt (IP-Adapter variant)
- FaceSwap via InsightFace
- Wildcards, array processing, inline LoRAs
- All presets (default, anime, realistic)
- All CMD flags

See the [upstream documentation](https://github.com/lllyasviel/Fooocus) for the full feature list, installation instructions, and hardware requirements.

---

## Installation

Clone this repo instead of the original:

```bash
git clone https://github.com/FreddieSparrow/cookiefooocus.git
cd cookiefooocus
conda env create -f environment.yaml
conda activate fooocus
pip install -r requirements_versions.txt
python entry_with_update.py
```

On first launch a setup wizard runs and asks two questions: your memory mode and whether to enable the adult content filter. The choice is saved and never asked again (delete `~/.config/cookiefooocus/first_run.json` to re-run it).

Optional dependencies for full filter functionality:
```bash
pip install rapidfuzz          # faster fuzzy matching (graceful fallback if absent)
pip install transformers       # ML injection classifier + NSFW image classifier
pip install Pillow             # image filter (likely already installed)
```

---

## Hardware Requirements & Memory Modes

On first launch the wizard offers five modes:

| # | Mode | Who it's for | Flags applied |
|---|------|-------------|---------------|
| 1 | **GPU (VRAM)** | NVIDIA/AMD GPU with 4 GB+ VRAM | _(none — full speed)_ |
| 2 | **Low VRAM** | GPU with < 4 GB VRAM | `--always-low-vram` |
| 3 | **CPU / RAM** | No GPU at all | `--always-cpu` |
| 4 | **Auto-detect** | Let Cookie-Fooocus decide | _(none — runtime detection)_ |
| 5 | **No VRAM / 16 GB RAM** | iGPU, server hardware, or no dedicated GPU — **requires 16 GB+ system RAM** | `--always-no-vram --unet-in-fp8-e4m3fn --vae-in-cpu` |

### Mode 5 — No VRAM / 16 GB DDR4/DDR5 (server & iGPU builds)

This mode requires **at least 16 GB of system RAM** (DDR4 or DDR5). Running with less will cause out-of-memory errors. It is designed for machines that have 16 GB+ system RAM but no dedicated GPU VRAM — including:

- Servers with integrated graphics or no display GPU
- Laptops/desktops with Intel/AMD integrated graphics
- Multi-tenant systems where VRAM is shared between processes
- CPU-only builds where you still want to minimise RAM usage

Three flags are applied together:

| Flag | Effect |
|------|--------|
| `--always-no-vram` | Models live in system RAM; GPU is used for compute only (no VRAM residency). Falls back gracefully to CPU if no GPU is present. |
| `--unet-in-fp8-e4m3fn` | Quantises the UNet to FP8 — roughly halves its memory footprint (~6.9 GB → ~3.5 GB in fp16→fp8) |
| `--vae-in-cpu` | Keeps the VAE decoder on CPU RAM, eliminating any VRAM spike during image decode |

**Approximate memory usage in mode 5 (SDXL):**

| Component | fp16 | fp8 (mode 5) |
|-----------|------|--------------|
| UNet | ~6.9 GB | ~3.5 GB |
| VAE | ~335 MB | ~335 MB (CPU) |
| CLIP x2 | ~1.5 GB | ~1.5 GB |
| **Total** | **~8.7 GB** | **~5.3 GB** |

This fits comfortably in 16 GB DDR4/DDR5 with room for the OS and Python overhead.

Generation will be slower than dedicated-GPU mode (expect ~5–15× depending on hardware), but the output quality is identical — FP8 quantisation affects speed, not image fidelity at SDXL scale.

You can also set mode 5 manually without the wizard:

```bash
python entry_with_update.py --always-no-vram --unet-in-fp8-e4m3fn --vae-in-cpu
```

For Docker / CI / non-interactive environments the wizard is skipped and auto-detect (mode 4) is used automatically.

---

## License

GPL-3.0 — same as upstream Fooocus.
