# Cookie-Fooocus

A security-hardened, safety-filtered fork of [Fooocus](https://github.com/lllyasviel/Fooocus) with Ollama-powered prompt expansion, multi-layer content moderation, and flexible hardware support — from high-end GPUs to CPU-only servers.

---

## What this fork adds over upstream Fooocus

| Feature | Upstream Fooocus | Cookie-Fooocus |
|---------|-----------------|----------------|
| Prompt expansion | Offline GPT-2 (local model file) | **Ollama / Gemma 4** (local LLM server) |
| Content moderation | None | **Multi-layer filter** (normalisation + rules + ML) |
| NSFW image filter | Basic censor | **HuggingFace classifier** with score thresholds |
| Authentication | Plaintext passwords | **PBKDF2-HMAC-SHA256**, 600k iterations |
| Model file safety | Raw `torch.load()` | **Pickle allowlist** (prevents RCE from `.ckpt` files) |
| Hardware modes | Manual CLI flags | **Interactive first-run wizard** (5 modes) |
| Safety enforcement | None | **content_filter.py verified against repo on every boot** |
| Docker support | Yes | Removed (not needed for local/server installs) |

---

## Installation

### 1. Prerequisites

- Python 3.10+
- Git
- [Ollama](https://ollama.com) — for prompt expansion (required)
- An NVIDIA/AMD GPU with 4 GB+ VRAM, **or** 16 GB+ system RAM for no-VRAM mode, **or** 64 GB+ RAM for CPU-only mode

### 2. Install Ollama and pull Gemma 4

```bash
# Install Ollama (macOS / Linux)
curl -fsSL https://ollama.com/install.sh | sh

# Pull Gemma 4
ollama pull gemma4

# Start the Ollama server (runs in background)
ollama serve
```

On Windows, download the Ollama installer from [ollama.com](https://ollama.com) and run `ollama pull gemma4` in a terminal.

Verify it works:
```bash
ollama run gemma4 "expand this image prompt: cat in a forest"
```

### 3. Clone and install Cookie-Fooocus

```bash
git clone https://github.com/FreddieSparrow/cookiefooocus.git
cd cookiefooocus

# Option A — Conda (recommended)
conda env create -f environment.yaml
conda activate fooocus
pip install -r requirements_versions.txt

# Option B — Python venv
python3 -m venv fooocus_env
source fooocus_env/bin/activate   # Windows: fooocus_env\Scripts\activate
pip install -r requirements_versions.txt
```

Optional (enables faster fuzzy matching and ML classifiers in the content filter):
```bash
pip install rapidfuzz transformers
```

### 4. First launch

```bash
python entry_with_update.py
```

On first launch a setup wizard runs and asks two questions. Your answers are saved and never asked again.

**Question 1 — Memory mode:**

| # | Mode | Who it's for | RAM / VRAM needed |
|---|------|-------------|------------------|
| 1 | GPU (VRAM) | NVIDIA/AMD GPU | 4 GB+ VRAM |
| 2 | Low VRAM | GPU with limited VRAM | < 4 GB VRAM |
| 3 | CPU only | No GPU, high-core server | **64 GB+ RAM** |
| 4 | Auto-detect | Let the app decide | — |
| 5 | No VRAM / RAM | iGPU or no dedicated GPU | **16 GB+ DDR4/DDR5** |

**Question 2 — Adult content filter:** Enable or disable the 18+ content gate.

To re-run the wizard, delete `~/.config/cookiefooocus/first_run.json`.

### 5. Custom Ollama host or model

By default, Cookie-Fooocus connects to Ollama at `http://localhost:11434` using the `gemma4` model. Override with environment variables:

```bash
OLLAMA_HOST=http://192.168.1.10:11434 OLLAMA_MODEL=gemma4 python entry_with_update.py
```

---

## Features

### Prompt expansion — Ollama / Gemma 4

Replaces the original offline GPT-2 engine with a locally-served Gemma 4 model via Ollama. Every prompt is expanded into a richer, more detailed description before generation — improving output quality without requiring any cloud connection.

- Seed-stable: the same seed produces the same expansion
- Graceful fallback: if Ollama is unreachable, the original prompt passes through unchanged
- Configurable: use any Ollama model via `OLLAMA_MODEL` env var

### Content safety filter

A multi-layer moderation middleware that sits between user input and the generation pipeline.

**Prompt normalisation** — defeats ~80% of real-world bypass tricks before any rule fires:

- Unicode NFKC (`𝓈𝑒𝓍` → `sex`, fullwidth, bold/italic variants)
- Homoglyph substitution (Cyrillic/Greek lookalikes: `с` → `c`, `ο` → `o`)
- Leet-speak (`3→e`, `0→o`, `4→a`, `@→a` and more)
- Diacritics (`café` → `cafe`)
- Zero-width character stripping
- Spaced-word collapse (`s e x` → `sex`)
- Base64 sniffing (entropy-gated to prevent CPU exhaustion attacks)

**Detection layers:**

| Layer | What it catches |
|-------|----------------|
| Hard block (CRITICAL) | CSAM, WMD synthesis |
| Hard block (BLOCK) | Deepfake nudity, weapons synthesis, prompt injection |
| Adult filter | Toggleable 18+ content gate |
| Intent patterns | Indirect phrasing (`"remove her clothes"`, `"undress the subject"`) |
| Fuzzy keywords | Edit-distance matching (catches intentional misspellings) |
| Risk scoring | Additive keyword clusters — blocks at threshold |
| ML classifier | Optional DeBERTa-based prompt-injection detector |
| Warn-pass | Gore/drug references flagged but not blocked |

**NSFW image filter** — every generated image is checked with `Falconsai/nsfw_image_detection` before display. Blocks at 65% confidence, warns at 35%.

**Audit log** — SHA-256-hashed JSONL at `~/.local/share/cookiefooocus/ai-audit.jsonl`. No raw prompts ever written. Thread-safe under parallel generation.

**Critical alerts** — CSAM and WMD matches write a separate JSON alert to `~/.local/share/cookiefooocus/alerts/`.

**Rate limiter** — 30 requests per 60 seconds per user, enforced before any filter work runs.

### content_filter.py integrity enforcement

On every boot, `launch.py` compares the local `modules/content_filter.py` against the upstream repository. If the file is missing, modified, or out of date, **startup is blocked** with a clear error message. This prevents the safety layer from being silently disabled.

To restore after an accidental change:
```bash
git checkout modules/content_filter.py
```

### Hardened authentication

Upstream Fooocus compares passwords as plaintext strings. This fork replaces that with:

- **PBKDF2-HMAC-SHA256** with 600,000 iterations (OWASP 2023 recommended)
- **`hmac.compare_digest`** for constant-time comparison (prevents timing attacks)
- 32-byte random salt per password
- Backwards compatible with legacy SHA-256 hashes

Plaintext passwords in `auth.json` are automatically hashed on first load.

### Safe model loading

PyTorch `.ckpt`/`.pt` model files use Python pickle, which can execute arbitrary code on load. This fork replaces the default unpickler with an allowlist that only permits `torch`, `numpy`, and `collections` — blocking any model file that attempts to import or execute anything else.

### Hardware modes (first-run wizard)

Five memory modes selectable at first launch:

| Mode | Flags applied | Min requirement |
|------|--------------|----------------|
| 1 — GPU | _(none)_ | 4 GB VRAM |
| 2 — Low VRAM | `--always-low-vram` | < 4 GB VRAM |
| 3 — CPU only | `--always-cpu` | **64 GB RAM** |
| 4 — Auto-detect | _(none)_ | Auto |
| 5 — No VRAM / RAM | `--always-no-vram --unet-in-fp8-e4m3fn --vae-in-cpu` | **16 GB RAM** |

**Mode 5 (No VRAM)** is designed for machines with no dedicated GPU VRAM — including iGPUs, integrated graphics, and servers. UNet is quantised to FP8 (~50% memory reduction), models live in system RAM, and the VAE runs on CPU to avoid any VRAM spike. SDXL fits in ~5.3 GB with these settings.

**Mode 3 (CPU only)** targets high-core-count servers with no GPU. Expect very slow generation (10–20+ minutes per image). 64 GB RAM is required to hold models plus generation buffers.

---

## All command-line flags

```
entry_with_update.py  [-h] [--listen [IP]] [--port PORT]
                      [--disable-header-check [ORIGIN]]
                      [--web-upload-size WEB_UPLOAD_SIZE]
                      [--hf-mirror HF_MIRROR]
                      [--external-working-path PATH [PATH ...]]
                      [--output-path OUTPUT_PATH]
                      [--temp-path TEMP_PATH] [--cache-path CACHE_PATH]
                      [--in-browser] [--disable-in-browser]
                      [--gpu-device-id DEVICE_ID]
                      [--async-cuda-allocation | --disable-async-cuda-allocation]
                      [--disable-attention-upcast]
                      [--all-in-fp32 | --all-in-fp16]
                      [--unet-in-bf16 | --unet-in-fp16 | --unet-in-fp8-e4m3fn | --unet-in-fp8-e5m2]
                      [--vae-in-fp16 | --vae-in-fp32 | --vae-in-bf16]
                      [--vae-in-cpu]
                      [--clip-in-fp8-e4m3fn | --clip-in-fp8-e5m2 | --clip-in-fp16 | --clip-in-fp32]
                      [--directml [DIRECTML_DEVICE]]
                      [--disable-ipex-hijack]
                      [--preview-option [none,auto,fast,taesd]]
                      [--attention-split | --attention-quad | --attention-pytorch]
                      [--disable-xformers]
                      [--always-gpu | --always-high-vram | --always-normal-vram | --always-low-vram | --always-no-vram | --always-cpu [CPU_NUM_THREADS]]
                      [--always-offload-from-vram]
                      [--pytorch-deterministic] [--disable-server-log]
                      [--debug-mode] [--is-windows-embedded-python]
                      [--disable-server-info] [--multi-user] [--share]
                      [--preset PRESET] [--disable-preset-selection]
                      [--language LANGUAGE]
                      [--disable-offload-from-vram] [--theme THEME]
                      [--disable-image-log] [--disable-analytics]
                      [--disable-metadata] [--disable-preset-download]
                      [--disable-enhance-output-sorting]
                      [--enable-auto-describe-image]
                      [--always-download-new-model]
                      [--rebuild-hash-cache [CPU_NUM_THREADS]]
```

---

## Original Fooocus features (all preserved)

- Text-to-image with SDXL — no prompt engineering needed
- Inpaint / Outpaint (Up / Down / Left / Right) with Fooocus's own inpaint model
- Image Prompt (IP-Adapter variant)
- Upscale (1.5x / 2x) and Variation (Subtle / Strong)
- FaceSwap via InsightFace
- Wildcards (`__color__ flower`)
- Array processing (`[[red, green, blue]] flower`)
- Inline LoRAs (`flower <lora:sunflowers:1.2>`)
- 100+ style presets
- Negative ADM guidance and SAG sharpness
- Native refiner swap inside single k-sampler
- Multi-user mode (`--multi-user`)
- Localization / I18N (`--language`)

---

## Minimum hardware

| Setup | GPU | VRAM | System RAM |
|-------|-----|------|-----------|
| Mode 1 — GPU | NVIDIA/AMD | 4 GB+ | 8 GB |
| Mode 2 — Low VRAM | NVIDIA/AMD | < 4 GB | 8 GB |
| Mode 3 — CPU only | None | — | **64 GB** |
| Mode 4 — Auto | NVIDIA/AMD | Auto | 8 GB |
| Mode 5 — No VRAM | iGPU / none | 0 GB | **16 GB** |

---

## License

GPL-3.0 — same as upstream Fooocus.
