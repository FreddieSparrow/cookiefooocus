# Cookie-Fooocus

**Provided by CookieHostUK · Coded with Claude AI assistance**

A security-hardened, performance-optimised fork of [Fooocus](https://github.com/lllyasviel/Fooocus) with a redesigned modular architecture: 3-mode prompt engine, 2-layer safety system, unified generation controller, performance telemetry, policy profiles, n8n cloud integration, and a video generation pipeline.

---

## Legal Disclaimer

> **No Warranty.** This software is provided "as is" without warranty of any kind. The authors and CookieHostUK accept no liability for any claim, damages, or other liability arising from use of this software or its generated content.
>
> **User Responsibility.** You are solely responsible for all content generated. Ensure your use complies with all applicable local, national, and international laws, including copyright, privacy, and content regulations.
>
> **Age Policy.** This tool must not be used to generate, process, or distribute content depicting minors in any sexual or harmful context. Automated safety systems enforce this — you remain legally responsible.
>
> **No Liability for AI Output.** Generated images are produced by AI models. No representations are made about accuracy, appropriateness, or fitness for any purpose.

---

## Architecture Overview

The system is built around three strict layers that never cross:

```
┌─────────────────────────────────────────────────────────────────┐
│  CORE  (never touched)                                          │
│  SDXL inference · VAE · samplers · attention                    │
└─────────────────────────────────────────────────────────────────┘
        ↑ called by
┌─────────────────────────────────────────────────────────────────┐
│  ORCHESTRATION                                                   │
│  PromptEngine · GenerationController · Queue · Cache · Telemetry│
└─────────────────────────────────────────────────────────────────┘
        ↑ governed by
┌─────────────────────────────────────────────────────────────────┐
│  POLICY LAYER                                                    │
│  Safety (2-layer) · Rate limits · Profiles · n8n hooks          │
└─────────────────────────────────────────────────────────────────┘
```

**Full generation flow:**

```
User Prompt
  ↓
Safety Layer (2-layer — Layer 1 deterministic, Layer 2 ML optional)
  ↓
Prompt Engine (RAW / BALANCED / LLM)
  ↓
Generation Controller (queue slot, cache, VRAM budget)
  ↓
Media Router (image mode OR video mode)
  ↓
SDXL Generator [image] OR Video Pipeline [video]
  ↓
Post-generation Safety (async NSFW → ALLOW / WARN / BLOCK)
  ↓
Output Layer + Telemetry + n8n callback
```

---

## What Changed vs Previous Version

| System | Before | After |
|--------|--------|-------|
| Prompt expansion | Opaque Ollama/GPT-2, inconsistent | **3 explicit modes (RAW/BALANCED/LLM) + Prompt Trace View** |
| Caching | Multiple scattered LRU caches | **Single unified LRU cache (UnifiedCache)** |
| Safety | 7 overlapping layers, mid-pipeline blocking | **2 clean layers, post-generation image moderation** |
| Queue | Ad-hoc semaphore | **Priority queue (user > batch > background)** |
| Performance | Not observable | **Telemetry dashboard with timing for every stage** |
| Config | Hardcoded thresholds | **Profile system (creative / balanced / strict / api_safe)** |
| Automation | None | **n8n cloud + self-hosted integration** |
| Video | Not supported | **Video pipeline (SVD + AnimateDiff) with Media Router** |
| Debugging | Silent failures | **Structured SafetyDecision with layer, rule, confidence** |
| Architecture | Entangled | **Strict core / orchestration / policy separation** |

---

## New Modules

| Module | Purpose |
|--------|---------|
| [modules/prompt_engine.py](modules/prompt_engine.py) | 3-mode prompt engine with PromptTrace |
| [modules/generation_controller.py](modules/generation_controller.py) | Unified queue + cache + VRAM controller |
| [modules/telemetry.py](modules/telemetry.py) | Performance telemetry (timing, counters, dashboard) |
| [modules/safety/\_\_init\_\_.py](modules/safety/__init__.py) | 2-layer safety interface |
| [modules/n8n_integration.py](modules/n8n_integration.py) | n8n webhook receiver + callback sender |
| [modules/video/\_\_init\_\_.py](modules/video/__init__.py) | Video module — modes, presets, availability check |
| [modules/video/router.py](modules/video/router.py) | Media Router — image/video routing |
| [profiles/balanced.json](profiles/balanced.json) | Default policy profile |
| [profiles/creative.json](profiles/creative.json) | Minimal filtering, LLM expansion |
| [profiles/strict.json](profiles/strict.json) | Maximum moderation, for public deployments |
| [profiles/api_safe.json](profiles/api_safe.json) | API/webhook mode with n8n defaults |

---

## 1. Prompt Engine (3 modes)

**File:** [modules/prompt_engine.py](modules/prompt_engine.py)

### Mode A — RAW

Prompt passes directly to SDXL unchanged. No rewriting, no expansion.

```python
from modules.prompt_engine import engine, PromptMode
result = engine.run("a cyberpunk city", seed=42, mode=PromptMode.RAW)
print(result.expanded)   # → "a cyberpunk city"
```

Use when: you write your own full SDXL prompt syntax.

### Mode B — BALANCED (default)

Deterministic keyword-based structured expansion. No LLM required. Always produces the same output for the same input.

```python
result = engine.run("a cyberpunk city", seed=42, mode=PromptMode.BALANCED)
print(result.expanded)
# → "a cyberpunk city, neon glow, volumetric fog, rim lighting,
#    cinematic, ultra-detailed, concept art, wide angle,
#    dramatic perspective, dystopian, masterpiece, best quality, highly detailed"
```

Use when: you want reproducible results without running an LLM.

### Mode C — LLM

Uses Ollama (local) with constrained JSON output to prevent prompt injection:

```json
{
  "subject": "...",
  "style": "...",
  "lighting": "...",
  "composition": "..."
}
```

Falls back silently to BALANCED if Ollama is unavailable.

```python
result = engine.run("a cyberpunk city", seed=42, mode=PromptMode.LLM)
```

### Prompt Trace View

Every result includes a full trace — what was added, why, and which mode was active:

```python
print(result.trace.display())
```

Output:
```
Mode: BALANCED
Original: 'a cyberpunk city'
Added:    + lighting: neon glow, volumetric fog, rim lighting | + style: cinematic, ultra-detailed, concept art | + composition: wide angle, dramatic perspective, dystopian | + masterpiece | + best quality | + highly detailed
Note: Deterministic structured expansion applied.  No LLM required.
```

This trace is shown in the UI so users always know exactly what happened to their prompt.

---

## 2. Generation Controller

**File:** [modules/generation_controller.py](modules/generation_controller.py)

Single authority for queue, cache, and hardware. Import the singleton:

```python
from modules.generation_controller import controller

# Expand prompt with cache
expanded = controller.expand_prompt("a city", seed=42, mode="balanced")

# Acquire a generation slot with priority
with controller.queue.slot(priority=0, task_id="user-request-1"):
    run_sdxl(expanded)

# Full status snapshot
print(controller.status())
```

### Priority levels

| Priority | Who uses it |
|----------|-------------|
| 0 | User foreground request (highest) |
| 1 | Batch / background generation |
| 2 | Background safety checks (lowest) |

### Unified cache

All caching goes through one `UnifiedCache` with namespaces:

| Namespace | What's stored |
|-----------|--------------|
| `prompt` | Expanded prompt strings |
| `embed` | CLIP embeddings |
| `nsfw` | NSFW classifier scores |

---

## 3. Telemetry Dashboard

**File:** [modules/telemetry.py](modules/telemetry.py)

Every stage in the pipeline is timed automatically:

```python
from modules.telemetry import telemetry

with telemetry.timer("generation_ms"):
    image = sdxl.run(prompt)

print(telemetry.dashboard())
```

Output:
```
── Performance Dashboard ──────────────────────
  Prompt Expand Ms             avg      48.2  min      32.1  max     120.4  n=24
  Safety Check Ms              avg      12.7  min       8.3  max      45.2  n=24
  Generation Ms                avg    4218.5  min    3720.0  max    5100.0  n=24
  Nsfw Check Ms                avg     210.3  min     180.0  max     290.0  n=24
  Queue Wait Ms                avg       0.0  min       0.0  max       0.0  n=24
── Counters ───────────────────────────────────
  blocked_prompts                            3
────────────────────────────────────────────────
```

---

## 4. 2-Layer Safety System

**File:** [modules/safety/\_\_init\_\_.py](modules/safety/__init__.py)

### Layer 1 — Deterministic (always runs, fast, no ML)

Hard rules only:
- CSAM and child safety
- Violence instructions
- Explicit jailbreak patterns
- Undress/explicit sexual intent (clean regex)

### Layer 2 — ML classifier (optional, edge cases only)

- Transformer-based injection detection
- Only for ambiguous prompts
- Configurable threshold — not blindly blocking

### Image moderation — post-generation

Images are no longer blocked mid-pipeline. Generation runs freely, then:

```
Post-generation:
  NSFW score < warn_threshold  → SHOW
  NSFW score ≥ warn_threshold  → BLUR + warning
  NSFW score ≥ block_threshold → HIDE
```

This eliminates wasted GPU runs and improves UX.

### Structured safety reasons

Every decision returns a safe-to-surface reason object:

```python
from modules.safety import check_prompt
decision = check_prompt("my prompt")

print(decision.allowed)            # True / False
print(decision.reason.layer)       # "deterministic" | "ml" | "none"
print(decision.reason.rule)        # "hard_block" | "content_rule" | "pass"
print(decision.reason.confidence)  # 0.0–1.0
```

Callers see the layer and rule category — never the internal regex pattern (which would allow bypass tuning).

---

## 5. Policy Profiles

**Directory:** [profiles/](profiles/)

Instead of editing thresholds directly in `safety_policy.json`, load a named profile:

| Profile | Description |
|---------|-------------|
| [balanced.json](profiles/balanced.json) | Default. Moderate filtering, BALANCED prompt mode. |
| [creative.json](profiles/creative.json) | Minimal filtering, LLM expansion. For local/personal use only. |
| [strict.json](profiles/strict.json) | Maximum moderation, low thresholds, full logging. For public deployments. |
| [api_safe.json](profiles/api_safe.json) | For programmatic access and n8n automation. Includes n8n config block. |

Profiles are read-only references. Copy values into `safety_policy.json` to apply them. This avoids the system ever auto-modifying active policy.

---

## 6. n8n Integration

**File:** [modules/n8n_integration.py](modules/n8n_integration.py)

Connect Cookie-Fooocus to any n8n workflow — cloud or self-hosted — in both local and server mode.

### Setup

**Step 1 — Add n8n config to `safety_policy.json`:**

```json
{
  "n8n": {
    "enabled": true,
    "token": "your-secret-webhook-token",
    "callback_url": "https://your-n8n-cloud.app.n8n.cloud/webhook/cookiefooocus"
  }
}
```

Or copy [profiles/api_safe.json](profiles/api_safe.json) as a starting point.

**Step 2 — Register routes in `webui.py`** (one line, at the bottom after Gradio app creation):

```python
from modules.n8n_integration import register_routes
register_routes(app)
```

**Step 3 — In n8n, create a Webhook Trigger node:**

```
Method:   POST
Path:     /cookiefooocus
Auth:     Header  →  X-CF-Token: your-secret-webhook-token
```

### Sending a generation request from n8n

Create an HTTP Request node in n8n:

```
Method:  POST
URL:     http://your-host:7865/cf/webhook/generate
Headers: X-CF-Token: your-secret-webhook-token
Body (JSON):
{
  "prompt":       "a cyberpunk city at night",
  "seed":         12345,
  "prompt_mode":  "balanced",
  "job_id":       "n8n-job-001"
}
```

### Response from Cookie-Fooocus → n8n

```json
{
  "job_id":       "n8n-job-001",
  "status":       "queued",
  "prompt_trace": {
    "mode":     "balanced",
    "original": "a cyberpunk city at night",
    "added":    ["lighting: neon glow", "style: cinematic"]
  },
  "safety": {
    "allowed":    true,
    "decision":   "allow",
    "layer":      "none",
    "confidence": 1.0
  }
}
```

When generation completes, Cookie-Fooocus POSTs back to your `callback_url`:

```json
{
  "event":        "complete",
  "job_id":       "n8n-job-001",
  "status":       "complete",
  "image_base64": "iVBORw0KGgo...",
  "image_mime":   "image/png",
  "telemetry":    { "generation_ms": { "avg": 4218 } }
}
```

### Full webhook payload schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `prompt` | string | Yes | User prompt |
| `negative_prompt` | string | No | Negative prompt |
| `seed` | int | No | RNG seed (random if omitted) |
| `steps` | int | No | Diffusion steps (default 30) |
| `cfg` | float | No | CFG scale (default 7.0) |
| `prompt_mode` | string | No | `raw` / `balanced` / `llm` |
| `style` | string | No | Fooocus style preset name |
| `callback_url` | string | No | Override callback URL for this request |
| `job_id` | string | No | Echoed in all responses |

### Event hooks

n8n also receives real-time events for monitoring:

| Event | When |
|-------|------|
| `blocked` | Prompt blocked by safety layer |
| `complete` | Generation finished, image attached |
| `queue_wait` | Job waited >5s in queue (for alerting) |

### Local mode vs server mode

| Mode | Webhook binds to | Auth required |
|------|-----------------|---------------|
| Local | `localhost:7865` | Yes — token always required |
| Server (`--server --listen`) | All interfaces (or `--listen IP`) | Yes — token required |

---

## 7. Video Generation

**Files:** [modules/video/](modules/video/)

Video is a switchable output type — not a separate application. The same prompt, safety, and queue infrastructure is reused.

### UI concept

```
[ Image ]   [ Video ]        ← mode toggle (same interface)

Prompt: ________________________
Duration:  2s | 4s | 6s | 10s
FPS:       12 | 24
Motion:    smooth | cinematic | handheld | zoom | orbit | parallax
```

On any generated image, a button appears:
```
[ Upscale ]  [ Vary ]  [ Inpaint ]  [ Animate ▶ ]
```

Clicking "Animate" sends the image to the img2vid pipeline with the same seed and prompt.

### Motion presets

| Preset | Description |
|--------|-------------|
| smooth | Stable camera, fluid movement |
| cinematic | Dolly + rack focus |
| handheld | Slight natural shake |
| zoom | Slow telephoto pull |
| orbit | 360° turntable rotation |
| parallax | Foreground/background depth separation |
| dolly | Vertigo zoom effect |
| drone | Aerial descending shot |

### Backends

| Backend | Mode | Best for |
|---------|------|---------|
| SVD (Stable Video Diffusion) | img2vid | Short clips, most stable |
| AnimateDiff | text2vid, img2vid | Flexible motion, SDXL-based |

### Calling the media router

```python
from modules.video.router import generate, GenerationJob
from modules.video import MediaMode

# Image generation (default — nothing changes)
job = generate("a cyberpunk city", mode="image", seed=42)

# Video generation
job = generate(
    "a cyberpunk city at night",
    mode="video",
    seed=42,
    duration_s=6.0,
    fps=24,
    motion_preset="cinematic",
    backend="svd",
)
print(job.status)       # "queued"
print(job.expanded)     # expanded prompt with motion suffix
```

### Safety in video mode

- Prompt filter runs before routing (identical to image mode)
- Per-frame NSFW check post-generation (not mid-pipeline)
- No new safety modules introduced

### Roadmap

| Phase | Features |
|-------|---------|
| Phase 1 (MVP) | SVD img2vid + FFmpeg + local CLI |
| Phase 2 | AnimateDiff + motion presets + consistency engine (RAFT optical flow) |
| Phase 3 | Scene builder + prompt timeline editor + keyframe locking |
| Phase 4 | Multi-shot video + character consistency + "Regenerate Segment" |

---

## Installation

### Prerequisites

- Python 3.10+
- Git
- NVIDIA/AMD GPU 4 GB+ VRAM, **or** Apple Silicon Mac 32 GB+ unified memory, **or** 16 GB+ system RAM

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

### Option B — Server Mode (multi-user, with login)

**macOS / Linux:**
```bash
git clone https://github.com/FreddieSparrow/cookiefooocus.git
cd cookiefooocus
bash install_server.sh
cp auth.json.example auth.json
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

---

### Optional: Ollama (for LLM prompt mode)

Ollama is used by Mode C (LLM) in the prompt engine. Hardware requirements:
- Apple Silicon Mac — 32 GB+ unified memory
- PC — 26 GB+ RAM and 12 GB+ VRAM

If requirements not met, Cookie-Fooocus falls back to BALANCED mode automatically.

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4
ollama serve
```

Override host or model:
```bash
OLLAMA_HOST=http://192.168.1.10:11434 OLLAMA_MODEL=gemma4 python entry_with_update.py
```

### First launch

A setup wizard runs once, asking for a hardware mode (1–6). To re-run: delete `~/.config/cookiefooocus/first_run.json`.

---

## Hardware Modes

| # | Mode | Who it's for | Min requirement |
|---|------|-------------|-----------------|
| 1 | GPU (VRAM) | NVIDIA/AMD GPU | 4 GB+ VRAM |
| 2 | Low VRAM | GPU < 4 GB VRAM | < 4 GB VRAM |
| 3 | CPU only | No GPU, high-core server | 64 GB+ RAM |
| 4 | Auto-detect | Let the app decide | — |
| 5 | No VRAM / RAM | iGPU or no GPU | 16 GB+ DDR4/DDR5 |
| 6 | Apple Silicon | M-series Mac | 32 GB+ unified memory |

---

## Content Safety System

### 2-layer architecture

**Layer 1 — Deterministic (always active, no ML):**

| Rule | What it catches |
|------|----------------|
| Hard block (CRITICAL) | CSAM, WMD synthesis — critical alert written to disk |
| Hard block | Deepfake nudity, weapons synthesis, prompt injection |
| Adult filter | Permanently enabled — explicit sexual content |
| Intent patterns | Indirect phrasing: "remove her clothes", "undress the subject" |
| Fuzzy keywords | Edit-distance matching (intentional misspellings) |

**Layer 2 — ML classifier (optional, edge cases only):**
- Transformer-based injection detection (DeBERTa primary, fallbacks)
- Runs only for ambiguous prompts
- Returns a score, not a direct block decision by default

### Image moderation (post-generation)

Images are checked **after** generation completes:

| NSFW score | Action |
|-----------|--------|
| < warn threshold | Show normally |
| ≥ warn threshold | Blur + warning |
| ≥ block threshold | Hide result |

No GPU cycles are wasted on mid-pipeline blocking.

### Structured decision output

```python
from modules.safety import check_prompt
d = check_prompt("some prompt")
# d.allowed          → True / False
# d.reason.layer     → "deterministic" / "ml" / "none"
# d.reason.rule      → "hard_block" / "content_rule" / "pass"
# d.reason.confidence → float 0.0–1.0
```

### Policy configuration

Edit `safety_policy.json` to tune thresholds:

```json
{
  "update_channel": "stable",
  "prompt_filter": {
    "ml_threshold": 0.80,
    "risk_threshold": 6
  },
  "image_filter": {
    "nsfw_block_threshold": 0.65,
    "nsfw_warn_threshold": 0.35,
    "age_check_enabled": true
  },
  "n8n": {
    "enabled": false,
    "token": "",
    "callback_url": ""
  }
}
```

### Safety test suite

```bash
pip install pytest
python -m pytest tests/test_safety.py -v
```

70+ must-block prompts · 40+ must-allow benign prompts · normalisation unit tests.

---

## Hardened Authentication (Server Mode)

| Property | Detail |
|----------|--------|
| Algorithm | PBKDF2-HMAC-SHA256 |
| Iterations | 600,000 (OWASP 2023) |
| Salt | 32-byte random per user |
| Comparison | `hmac.compare_digest` (constant-time) |
| Session tokens | 256-bit random, 1-hour TTL |
| Roles | `admin` (full) / `user` (generate + change own password) |

### Default credentials (server mode only — change immediately)

| Field | Value |
|---|---|
| Username | `admin` |
| Password | `changeme123` |

---

## Auto-Update System

| Channel | Tracks |
|---------|-------|
| `stable` | Tagged releases only (default) |
| `beta` | Pre-release tags |
| `dev` | Latest commit on `main` |
| `off` | Disabled |

Set in `safety_policy.json`: `"update_channel": "stable"`

After any legitimate code change: `python update_manifest.py`

---

## Performance Summary

| Stage | Tracked metric | Typical time |
|-------|---------------|-------------|
| Prompt expansion | `prompt_expand_ms` | 30–120 ms |
| Safety check | `safety_check_ms` | 8–50 ms |
| Queue wait | `queue_wait_ms` | 0 ms (single user) |
| SDXL generation | `generation_ms` | 3–8 s |
| NSFW check | `nsfw_check_ms` | 150–300 ms (async) |

View live: `python -c "from modules.telemetry import telemetry; print(telemetry.dashboard())"`

---

## All Command-Line Flags

```
entry_with_update.py  [--server]
                      [--listen [IP]] [--port PORT]
                      [--share]
                      [--gpu-device-id DEVICE_ID]
                      [--always-gpu | --always-high-vram | --always-normal-vram]
                      [--always-low-vram | --always-no-vram | --always-cpu]
                      [--unet-in-bf16 | --unet-in-fp16 | --unet-in-fp8-e4m3fn]
                      [--vae-in-fp16 | --vae-in-fp32 | --vae-in-cpu]
                      [--disable-offload-from-vram]
                      [--preset PRESET] [--language LANGUAGE]
                      [--output-path PATH] [--temp-path PATH]
                      [--hf-mirror URL]
                      [--debug-mode] [--disable-image-log]
                      [--rebuild-hash-cache [THREADS]]
```

---

## Original Fooocus Features (all preserved)

- Text-to-image with SDXL — no prompt engineering needed
- Inpaint / Outpaint (Up / Down / Left / Right)
- Image Prompt (IP-Adapter)
- Upscale (1.5x / 2x) and Variation (Subtle / Strong)
- FaceSwap via InsightFace
- Wildcards (`__color__ flower`)
- Array processing (`[[red, green, blue]] flower`)
- Inline LoRAs (`<lora:sunflowers:1.2>`)
- 100+ style presets
- Multi-user mode (`--multi-user`)
- Localization / I18N (`--language`)

---

## Credits & Attribution

- **Provided by** [CookieHostUK](https://github.com/FreddieSparrow)
- **Coded with** Claude AI assistance (Anthropic)
- **Based on** [Fooocus](https://github.com/lllyasviel/Fooocus) by lllyasviel
- **License:** GPL-3.0 — same as upstream Fooocus
