# Cookie-Fooocus

**Provided by CookieHostUK · Coded with Claude AI assistance**

A security-hardened, performance-optimised fork of [Fooocus](https://github.com/lllyasviel/Fooocus) with a fully redesigned modular architecture: 3-mode prompt engine, 2-layer safety system, split-responsibility generation controller, VRAM governor, performance telemetry with percentiles, policy profiles, HMAC-signed n8n cloud integration, and a video generation pipeline.

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

## Fooocus vs Cookie-Fooocus v1 vs v2

| Feature | Upstream Fooocus | Cookie-Fooocus v1 | Cookie-Fooocus v2 (this) |
|---------|-----------------|-------------------|--------------------------|
| **Prompt expansion** | GPT-2 only, no cache | Ollama or GPT-2 (hardware-gated), LRU cache | **3 explicit modes (RAW / BALANCED / LLM) + PromptTrace with `mode_used` and `fallback_reason`** |
| **Content filter** | None | 7-layer pipeline (regex + ML + fuzzy + risk score) mixed with pipeline | **2-layer: deterministic (Layer 1) + ML optional (Layer 2) — clean separation** |
| **Image moderation** | Basic censor | NSFW classifier blocks mid-generation | **Post-generation only: SHOW / BLUR / HIDE — no wasted GPU cycles** |
| **Caching** | None | Single unified LRU cache (all lifecycles mixed) | **3 separate caches: prompt (LRU/no-TTL), nsfw (TTL 300s)** |
| **Queue** | None (OOM risk) | Semaphore (FIFO, no priority) | **Priority queue: user > batch > background, starvation prevention, cancellation, timeout** |
| **VRAM management** | None | None | **VRAM governor: pre-flight check + auto step/resolution/precision downscale** |
| **Safety decisions** | None | Silent block/warn | **Structured: `layer`, `rule`, `confidence` — no internal detail revealed** |
| **Performance observability** | None | None | **Telemetry: avg + p50 + p95 per stage + VRAM peak tracking** |
| **Authentication** | Plaintext passwords | PBKDF2-HMAC-SHA256 (server mode) | **Same — unchanged** |
| **Session tokens** | None | 256-bit, 1-hour TTL | **Same — unchanged** |
| **Automation / API** | None | None | **n8n integration: HMAC-SHA256 signed + replay protection + rate limiting** |
| **Video generation** | None | None | **MediaRouter: SVD + AnimateDiff + 8 motion presets — same pipeline reused** |
| **Policy config** | Hardcoded | `safety_policy.json` (thresholds) | **Policy profiles: balanced / creative / strict / api_safe** |
| **Architecture** | Monolithic | Modular (moderation/, security/, observability/) but tightly coupled | **3-layer: core / orchestration / policy — no cross-layer calls** |
| **Controller** | None | Single class (potential bottleneck) | **Split: scheduler.py + resource_manager.py + cache/ — no cross-blocking** |
| **Model loading** | Scattered | Scattered | **Hardware profile read once, VRAM governor enforces budget pre-job** |
| **Learning engine** | None | On-device bypass logging + pattern suggester | **Same — unchanged** |
| **Auto-update** | None | Background git pull, channel config | **Same — unchanged, signed releases recommended** |
| **Apple Silicon** | Partial | Mode 6: MPS + Metal | **Same — unchanged** |
| **Safe model loading** | Raw `torch.load()` | Pickle allowlist | **Same — unchanged** |
| **Security manifest** | None | SHA-256 boot verification of `content_filter.py` | **Same — unchanged** |

---

## Architecture

Three strict layers. No module owns multiple responsibilities. UI never calls pipelines directly.

```
┌─────────────────────────────────────────────────────────────────┐
│  CORE  (never touched)                                          │
│  SDXL inference · VAE · samplers · attention                    │
└─────────────────────────────────────────────────────────────────┘
        ↑ called by
┌─────────────────────────────────────────────────────────────────┐
│  ORCHESTRATION                                                   │
│  PromptEngine · Scheduler · ResourceManager · CacheManager      │
│  Telemetry · MediaRouter · VideoModule                          │
└─────────────────────────────────────────────────────────────────┘
        ↑ governed by
┌─────────────────────────────────────────────────────────────────┐
│  POLICY LAYER                                                    │
│  Safety (2-layer) · Rate limits · Profiles · n8n hooks          │
└─────────────────────────────────────────────────────────────────┘
```

**Generation flow:**

```
User Prompt
  ↓
Safety Layer (Layer 1: deterministic | Layer 2: ML async)
  ↓
VRAM Governor (pre-flight check + auto quality scaling)
  ↓
Job Scheduler (priority queue — submit & wait for slot)
  ↓
Prompt Engine (RAW / BALANCED / LLM + PromptTrace)
  ↓
Media Router → Image Pipeline  OR  Video Pipeline
  ↓
Post-generation Safety (NSFW per-frame → SHOW / BLUR / HIDE)
  ↓
Output + Telemetry + n8n signed callback
```

---

## What Changed vs Previous Version

| System | Before | After |
|--------|--------|-------|
| Prompt expansion | Opaque Ollama/GPT-2, silent fallback | **3 explicit modes + PromptTrace with `mode_used` and `fallback_reason`** |
| Caching | Single unified cache (all lifecycles mixed) | **3 separate caches: prompt (LRU), nsfw (TTL 300s, MB-capped)** |
| Queue | Simple semaphore | **Priority queue: lifecycle, timeout, cancellation, starvation prevention** |
| GPU protection | None | **VRAM governor: pre-flight check + auto step/resolution/precision scaling** |
| Safety | 7 overlapping layers, mid-pipeline blocking | **2 clean layers + post-gen image moderation (SHOW/BLUR/HIDE)** |
| Safety decisions | Opaque | **Structured SafetyDecision: layer, rule, confidence** |
| n8n auth | Static token | **HMAC-SHA256 + timestamp + nonce (replay protection) + rate limiting** |
| Performance | avg only | **avg + min + max + p50 + p95 per metric + VRAM peak tracking** |
| Controller | Single class (bottleneck risk) | **Split: scheduler, resource_manager, cache — no cross-blocking** |
| Video | Not supported | **MediaRouter + SVD/AnimateDiff pipeline + 8 motion presets** |
| Architecture | Entangled | **Strict core / orchestration / policy separation** |

---

## Module Map

```
modules/
  prompt_engine.py              ← 3-mode engine + PromptTrace
  telemetry.py                  ← timing, counters, VRAM, p50/p95
  n8n_integration.py            ← HMAC-signed webhook + callbacks
  safety/
    __init__.py                 ← 2-layer safety interface
  generation_controller/
    __init__.py                 ← façade (controller singleton)
    scheduler.py                ← priority queue + job lifecycle
    resource_manager.py         ← VRAM governor + hardware profile
  cache/
    __init__.py                 ← cache manager
    prompt_cache.py             ← LRU, no TTL (deterministic)
    nsfw_cache.py               ← TTL 300s, background cleanup thread
  video/
    __init__.py                 ← MediaMode, MotionPreset, 8 presets
    router.py                   ← MediaRouter (image vs video routing)
profiles/
  balanced.json                 ← default
  creative.json                 ← minimal filtering, LLM expansion
  strict.json                   ← maximum moderation
  api_safe.json                 ← programmatic access + n8n defaults
```

---

## 1. Prompt Engine (3 modes)

**File:** [modules/prompt_engine.py](modules/prompt_engine.py)

### Mode A — RAW

Passes directly to SDXL unchanged. For advanced users.

```python
from modules.prompt_engine import engine, PromptMode
result = engine.run("a cyberpunk city", seed=42, mode=PromptMode.RAW)
```

### Mode B — BALANCED (default)

Deterministic keyword-based expansion. No LLM. Same input always gives same output.

```python
result = engine.run("a cyberpunk city", seed=42, mode=PromptMode.BALANCED)
print(result.expanded)
# → "a cyberpunk city, neon glow, volumetric fog, rim lighting, cinematic,
#    ultra-detailed, concept art, wide angle, masterpiece, best quality, highly detailed"
```

### Mode C — LLM

Ollama with constrained JSON output (`subject`, `style`, `lighting`, `composition`).
Falls back to BALANCED if Ollama unavailable — with an explicit reason in the trace.

### Prompt Trace View

Every result carries a full trace of what happened — including whether a fallback occurred:

```python
print(result.trace.display())
```

```
Requested: LLM
Executed:  BALANCED
Fallback:  Ollama unavailable or returned invalid JSON — fell back to BALANCED.
Original:  'a cyberpunk city'
Added:    + lighting: neon glow, volumetric fog, rim lighting | + style: cinematic, ultra-detailed
Note: Deterministic structured expansion applied.  No LLM required.
```

`result.trace.mode_used` and `result.trace.fallback_reason` are always set — no more silent fallbacks.

---

## 2. Generation Controller (split responsibilities)

**Package:** [modules/generation_controller/](modules/generation_controller/)

Three sub-modules with no cross-blocking:

### Scheduler ([scheduler.py](modules/generation_controller/scheduler.py))

Priority queue with full job lifecycle:

```
QUEUED → SCHEDULED → RUNNING → COMPLETE | FAILED | CANCELLED | TIMED_OUT
```

Features:
- Priority 0 (user) / 1 (batch) / 2 (background)
- Starvation prevention — low-priority jobs promoted after 30s wait
- Per-job timeout (default 600s)
- Cancellation tokens checked before GPU work starts

```python
from modules.generation_controller import controller

# Context manager — acquires slot, starts job, releases on exit
with controller.slot(priority=0, job_id="user-42") as job:
    if job.is_cancelled():
        return
    run_sdxl(expanded_prompt)
```

### Resource Manager ([resource_manager.py](modules/generation_controller/resource_manager.py))

VRAM governor with auto quality downscaling:

```python
ok, params = controller.check_resources(width=1024, height=1024, steps=30)
if not ok:
    return "Insufficient VRAM — request rejected"
# params.steps / params.width / params.precision may have been reduced
```

Downscale cascade (in order):
1. Reduce steps (30 → 20 → 15)
2. Reduce resolution (1024 → 768 → 512)
3. Switch precision (fp16 → fp8)
4. Reject if still insufficient

`params.downscaled = True` tells the caller quality was reduced.

### Cache Manager ([modules/cache/](modules/cache/))

Three physically separate caches — different lifecycles, no shared eviction:

| Cache | Policy | Why separate |
|-------|--------|-------------|
| `prompt_cache` | LRU, no TTL | Deterministic — same input = same output forever |
| `nsfw_cache` | TTL 300s, background cleanup | Images are temp files; stale scores mislead |

---

## 3. Telemetry Dashboard

**File:** [modules/telemetry.py](modules/telemetry.py)

Tracks avg / min / max / **p50 / p95** per metric + VRAM peak:

```python
from modules.telemetry import telemetry

with telemetry.timer("generation_ms"):
    image = sdxl.run(prompt)

telemetry.record_vram()   # sample peak VRAM after generation
print(telemetry.dashboard())
```

Output:
```
── Performance Dashboard ──────────────────────────────────────
  Prompt Expand Ms              avg     48.2ms  p50     42.0  p95    115.0  max    120.4  n=24
  Safety Check Ms               avg     12.7ms  p50     10.1  p95     44.0  max     45.2  n=24
  Generation Ms                 avg   4218.5ms  p50   4100.0  p95   5050.0  max   5100.0  n=24
  Nsfw Check Ms                 avg    210.3ms  p50    200.0  p95    285.0  max    290.0  n=24
  Vram Peak Mb                  avg   7240.0MB  p50   7200.0  p95   7800.0  max   8100.0  n=24
── Counters ────────────────────────────────────────────────────
  blocked_prompts                                3
────────────────────────────────────────────────────────────────
```

Rule: telemetry must never block execution. All recording is non-locking writes.

---

## 4. 2-Layer Safety System

**File:** [modules/safety/\_\_init\_\_.py](modules/safety/__init__.py)

### Layer 1 — Deterministic (always runs, no ML, fast)

| Rule | What it catches |
|------|----------------|
| Hard block (CRITICAL) | CSAM, WMD synthesis — critical alert to disk |
| Hard block | Deepfake nudity, weapons synthesis, prompt injection |
| Adult filter | Permanently enabled |
| Intent patterns | "remove her clothes", "undress the subject" |
| Fuzzy keywords | Edit-distance matching |

### Layer 2 — ML classifier (optional, edge cases only)

- Runs only when Layer 1 passes
- DeBERTa v3 primary, fallback stack
- Returns a score — configurable threshold
- Async-safe: does not block queue

### Image moderation — post-generation only

```
After SDXL generates:
  score < warn_threshold  → SHOW
  score ≥ warn_threshold  → BLUR + warning
  score ≥ block_threshold → HIDE
```

No mid-pipeline blocking. No wasted GPU cycles.

### Structured decision output

```python
from modules.safety import check_prompt
d = check_prompt("my prompt")

d.allowed            # True / False
d.reason.layer       # "deterministic" | "ml" | "none"
d.reason.rule        # "hard_block" | "content_rule" | "ml_classifier" | "pass"
d.reason.confidence  # float 0.0–1.0
```

---

## 5. Policy Profiles

**Directory:** [profiles/](profiles/)

| Profile | Use case |
|---------|---------|
| [balanced.json](profiles/balanced.json) | Default. Moderate filtering, BALANCED expansion. |
| [creative.json](profiles/creative.json) | Minimal filtering, LLM expansion. Local/personal only. |
| [strict.json](profiles/strict.json) | Max moderation. Public deployments. |
| [api_safe.json](profiles/api_safe.json) | Programmatic / n8n use. Includes n8n config block. |

Copy values into `safety_policy.json` to apply. Profiles are never auto-applied.

---

## 6. n8n Integration (HMAC-signed)

**File:** [modules/n8n_integration.py](modules/n8n_integration.py)

### Security model

Every request uses HMAC-SHA256 — not a static token. This prevents replay attacks.

| Protection | Mechanism |
|-----------|----------|
| Signature | HMAC-SHA256(secret, `timestamp:nonce:body`) |
| Replay prevention | Nonce stored for 10 minutes — each nonce accepted once only |
| Timestamp drift | Requests > ±5 minutes old are rejected |
| Payload size | 64KB cap (configurable) |
| Schema | Strict field whitelist — unknown fields ignored |
| Rate limit | 30 requests / 60s per source IP (configurable) |

### Setup

**Step 1 — `safety_policy.json`:**

```json
{
  "n8n": {
    "enabled":           true,
    "secret":            "your-32-char-or-longer-secret-key",
    "callback_url":      "https://your.n8n.cloud/webhook/cookiefooocus",
    "simple_token_mode": false,
    "rate_limit_rpm":    30
  }
}
```

For local/simple use where signing is too complex, set `"simple_token_mode": true` — this
accepts `X-CF-Signature: your-secret` as a plain string. Never use simple mode on a public server.

**Step 2 — `webui.py`** (one line after Gradio app is created):

```python
from modules.n8n_integration import register_routes
register_routes(app)
```

**Step 3 — n8n signing (Code node):**

```javascript
// n8n Code node — runs before HTTP Request
const secret  = "your-secret-key";
const body    = JSON.stringify($input.item.json);
const ts      = String(Math.floor(Date.now() / 1000));
const nonce   = crypto.randomBytes(8).toString("hex");
const msg     = `${ts}:${nonce}:${body}`;
const sig     = crypto.createHmac("sha256", secret).update(msg).digest("hex");

return [{
  json: {
    body,
    headers: {
      "Content-Type":   "application/json",
      "X-CF-Timestamp": ts,
      "X-CF-Nonce":     nonce,
      "X-CF-Signature": sig,
    }
  }
}];
```

**Step 4 — HTTP Request node in n8n:**

```
Method:  POST
URL:     http://your-host:7865/cf/webhook/generate
Headers: (from Code node output)
Body:    (from Code node output)
```

### Webhook payload (n8n → CF)

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `prompt` | string | Yes | Capped at 2000 chars |
| `negative_prompt` | string | No | |
| `seed` | int | No | Random if omitted |
| `steps` | int | No | Default 30, max 150 |
| `cfg` | float | No | Default 7.0, max 30 |
| `prompt_mode` | string | No | `raw` / `balanced` / `llm` |
| `style` | string | No | Fooocus style preset |
| `callback_url` | string | No | Per-request callback override |
| `job_id` | string | No | Echoed in all responses |

### Response (CF → n8n, immediate)

```json
{
  "job_id":        "n8n-job-001",
  "status":        "queued",
  "prompt_trace": {
    "mode":            "llm",
    "mode_used":       "balanced",
    "fallback_reason": "Ollama unavailable — fell back to BALANCED.",
    "original":        "a cyberpunk city",
    "added":           ["lighting: neon glow", "style: cinematic"]
  },
  "safety": {
    "allowed":    true,
    "decision":   "allow",
    "layer":      "none",
    "confidence": 1.0
  }
}
```

### Callback (CF → n8n, when complete)

```json
{
  "event":        "complete",
  "job_id":       "n8n-job-001",
  "status":       "complete",
  "image_base64": "iVBORw0KGgo...",
  "image_mime":   "image/png",
  "telemetry": {
    "generation_ms": { "avg": 4218, "p50": 4100, "p95": 5050 }
  }
}
```

### Events forwarded to n8n

| Event | When |
|-------|------|
| `blocked` | Prompt blocked by safety |
| `complete` | Generation done, image base64 attached |
| `queue_wait` | Job waited > 5s (alerting) |

### Local vs server mode

| Mode | Binds to | Auth |
|------|---------|------|
| Local | `localhost:7865` | HMAC always required |
| Server (`--server --listen`) | All interfaces | HMAC always required |

---

## 7. Video Generation

**Files:** [modules/video/](modules/video/)

Same UI, same queue, same safety — video is a switchable output type.

### UI concept

```
[ Image ]  [ Video ]          ← mode toggle

Prompt:  ___________________________________
Duration:    [ 2s ]  [ 4s ]  [ 6s ]  [ 10s ]
FPS:         [ 12 ]  [ 24 ]
Motion:      [ smooth ]  [ cinematic ]  [ handheld ]  [ zoom ]  [ orbit ]
```

Any generated image has an **Animate ▶** button. Clicking it sends that image and seed directly to the img2vid pipeline.

### Motion presets

| Preset | Effect |
|--------|--------|
| smooth | Stable camera, fluid movement |
| cinematic | Dolly + rack focus |
| handheld | Slight naturalistic shake |
| zoom | Slow telephoto pull |
| orbit | 360° turntable |
| parallax | Foreground/background depth separation |
| dolly | Vertigo zoom effect |
| drone | Aerial descending shot |

### Backends

| Backend | Mode | Stability |
|---------|------|---------|
| SVD (Stable Video Diffusion) | img2vid | Best — recommended MVP |
| AnimateDiff | text2vid + img2vid | Most flexible |

### Video reuses image pipeline — no duplication

```python
# Image pipeline call (unchanged)
image_pipeline.run(expanded_prompt, seed, steps, ...)

# Video pipeline: same call in a frame loop
for frame_t in range(total_frames):
    latent = motion_controller(prev_latent, frame_t)
    frame  = image_pipeline.run(expanded_prompt, latent=latent, ...)
    frames.append(frame)
```

Safety runs once before routing. Per-frame NSFW runs post-generation only.

### Calling the media router

```python
from modules.video.router import generate

job = generate(
    "a cyberpunk city at night",
    mode="video",
    seed=42,
    duration_s=6.0,
    fps=24,
    motion_preset="cinematic",
    backend="svd",
)
print(job.status)      # "queued"
print(job.expanded)    # prompt + motion suffix
```

### Video roadmap

| Phase | Features |
|-------|---------|
| 1 (MVP) | SVD img2vid + FFmpeg encoder + local CLI |
| 2 | AnimateDiff + RAFT optical flow consistency + seed chaining |
| 3 | Scene builder + prompt timeline editor + keyframe locking |
| 4 | Multi-shot + character consistency + "Regenerate Segment" |

---

## Installation

### Prerequisites

- Python 3.10+
- Git
- NVIDIA/AMD GPU 4 GB+ VRAM, **or** Apple Silicon Mac 32 GB+ unified memory, **or** 16 GB+ system RAM

### Option A — Local Mode

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

### Option B — Server Mode (multi-user)

**macOS / Linux:**
```bash
bash install_server.sh
cp auth.json.example auth.json
# Edit auth.json — change admin password
bash run_server.sh
```

---

### Optional: Ollama (LLM prompt mode)

Hardware requirements for Mode C (LLM):
- Apple Silicon — 32 GB+ unified memory
- PC — 26 GB+ RAM and 12 GB+ VRAM

Falls back to BALANCED automatically with an explicit `fallback_reason` in the trace.

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4
ollama serve
```

---

## Hardware Modes (First-Run Wizard)

| # | Mode | Min requirement |
|---|------|----------------|
| 1 | GPU (VRAM) | 4 GB+ VRAM |
| 2 | Low VRAM | < 4 GB VRAM |
| 3 | CPU only | 64 GB+ RAM |
| 4 | Auto-detect | — |
| 5 | No VRAM / RAM | 16 GB+ DDR4/DDR5 |
| 6 | Apple Silicon | 32 GB+ unified memory |

---

## Content Safety System

### Layer 1 — Deterministic (always active)

| Rule | Catches |
|------|---------|
| Hard block CRITICAL | CSAM, WMD — alert written to disk |
| Hard block | Deepfake nudity, weapons, injection |
| Adult filter | Always on |
| Intent patterns | Indirect undressing requests |
| Fuzzy match | Edit-distance bypass attempts |

### Layer 2 — ML (optional, edge cases)

- DeBERTa v3 primary, fallback stack
- Configurable threshold (default 0.80)
- Never blocks deterministic decisions

### Image moderation post-generation

| Score | Action |
|-------|--------|
| < warn threshold | Show |
| ≥ warn threshold | Blur + warning |
| ≥ block threshold | Hide |

### Safety policy config (`safety_policy.json`)

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
    "secret": "",
    "callback_url": "",
    "simple_token_mode": false,
    "rate_limit_rpm": 30
  }
}
```

### Safety tests

```bash
pip install pytest
python -m pytest tests/test_safety.py -v
```

---

## Hardened Authentication (Server Mode)

| Property | Detail |
|----------|--------|
| Algorithm | PBKDF2-HMAC-SHA256 |
| Iterations | 600,000 (OWASP 2023) |
| Salt | 32-byte random per user |
| Comparison | `hmac.compare_digest` (constant-time) |
| Session tokens | 256-bit random, 1-hour TTL |
| Roles | `admin` / `user` |

Default credentials (server mode only — **change immediately**): `admin` / `changeme123`

---

## Auto-Update System

| Channel | Tracks |
|---------|-------|
| `stable` | Tagged releases (default) |
| `beta` | Pre-release tags |
| `dev` | Latest commit on `main` |
| `off` | Disabled |

After any code change: `python update_manifest.py`

---

## Performance Summary

| Stage | Metric | Typical avg | p95 |
|-------|--------|------------|-----|
| Prompt expansion | `prompt_expand_ms` | 48 ms | 115 ms |
| Safety check | `safety_check_ms` | 13 ms | 44 ms |
| Queue wait | `queue_wait_ms` | 0 ms | 0 ms |
| SDXL generation | `generation_ms` | 4.2 s | 5.1 s |
| NSFW check | `nsfw_check_ms` | 210 ms | 285 ms |
| VRAM peak | `vram_peak_mb` | 7.2 GB | 7.8 GB |

View live:

```bash
python -c "from modules.telemetry import telemetry; print(telemetry.dashboard())"
```

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

- Text-to-image with SDXL
- Inpaint / Outpaint
- Image Prompt (IP-Adapter)
- Upscale (1.5x / 2x) and Variation
- FaceSwap via InsightFace
- Wildcards, array processing, inline LoRAs
- 100+ style presets
- Multi-user mode, localization

---

## Credits & Attribution

- **Provided by** [CookieHostUK](https://github.com/FreddieSparrow)
- **Coded with** Claude AI assistance (Anthropic)
- **Based on** [Fooocus](https://github.com/lllyasviel/Fooocus) by lllyasviel
- **License:** GPL-3.0
