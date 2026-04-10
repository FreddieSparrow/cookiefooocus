# 🍪 Cookie-Fooocus

**Provided by CookieHostUK · Coded with Claude AI assistance**

A security-hardened, safety-filtered fork of [Fooocus](https://github.com/lllyasviel/Fooocus) with multi-layer content moderation, hardware-adaptive prompt expansion, dual local/server modes, and full Apple Silicon support.

---

## ⚖️ Legal Disclaimer

> **No Warranty.** This software is provided "as is" without warranty of any kind. The authors and CookieHostUK accept no liability for any claim, damages, or other liability arising from use of this software or its generated content.
>
> **User Responsibility.** You are solely responsible for all content generated. Ensure your use complies with all applicable local, national, and international laws, including copyright, privacy, and content regulations.
>
> **Age Policy.** This tool must not be used to generate, process, or distribute content depicting minors in any sexual or harmful context. Automated safety systems enforce this — you remain legally responsible.
>
> **No Liability for AI Output.** Generated images are produced by AI models. No representations are made about accuracy, appropriateness, or fitness for any purpose.

---

## What this fork adds over upstream Fooocus

| Feature | Upstream Fooocus | Cookie-Fooocus |
|---------|-----------------|----------------|
| Prompt expansion | Offline GPT-2 | **Hardware-adaptive: Ollama/Gemma 4 on capable hardware, GPT-2 fallback otherwise** |
| Content moderation | None | **Multi-layer filter** (normalisation + rules + ML) |
| NSFW image filter | Basic censor | **HuggingFace classifier** with configurable score thresholds |
| Age safety check | None | **Input and output image age-check** — blocks suspected minors |
| Authentication | Plaintext passwords | **PBKDF2-HMAC-SHA256** 600k iterations (server mode only) |
| Mode of operation | Single mode | **Local mode** (no auth, identical to upstream) + **Server mode** (multi-user auth) |
| Model file safety | Raw `torch.load()` | **Pickle allowlist** (prevents RCE from `.ckpt` files) |
| Hardware modes | Manual CLI flags | **Interactive first-run wizard** (6 modes incl. Apple Silicon) |
| Apple Silicon | Partial | **Mode 6: MPS via Metal, optimised for 32 GB+ unified memory** |
| Safety enforcement | None | **content_filter.py verified on every boot** |
| Policy config | Hardcoded | **`safety_policy.json`** — tune thresholds without code changes |
| Debug trace | None | **Optional `debug_trace` mode** shows full decision chain |

---

## Architecture

```
Cookie-Fooocus
├── core/                   Fooocus engine (untouched)
├── extras/
│   └── expansion.py        Hardware-gated Ollama/GPT-2 expansion
├── modules/
│   ├── content_filter.py   Multi-layer safety pipeline (always active)
│   ├── hardware_check.py   Detects Apple Silicon / PC spec for Ollama gate
│   ├── auth.py             PBKDF2 authentication (server mode only)
│   └── first_run.py        Setup wizard (memory mode selection)
├── safety_policy.json      Externalised safety thresholds (edit freely)
└── webui.py                Gradio UI with branding, tips, legal footer
```

**Mode separation:**
- **Local mode** (default) — no auth module loaded, identical to upstream Fooocus
- **Server mode** (`--server`) — PBKDF2 auth, rate limiting, full audit logs

---

## Installation

### 1. Prerequisites

- Python 3.10+
- Git
- An NVIDIA/AMD GPU with 4 GB+ VRAM, **or** an Apple Silicon Mac with 32 GB+ unified memory, **or** 16 GB+ system RAM for no-VRAM mode

### 2. Clone and install

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

Optional extras (enables faster fuzzy matching, ML classifiers, and NSFW image filter):
```bash
pip install rapidfuzz transformers pillow psutil
```

### 3. Ollama (optional — for Gemma 4 prompt expansion)

Ollama is only used if your hardware meets minimum requirements:
- **Apple Silicon Mac** — 32 GB+ unified memory
- **PC** — 26 GB+ RAM and 12 GB+ VRAM

If requirements are not met, Cookie-Fooocus automatically falls back to the original GPT-2 expansion engine.

```bash
# Install Ollama (macOS / Linux)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4
ollama serve
```

On Windows: download from [ollama.com](https://ollama.com) and run `ollama pull gemma4`.

Override host or model:
```bash
OLLAMA_HOST=http://192.168.1.10:11434 OLLAMA_MODEL=gemma4 python entry_with_update.py
```

### 4. First launch

```bash
python entry_with_update.py
```

A setup wizard runs once, asking you to choose a hardware mode. Your answer is saved and never asked again. To re-run, delete `~/.config/cookiefooocus/first_run.json`.

---

## Hardware Modes (First-Run Wizard)

| # | Mode | Who it's for | Min requirement |
|---|------|-------------|-----------------|
| 1 | GPU (VRAM) | NVIDIA/AMD GPU | 4 GB+ VRAM |
| 2 | Low VRAM | GPU < 4 GB VRAM | < 4 GB VRAM |
| 3 | CPU only | No GPU, high-core server | **64 GB+ RAM** |
| 4 | Auto-detect | Let the app decide | — |
| 5 | No VRAM / RAM | iGPU or no GPU | **16 GB+ DDR4/DDR5** |
| 6 | Apple Silicon | M-series Mac | **32 GB+ unified memory** |

**Mode 6 (Apple Silicon)** uses PyTorch MPS (Metal Performance Shaders). No additional drivers are needed — MPS is auto-detected by PyTorch. 32 GB unified memory is recommended for SDXL.

---

## Local Mode vs Server Mode

### Local Mode (default)

```bash
python entry_with_update.py
```

- No login system
- No passwords
- Single-user, identical behaviour to upstream Fooocus
- Auth module is **not loaded at all**

### Server Mode

```bash
python entry_with_update.py --server --listen
```

- Full PBKDF2-HMAC-SHA256 authentication (600k iterations)
- Multi-user support
- Per-user rate limiting
- Audit logging

Create `auth.json` in the project root:
```json
[
  {"user": "alice", "pass": "your-password-here"},
  {"user": "bob",   "pass": "another-password"}
]
```
Plaintext passwords are automatically hashed to PBKDF2 on first load. The original `pass` field is not stored.

---

## Content Safety System

All of the following run on every generation — they cannot be disabled by users.

### Prompt normalisation (defeats ~80% of real-world bypass tricks)

| Step | What it handles |
|------|----------------|
| Unicode NFKC | Bold/italic math, fullwidth, `𝓈𝑒𝓍 → sex` |
| Homoglyphs | Cyrillic/Greek lookalikes: `с → c`, `ο → o` |
| Leet-speak | `3→e`, `0→o`, `4→a`, `@→a` |
| Diacritics | `café → cafe` |
| Zero-width chars | Strips invisible characters |
| Spaced words | `s e x → sex` |
| Base64 sniffing | Entropy-gated decode + scan |

### Detection pipeline

| Layer | What it catches |
|-------|----------------|
| Hard block (CRITICAL) | CSAM, WMD synthesis — critical alert written to disk |
| Hard block (BLOCK) | Deepfake nudity, weapons synthesis, prompt injection |
| 18+ adult filter | **Permanently enabled** — cannot be toggled by users |
| Intent patterns | Indirect phrasing (`"remove her clothes"`, `"undress the subject"`) |
| Fuzzy keywords | Edit-distance matching (catches intentional misspellings) |
| Risk scoring | Additive keyword clusters — blocks at configurable threshold |
| ML classifier | Optional DeBERTa-based prompt-injection detector |
| Warn-pass | Gore/drug references flagged but not blocked |

### Image safety

- **Output images** — every generated image is checked with `Falconsai/nsfw_image_detection` before display
- **Input images** — uploaded images are checked before entering the pipeline
- **Age-safety check** — if an image contains a suspected minor, it is blocked and a critical alert is written

Thresholds are configurable in `safety_policy.json`.

### Policy configuration

Edit `safety_policy.json` to tune thresholds without touching any code:

```json
{
  "prompt_filter": {
    "ml_threshold": 0.80,
    "risk_threshold": 6
  },
  "image_filter": {
    "nsfw_block_threshold": 0.65,
    "nsfw_warn_threshold": 0.35,
    "age_check_enabled": true
  },
  "debug_trace": false
}
```

Set `"debug_trace": true` to see the full decision chain in `FilterResult.trace` (for development/debugging only — do not leave on in production).

### Audit log

SHA-256-hashed JSONL at `~/.local/share/cookiefooocus/ai-audit.jsonl`. No raw prompts are ever written. Thread-safe under parallel generation.

### Critical alerts

CSAM, WMD, and age-safety matches write a separate JSON alert to `~/.local/share/cookiefooocus/alerts/`.

### Rate limiter

30 requests per 60 seconds per user by default (configurable in `safety_policy.json`).

### content_filter.py integrity

On every boot, `launch.py` compares the local `modules/content_filter.py` against the upstream repository. If the file is missing, modified, or out of date, startup is blocked.

To restore:
```bash
git checkout modules/content_filter.py
```

---

## Hardened Authentication (Server Mode)

| Property | Detail |
|----------|--------|
| Algorithm | PBKDF2-HMAC-SHA256 |
| Iterations | 600,000 (OWASP 2023 recommendation) |
| Salt | 32-byte random per user |
| Comparison | `hmac.compare_digest` (constant-time, prevents timing attacks) |
| Migration | Plaintext passwords in `auth.json` auto-hashed on first load |
| Auth module loading | **Only loaded in `--server` mode** — not imported in local mode |

---

## Safe Model Loading

PyTorch `.ckpt`/`.pt` model files use Python pickle, which can execute arbitrary code. This fork replaces the default unpickler with an allowlist that only permits `torch`, `numpy`, and `collections`.

---

## All Command-Line Flags

```
entry_with_update.py  [--server]
                      [--listen [IP]] [--port PORT]
                      [--share]
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
                      [--always-offload-from-vram] [--disable-offload-from-vram]
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

## Minimum Hardware

| Setup | GPU | VRAM | System RAM |
|-------|-----|------|-----------|
| Mode 1 — GPU | NVIDIA/AMD | 4 GB+ | 8 GB |
| Mode 2 — Low VRAM | NVIDIA/AMD | < 4 GB | 8 GB |
| Mode 3 — CPU only | None | — | **64 GB** |
| Mode 4 — Auto | NVIDIA/AMD | Auto | 8 GB |
| Mode 5 — No VRAM | iGPU / none | 0 GB | **16 GB** |
| Mode 6 — Apple Silicon | Apple M-series | — (unified) | **32 GB** |

---

## Original Fooocus Features (all preserved)

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

## Credits & Attribution

- **Provided by** [CookieHostUK](https://github.com/FreddieSparrow)
- **Coded with** Claude AI assistance (Anthropic)
- **Based on** [Fooocus](https://github.com/lllyasviel/Fooocus) by lllyasviel
- **License:** GPL-3.0 — same as upstream Fooocus
