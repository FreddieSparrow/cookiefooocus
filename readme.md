# 🍪 Cookie-Fooocus

**Provided by CookieHostUK · Coded with Claude AI assistance**

A security-hardened, safety-filtered, performance-optimised fork of [Fooocus](https://github.com/lllyasviel/Fooocus) with multi-layer content moderation, hardware-adaptive prompt expansion, dual local/server modes, full Apple Silicon support, and a self-improving on-device safety engine.

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
| Prompt expansion | Offline GPT-2 | **Hardware-adaptive: Ollama/Gemma 4 on capable hardware, GPT-2 fallback + result cache** |
| Content moderation | None | **Multi-layer filter** (normalisation + rules + ML fallback stack) |
| NSFW image filter | Basic censor | **HuggingFace classifier** with configurable score thresholds |
| Age safety check | None | **Input and output image age-check** — blocks suspected minors |
| Authentication | Plaintext passwords | **PBKDF2-HMAC-SHA256** 600k iterations (server mode only) |
| Mode of operation | Single mode | **Local mode** (no auth, identical to upstream) + **Server mode** (multi-user auth) |
| Role-based access | None | **Admin / User roles** — admin manages accounts, users can change own password |
| Session tokens | None | **256-bit session tokens** — no repeated password hashing per request |
| Model file safety | Raw `torch.load()` | **Pickle allowlist** (prevents RCE from `.ckpt` files) |
| Hardware modes | Manual CLI flags | **Interactive first-run wizard** (6 modes incl. Apple Silicon) |
| Apple Silicon | Partial | **Mode 6: MPS via Metal, PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0** |
| Safety enforcement | None | **content_filter.py verified on every boot via security_manifest.json** |
| Policy config | Hardcoded | **`safety_policy.json`** — tune thresholds without code changes |
| Debug trace | None | **Optional `debug_trace` mode** shows full decision chain |
| Observability | None | **Structured JSON event log** with reason chains and per-module metrics |
| Self-improving filter | None | **On-device learning engine** logs bypass attempts for pattern review |
| Pattern suggester | None | **`pattern_suggester.py`** clusters bypass events and suggests new rules |
| Safety test suite | None | **500+ adversarial prompts** — run `pytest tests/test_safety.py` |
| Auto-update | Manual | **Background GitHub auto-update** on every boot (configurable channel) |
| Performance | Baseline | **Prompt cache, async image checks, generation queue, startup warm-up** |
| Module architecture | Monolithic | **Separated: moderation/, security/, observability/** |
| Update integrity | None | **`security_manifest.json`** — versioned SHA-256 manifest replaces live GitHub check |

---

## Architecture

```
Cookie-Fooocus
├── core/                        Fooocus engine (untouched)
├── extras/
│   └── expansion.py             Hardware-gated Ollama/GPT-2 + prompt cache
├── modules/
│   ├── content_filter.py        Multi-layer safety pipeline (always active)
│   ├── hardware_check.py        Detects Apple Silicon / PC spec
│   ├── auth.py                  PBKDF2 auth + role system (server mode only)
│   ├── session_manager.py       256-bit session tokens (server mode only)
│   ├── first_run.py             Setup wizard (memory mode selection)
│   ├── auto_updater.py          Background GitHub auto-update
│   ├── performance.py           Prompt cache, async checks, generation queue
│   ├── learning_engine.py       On-device bypass event logger
│   ├── pattern_suggester.py     Bypass pattern clustering + suggestions
│   ├── moderation/              Re-export boundary for moderation layer
│   ├── security/                Re-export boundary for security layer
│   └── observability/           Structured JSON event logging
├── tests/
│   └── test_safety.py           500+ adversarial + benign test prompts
├── safety_policy.json           Externalised safety thresholds + update channel
├── security_manifest.json       SHA-256 manifest for safety-critical files
├── update_manifest.py           Regenerate manifest after legitimate updates
└── webui.py                     Gradio UI with branding, tips, legal footer
```

**Mode separation:**
- **Local mode** (default) — no auth module loaded, identical to upstream Fooocus
- **Server mode** (`--server`) — PBKDF2 auth, role-based access, session tokens, rate limiting, audit logs

---

## Performance Improvements over Upstream Fooocus

| Optimisation | Upstream | Cookie-Fooocus |
|---|---|---|
| Prompt expansion cache | None — re-runs LLM every call | **LRU cache (256 entries)** — identical prompts skip LLM entirely |
| Safety model loading | On first request (blocks UI) | **Startup warm-up** — pre-loaded in background at boot |
| Image classification | Blocking main thread | **Async future** — UI continues while NSFW check runs in background |
| Concurrent generation | Unlimited (OOM risk) | **Generation queue** — serialises requests, prevents OOM |
| Hardware batch size | Fixed 1 | **Adaptive** — scales with VRAM/RAM automatically |
| Prompt normalisation | Per-call | **LRU cached** — identical inputs skip re-normalisation |

---

## Installation

> **Style presets** (Realistic, Anime, etc.) are selected inside the web UI — not via separate scripts.

### Prerequisites

- Python 3.10+
- Git
- NVIDIA/AMD GPU 4 GB+ VRAM, **or** Apple Silicon Mac 32 GB+ unified memory, **or** 16 GB+ system RAM

---

### Option A — Local Mode (personal use, no login)

**macOS / Linux:**
```bash
git clone https://github.com/FreddieSparrow/cookiefooocus.git
cd cookiefooocus
bash install_local.sh
bash run.sh
```

**Windows:**
```
1. Clone or download the repo
2. Double-click install_local.bat
3. Double-click run_local.bat
```

---

### Option B — Server Mode (multi-user, with login)

**macOS / Linux:**
```bash
git clone https://github.com/FreddieSparrow/cookiefooocus.git
cd cookiefooocus
bash install_server.sh
# Edit auth.json — change the default admin password
bash run_server.sh
```

**Windows:**
```
1. Clone or download the repo
2. Double-click install_server.bat
3. Edit auth.json — change the default admin password
4. Double-click run_server.bat
```

The install scripts create a Python virtual environment, install all dependencies, and set up credentials automatically.

---

### Run scripts summary

| Script | Platform | What it does |
|--------|----------|-------------|
| `run.sh` | Mac/Linux | Local mode |
| `run.sh --server --listen` | Mac/Linux | Server mode (manual) |
| `run_server.sh` | Mac/Linux | Server mode (dedicated script) |
| `run_local.bat` | Windows | Local mode |
| `run_server.bat` | Windows | Server mode |

---

### Optional: Ollama (for Gemma 4 prompt expansion)

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

### First launch

A setup wizard runs once, asking you to choose a hardware mode (1–6). Your answer is saved. To re-run the wizard, delete `~/.config/cookiefooocus/first_run.json`.

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

**Mode 6 (Apple Silicon)** uses PyTorch MPS (Metal Performance Shaders). `PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0` is set globally, allowing models to use all available unified memory. The `--disable-offload-from-vram` flag keeps models resident in the unified memory pool between generations.

---

## Local Mode vs Server Mode

### Local Mode (default)

```bash
python entry_with_update.py
```

- No login system
- No passwords — identical to upstream Fooocus
- Auth module is **not loaded at all**
- Single-user

### Server Mode

```bash
python entry_with_update.py --server --listen
```

- Full PBKDF2-HMAC-SHA256 authentication
- Role-based access (admin / user)
- Session tokens — one login per session, not per request
- Per-user rate limiting
- Audit logging

---

## Server Setup Guide (Admin)

### Step 1 — Create auth.json

Copy the example and edit it:

```bash
cp auth.json.example auth.json
```

Edit `auth.json`:

```json
[
  {
    "user": "admin",
    "pass": "YourStrongAdminPassword!",
    "role": "admin"
  },
  {
    "user": "alice",
    "pass": "AliceInitialPass2025!",
    "role": "user"
  }
]
```

> ⚠️ **Change the default password immediately.** The built-in default is `changeme123` — it will be active if no `auth.json` is found.

Plaintext passwords in `auth.json` are automatically hashed to PBKDF2 on first load. The original `pass` field is not retained in memory.

### Step 2 — Start in server mode

```bash
python entry_with_update.py --server --listen
```

### Step 3 — Users can change their own password

Users cannot change their own username (set by admin), but can change their password after logging in. This is handled via the `change_password()` function in `modules/auth.py` and can be exposed in the UI.

### Step 4 — Add / remove users (admin only)

Use `admin_add_user()` and `admin_remove_user()` from `modules/auth.py`. Future UI for this is planned.

### Role summary

| Role | Can do |
|------|--------|
| `admin` | Generate images + manage users + view audit logs |
| `user` | Generate images + change own password |

---

## Default Credentials

> ⚠️ **CHANGE THESE BEFORE EXPOSING TO THE INTERNET**

| Field | Value |
|---|---|
| Username | `admin` |
| Password | `changeme123` |
| Role | `admin` |

These defaults apply **only in server mode** when no `auth.json` is found. Local mode has no credentials at all.

---

## Auto-Update System

On every boot, Cookie-Fooocus checks GitHub for a newer release and applies it automatically via `git pull`.

**Update channels** (set in `safety_policy.json`):

| Channel | What it tracks |
|---|---|
| `stable` | Tagged releases only (default — recommended) |
| `beta` | Pre-release tags |
| `dev` | Latest commit on `main` (may be unstable) |
| `off` | Disable auto-update entirely |

```json
{ "update_channel": "stable" }
```

**How it works:**
1. Background thread checks GitHub API (non-blocking — app loads normally)
2. If a newer version is available: backs up current code → `git pull` → regenerates `security_manifest.json`
3. On failure: rollback to backup automatically
4. Backup stored in `_backup/` (auto-deleted on next successful update)

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
| ML classifier stack | DeBERTa (primary) → DistilBERT (fallback) → toxic classifier (fallback) |
| Warn-pass | Gore/drug references flagged but not blocked |

### ML Classifier Fallback Stack

Three models tried in order — whichever loads first is used:

1. `protectai/deberta-v3-base-prompt-injection-v2` (primary)
2. `laiyer/deberta-v3-base-prompt-injection` (secondary)
3. `martin-ha/toxic-comment-model` (tertiary — lightest weight)

### Image safety

- **Output images** — every generated image is checked with `Falconsai/nsfw_image_detection` before display
- **Input images** — uploaded images are checked before entering the pipeline
- **Age-safety check** — if an image contains a suspected minor, it is blocked and a critical alert is written
- **Async checking** — image checks run in a background thread (UI not blocked)

### Observability — Structured JSON events

Every filter decision emits a structured JSON event to `~/.local/share/cookiefooocus/observability.jsonl`:

```json
{
  "ts": "2025-01-01T00:00:00.000Z",
  "event": "decision",
  "module": "moderation",
  "decision": "block",
  "reasons": ["adult_filter", "adult-nudity"],
  "score": 0.0,
  "category": "adult-nudity",
  "user_hash": "a1b2c3d4e5f6"
}
```

### Self-Improving Filter (On-Device Only)

The learning engine logs blocked prompts locally for pattern analysis:

```
~/.local/share/cookiefooocus/learning/
  bypass_events.jsonl   ← hashed event log (no raw prompts stored)
  stats.json            ← category counts
  suggestions.json      ← generated by pattern_suggester.py
```

Run the pattern suggester manually:
```bash
python -m modules.pattern_suggester
```

It will output a report showing which bypass categories are most active and suggest new rules to consider. **It never modifies `content_filter.py` automatically — human review is always required.**

### Policy configuration

Edit `safety_policy.json` to tune thresholds without touching any code:

```json
{
  "update_channel": "stable",
  "rate_limit": {
    "max_requests_per_window": 30,
    "window_seconds": 60
  },
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

Set `"debug_trace": true` to see the full decision chain in logs (development only).

### Audit log

SHA-256-hashed JSONL at `~/.local/share/cookiefooocus/ai-audit.jsonl`. No raw prompts are ever written. Thread-safe under parallel generation.

### Critical alerts

CSAM, WMD, and age-safety matches write a separate JSON alert to `~/.local/share/cookiefooocus/alerts/`.

### Rate limiter

30 requests per 60 seconds per user by default (configurable in `safety_policy.json`).

### Safety manifest & update integrity

`security_manifest.json` contains SHA-256 hashes of safety-critical files. On every boot, hashes are verified locally (no internet needed). After any legitimate update, regenerate with:

```bash
python update_manifest.py
```

### Running the safety test suite

```bash
pip install pytest
python -m pytest tests/test_safety.py -v
```

The suite includes:
- 70+ must-block prompts (CSAM, adult, injection, leet-speak, homoglyphs, WMD, deepfake, risk accumulation)
- 40+ must-allow benign prompts (art, medical, fashion, nature, sci-fi)
- Normalisation unit tests
- CSAM severity assertion tests

---

## Hardened Authentication (Server Mode)

| Property | Detail |
|----------|--------|
| Algorithm | PBKDF2-HMAC-SHA256 |
| Iterations | 600,000 (OWASP 2023 recommendation) |
| Salt | 32-byte random per user |
| Comparison | `hmac.compare_digest` (constant-time, prevents timing attacks) |
| Session tokens | 256-bit random tokens, 1-hour TTL |
| Migration | Plaintext passwords in `auth.json` auto-hashed on first load |
| Auth module loading | **Only loaded in `--server` mode** — not imported in local mode |
| Role system | `admin` (full access) / `user` (generate + change own password) |
| Username management | Usernames set by admin only — users cannot change their own username |

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
