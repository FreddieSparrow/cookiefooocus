# Cookie-Fooocus v2.5

**Provided by [CookieHostUK](https://cookiehost.uk) · Coded with Claude AI assistance**

A security-hardened, performance-optimised fork of [Fooocus](https://github.com/lllyasviel/Fooocus) designed to run reliably on **low-end hardware**, while scaling cleanly to high-end GPUs and **Apple Silicon (M-series)**. Rebuilt from scratch with a strict 3-layer architecture: 3-mode prompt engine, 2-layer safety system, VRAM governor, priority queue, HMAC-signed n8n integration, and a video generation pipeline.

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

## Quick Start

**If you only read one section, read this.**

### macOS / Linux

```bash
git clone https://github.com/FreddieSparrow/cookiefooocus.git
cd cookiefooocus
bash install_local.sh
bash run.sh
```

### Windows

```
1. Clone or download the repo (ZIP)
2. Double-click install_local.bat
3. Double-click run_local.bat
```

Then open: `http://localhost:7865`

That's it for local use. The rest of this README covers hardware tuning, optional features, and server mode.

---

## Version History at a Glance

| Version | What changed |
|---------|-------------|
| **Original Fooocus** | SDXL UI by lllyasviel. No queue, no safety, no API. Crashes on low VRAM. |
| **Cookie-Fooocus v1** | Added Ollama/GPT-2 prompt expansion, 7-layer safety pipeline, basic caching, PBKDF2 auth, Apple Silicon (Mode 6). Modular structure but tightly coupled. |
| **Cookie-Fooocus v2** | Full architecture redesign. 3-layer separation (core / orchestration / policy). 3-mode prompt engine with PromptTrace. 2-layer safety with clean interfaces. Priority queue replacing semaphore. Structured SafetyDecision output. |
| **Cookie-Fooocus v2.5 (this)** | Stabilisation + safety hardening. Predictive VRAM model with EWA feedback correction. L1/L2 (memory + SQLite) cache hierarchy. Decision chain logging per job. Telemetry threshold monitoring with opt-in auto-tune. Per-user queue limits. Video frame cost cap. n8n disabled by default. STANDARD (GPT-2) prompt mode. Multi-GPU topology layer. Distributed worker protocol. Cache lifecycle hardening (size/age eviction + compaction). VRAM calibration tool. Safety Explainability UI. Server mode blocked on macOS. |

---

## Why This Exists

Upstream Fooocus crashes on low VRAM. It silently falls back without telling you. It has no queue, so concurrent jobs can OOM the GPU. It has no telemetry, so you can't tell what's slow.

Cookie-Fooocus fixes those things structurally — not with config flags, but with actual architecture changes:

- **Won't crash on low VRAM** — governor adjusts quality before the job starts instead of failing mid-generation
- **No silent fallbacks** — every prompt trace tells you exactly what mode ran and why
- **No GPU overload** — priority queue with timeout and starvation prevention
- **Measurable performance** — p50/p95 latency per stage, not just averages
- **Automatable** — HMAC-signed n8n integration that's actually safe to expose

---

## v2.5 Stabilisation Changes

This release is a stability and control update, not a feature expansion. The goal: fewer hidden interactions, safer defaults, and traceable decisions.

### n8n disabled by default

`n8n.enabled` is now `false` in `safety_policy.json`. Webhook routes do not register, no HMAC signing overhead runs, and no callback threads start unless you explicitly enable it. Startup logs: `n8n integration disabled (safe mode)`.

### Auto-tune is opt-in

Telemetry collects data and fires threshold alerts by default. It does **not** modify runtime behaviour unless `safety_policy.json` explicitly sets `"telemetry": {"auto_tune": true}`. The principle: *predictive systems may suggest. Only validators may enforce.*

### Decision chain logging

Every generation job now produces a `decision_chain` — an ordered audit log of what each pipeline stage decided about the parameters:

```json
"decision_chain": [
  {"stage": "vram_model",      "action": "reduce_steps",  "reason": "predicted_vram_exceeds_budget",
   "original": {"steps": 30},  "final": {"steps": 20}},
  {"stage": "cost_validator",  "action": "approve",       "reason": "within_budget"},
  {"stage": "scheduler",       "action": "acquire_slot",  "reason": "priority=0 user=alice"}
]
```

This is returned in the n8n response and available for logging. No more "something silently changed my steps."

### L1 / L2 cache hierarchy

Both caches (prompt + NSFW) now have a two-tier structure:

| Tier | Type | Speed | Survives restart |
|------|------|-------|-----------------|
| L1 | In-memory LRU | Microseconds | No |
| L2 | SQLite on disk | Milliseconds | Yes |

L2 writes are async (daemon thread) and failures are non-fatal. Cold starts warm L1 from L2 automatically. Location: `data/cache/`.

### Predictive VRAM model with feedback correction

VRAM estimation now uses:

```
estimated = BASE_MODEL (3.5 GB) + (megapixels × pixel_cost) + (steps × step_cost)
```

After each completed job, actual VRAM peak is compared to the prediction. The `pixel_cost` coefficient is slowly corrected using EWA smoothing (α=0.1, clamped to ±2× baseline). The model gets more accurate over time without oscillating.

### Per-user queue limits

`MAX_ACTIVE_JOBS_PER_USER = 2` — a single user cannot hold more than 2 active queue slots simultaneously. `submit()` raises `TooManyJobsError` immediately (HTTP 429 equivalent). Prevents one user saturating the GPU lane.

### Video frame cost cap

`MAX_TOTAL_FRAMES = 96` — video jobs with `duration_s × fps > 96` have duration clamped before the job reaches the queue. Result is recorded in `job.metadata["frame_cap"]` so callers know if clamping occurred.

---

## What Makes This Different (Feature Impact)

### Smarter Prompting

Four explicit modes — you choose, it doesn't guess:

| Mode | What it does | Use when |
|------|-------------|----------|
| RAW | Passes your prompt unchanged | You know exactly what you want |
| BALANCED | Deterministic keyword expansion (no LLM, fast) | Best default for most users |
| STANDARD | Original Fooocus GPT-2 expansion engine | You want the classic Fooocus V2 behaviour |
| LLM | Ollama rewrites your prompt creatively | You want more expressive results and have the hardware |

Every result carries a full trace — including whether a fallback happened and why.

### Runs on Weak Hardware

The VRAM governor runs a pre-flight check before every generation. If memory is tight, it adjusts automatically:

```
Steps ↓  →  Resolution ↓  →  Precision ↓  →  Reject (only if nothing else works)
```

Instead of `CUDA out of memory`, you get a slightly lower quality image that actually generates.

### Real Performance Tracking

Telemetry records avg / p50 / p95 per stage and VRAM peak. p95 matters — it tells you your worst-case, not just your average. If generation is slow, you'll know exactly which stage is the bottleneck.

### Actual Security

- Prompt safety: 2-layer system (fast deterministic rules + optional ML classifier)
- API auth: HMAC-SHA256 signed requests with replay protection — not static tokens
- Image moderation: post-generation only, so no GPU cycles wasted on blocked prompts

### Video, Same Pipeline

Switch from image to video in the UI. Same queue, same safety, same prompt engine. No separate tooling.

---

## Hardware Guide

### Minimum — It will run, slowly

- 16 GB RAM, CPU-only mode
- Expect 5–20 minutes per image

### Recommended — Smooth experience

- 4–8 GB VRAM GPU (NVIDIA/AMD)
  **or**
- Apple Silicon Mac, 32 GB+ unified memory

### High-End — Full feature set

- 12 GB+ VRAM GPU
- Enables LLM prompt mode and stable video generation

---

## Apple Silicon (M1 / M2 / M3 / M4)

Cookie-Fooocus runs well on Apple Silicon. Select **Mode 6** on first run.

**What works:**
- MPS (Metal backend) acceleration
- Unified memory means no hard VRAM ceiling — large jobs that would OOM on a GPU with the same spec will complete
- All features including video and LLM mode (with Ollama)

**What to expect:**
- Slower than NVIDIA for raw diffusion throughput
- LLM mode requires Ollama installed separately (see Optional: Ollama below)
- Best results with 32 GB+ unified memory; 16 GB is workable with BALANCED mode only

**Recommended settings for Apple Silicon:**

```
Hardware Mode: 6 (Apple Silicon / MPS)
Prompt Mode:  BALANCED (default) — LLM if you have 32 GB+
Resolution:   768 or 1024
Steps:        20–25
```

---

## Installation (Full)

### Prerequisites

- Python 3.10+
- Git
- NVIDIA/AMD GPU 4 GB+ VRAM, **or** Apple Silicon Mac 32 GB+ unified memory, **or** 16 GB+ system RAM

### Option A — Local Mode (single user)

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
# Edit auth.json — change the admin password before starting
bash run_server.sh
```

Default credentials (change immediately): `admin` / `changeme123`

---

### Optional: Ollama (LLM Prompt Mode)

Only needed if you want Mode C (LLM) prompt expansion. Falls back to BALANCED automatically if not installed — with an explicit `fallback_reason` in the trace.

Hardware requirement: Apple Silicon 32 GB+, or PC with 26 GB+ RAM and 12 GB+ VRAM.

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull gemma4
ollama serve
```

---

## Hardware Modes (First-Run Wizard)

On first run you'll be asked to select a hardware mode. Pick the one that matches your system:

| # | Mode | When to use |
|---|------|-------------|
| 1 | GPU (VRAM) | NVIDIA/AMD with 4 GB+ VRAM |
| 2 | Low VRAM | NVIDIA/AMD with less than 4 GB VRAM |
| 3 | CPU only | No GPU, 64 GB+ RAM |
| 4 | Auto-detect | Not sure — let the system decide |
| 5 | No VRAM / RAM | 16 GB+ DDR4/DDR5, minimum viable |
| 6 | Apple Silicon | M-series Mac |

---

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `CUDA out of memory` | Not using Cookie-Fooocus properly — the governor should prevent this | Select a lower hardware mode, or reduce resolution and steps manually |
| `Ollama unavailable` | Ollama not running | Run `ollama serve`, or switch to BALANCED mode |
| `Job timed out` | Generation exceeded 600s | Reduce resolution or steps; check VRAM with `telemetry.dashboard()` |
| `Invalid HMAC signature` | n8n secret mismatch | Confirm `secret` in `safety_policy.json` matches the n8n Code node |
| `Nonce already used` | Replay attack or duplicate request | Normal if retrying — generate a new nonce per request |
| Blank output / hidden image | NSFW score above block threshold | Lower `nsfw_block_threshold` in `safety_policy.json` if legitimate |
| Very slow on Apple Silicon | Using CPU mode accidentally | Confirm Mode 6 is selected; check for `mps` in startup logs |

---

## Performance Tips

### For weak hardware (< 4 GB VRAM or CPU-only)
- Use BALANCED prompt mode (not LLM)
- Set resolution to 512 or 768
- Set steps to 15–20
- The VRAM governor handles the rest automatically

### For Apple Silicon
- Use Mode 6 (MPS)
- BALANCED mode at 1024 is comfortable on 32 GB
- Avoid LLM mode unless you have 32 GB+ and Ollama running

### For high-end GPUs (12 GB+ VRAM)
- Keep fp16
- LLM mode is stable
- Video generation works well at 1024

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

## 1. Prompt Engine (4 modes)

**File:** [modules/prompt_engine.py](modules/prompt_engine.py)

### Mode A — RAW

Passes directly to SDXL unchanged. For advanced users who know exactly what they want.

```python
from modules.prompt_engine import engine, PromptMode
result = engine.run("a cyberpunk city", seed=42, mode=PromptMode.RAW)
```

### Mode B — BALANCED (default)

Deterministic keyword-based expansion. No LLM. Same input always gives same output — predictable, fast, no external dependencies.

```python
result = engine.run("a cyberpunk city", seed=42, mode=PromptMode.BALANCED)
print(result.expanded)
# → "a cyberpunk city, neon glow, volumetric fog, rim lighting, cinematic,
#    ultra-detailed, concept art, wide angle, masterpiece, best quality, highly detailed"
```

### Mode C — LLM

Ollama with constrained JSON output (`subject`, `style`, `lighting`, `composition`). Falls back to BALANCED if Ollama is unavailable — with an explicit reason recorded in the trace.

### Mode D — STANDARD (GPT-2)

Original Fooocus V2 GPT-2 expansion engine. Loaded on first use. Falls back to BALANCED if the GPT-2 model is unavailable. Use this when you want classic Fooocus expansion behaviour.

```python
result = engine.run("a cyberpunk city", seed=42, mode=PromptMode.STANDARD)
# → original Fooocus GPT-2 expanded prompt
print(result.trace.display())
# Requested: STANDARD (GPT-2)
# Executed:  STANDARD (GPT-2)
# ...
```

### Prompt Trace (no more silent fallbacks)

Every result carries a full trace:

```python
print(result.trace.display())
```

```
Requested: LLM
Executed:  BALANCED
Fallback:  Ollama unavailable or returned invalid JSON — fell back to BALANCED.
Original:  'a cyberpunk city'
Added:    + lighting: neon glow, volumetric fog, rim lighting | + style: cinematic, ultra-detailed
Note: Deterministic structured expansion applied. No LLM required.
```

`result.trace.mode_used` and `result.trace.fallback_reason` are always set.

---

## 2. Generation Controller (split responsibilities)

**Package:** [modules/generation_controller/](modules/generation_controller/)

Three sub-modules with no cross-blocking:

### Scheduler ([scheduler.py](modules/generation_controller/scheduler.py))

Priority queue with full job lifecycle management:

```
QUEUED → SCHEDULED → RUNNING → COMPLETE | FAILED | CANCELLED | TIMED_OUT
```

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

VRAM governor with auto quality downscaling. Runs before every generation. If memory is insufficient, it adjusts parameters rather than letting the job fail:

```python
ok, params = controller.check_resources(width=1024, height=1024, steps=30)
if not ok:
    return "Insufficient VRAM — request rejected"
# params.steps / params.width / params.precision may have been reduced
```

Downscale cascade (applied in order until memory fits):

1. Reduce steps (30 → 20 → 15)
2. Reduce resolution (1024 → 768 → 512)
3. Switch precision (fp16 → fp8)
4. Reject if still insufficient

`params.downscaled = True` tells the caller quality was reduced.

### Cache Manager ([modules/cache/](modules/cache/))

Two physically separate caches — different lifecycles, no shared eviction:

| Cache | Policy | Why separate |
|-------|--------|-------------|
| `prompt_cache` | LRU, no TTL | Deterministic — same input = same output forever |
| `nsfw_cache` | TTL 300s, background cleanup | Images are temp files; stale scores mislead |

---

## 3. Telemetry Dashboard

**File:** [modules/telemetry.py](modules/telemetry.py)

Tracks avg / min / max / **p50 / p95** per metric + VRAM peak. p95 is the important number — it reveals worst-case performance, not just typical performance.

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

View live:

```bash
python -c "from modules.telemetry import telemetry; print(telemetry.dashboard())"
```

Rule: telemetry never blocks execution. All recording is non-locking writes.

---

## 4. 2-Layer Safety System

**File:** [modules/safety/\_\_init\_\_.py](modules/safety/__init__.py)

Two clean layers. No overlapping responsibilities.

### Layer 1 — Deterministic (always on, no ML, fast)

| Rule | What it catches |
|------|----------------|
| Hard block (CRITICAL) | CSAM, WMD synthesis — alert written to disk |
| Hard block | Deepfake nudity, weapons synthesis, prompt injection |
| Adult filter | Permanently enabled |
| Intent patterns | "remove her clothes", "undress the subject" |
| Fuzzy keywords | Edit-distance matching against bypass attempts |

### Layer 2 — ML classifier (optional, edge cases only)

- Only runs when Layer 1 passes (no wasted compute on obvious blocks)
- DeBERTa v3 primary, fallback stack
- Returns a score — configurable threshold (default 0.80)
- Async-safe: does not block the queue

### Image moderation — post-generation only

Safety checks happen before and after generation, not during. This means no GPU cycles are wasted if a prompt is blocked, and no mid-generation interruptions.

```
After SDXL generates:
  score < warn_threshold  → SHOW
  score ≥ warn_threshold  → BLUR + warning
  score ≥ block_threshold → HIDE
```

### Structured decision output

```python
from modules.safety import check_prompt
d = check_prompt("my prompt")

d.allowed            # True / False
d.reason.layer       # "deterministic" | "ml" | "none"
d.reason.rule        # "hard_block" | "content_rule" | "ml_classifier" | "pass"
d.reason.confidence  # float 0.0–1.0
```

### Safety tests

```bash
pip install pytest
python -m pytest tests/test_safety.py -v
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

Automate generation from n8n workflows. Two modes — pick based on your setup.

### Which mode to use

| Mode | When to use | How it works |
|------|-------------|-------------|
| Simple token | Local-only / testing | Set `"simple_token_mode": true`, send `X-CF-Signature: your-secret` as a plain header |
| HMAC (recommended) | Any server / public endpoint | Full HMAC-SHA256 with replay protection and rate limiting |

**Never use simple token mode on a public server.** It provides no replay protection.

### Security model (HMAC mode)

| Protection | Mechanism |
|-----------|----------|
| Signature | HMAC-SHA256(secret, `timestamp:nonce:body`) |
| Replay prevention | Nonce stored 10 minutes — each nonce accepted once only |
| Timestamp drift | Requests older than ±5 minutes rejected |
| Payload size | 64 KB cap (configurable) |
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

**Step 2 — `webui.py`** (one line after Gradio app is created):

```python
from modules.n8n_integration import register_routes
register_routes(app)
```

**Step 3 — n8n signing (Code node, runs before HTTP Request):**

```javascript
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

---

## 7. Video Generation

**Files:** [modules/video/](modules/video/)

Same UI, same queue, same safety system — video is a switchable output type, not a separate tool.

### UI

```
[ Image ]  [ Video ]          ← mode toggle

Prompt:   ___________________________________
Duration:     [ 2s ]  [ 4s ]  [ 6s ]  [ 10s ]
FPS:          [ 12 ]  [ 24 ]
Motion:       [ smooth ]  [ cinematic ]  [ handheld ]  [ zoom ]  [ orbit ]
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

| Backend | Mode | Notes |
|---------|------|-------|
| SVD (Stable Video Diffusion) | img2vid | Recommended for MVP |
| AnimateDiff | text2vid + img2vid | More flexible |

### How video reuses the image pipeline

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

## Safety Policy Config

**File:** `safety_policy.json`

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

## Full Version Comparison (Fooocus → v1 → v2 → v2.5)

| Feature | Upstream Fooocus | v1 | v2 | v2.5 (this) |
|---------|-----------------|----|----|-------------|
| **Prompt expansion** | GPT-2 only, no cache | Ollama or GPT-2, LRU cache | 3 explicit modes (RAW / BALANCED / LLM) | **Same + PromptTrace: `mode_used`, `fallback_reason` always set** |
| **Content filter** | None | 7-layer pipeline (regex + ML + fuzzy), mixed into pipeline | **2-layer: deterministic (L1) + ML optional (L2) — clean separation** | Same |
| **Image moderation** | Basic censor | NSFW classifier blocks mid-generation | **Post-generation only: SHOW / BLUR / HIDE** | Same |
| **Caching** | None | Single unified LRU cache (all lifecycles mixed) | **2 separate caches: prompt (LRU/no-TTL), nsfw (TTL 300s)** | Same |
| **Queue** | None (OOM risk) | Semaphore, FIFO, no priority | **Priority queue: user > batch > background, starvation prevention, cancellation** | Same |
| **VRAM management** | None | None | None | **VRAM governor: pre-flight check + auto step/resolution/precision downscale** |
| **Safety decisions** | None | Silent block/warn | **Structured SafetyDecision: `layer`, `rule`, `confidence`** | Same |
| **Performance observability** | None | None | None | **Telemetry: avg + p50 + p95 per stage + VRAM peak tracking** |
| **Authentication** | Plaintext passwords | **PBKDF2-HMAC-SHA256, 600k iterations, 32-byte salt** | Same | Same |
| **Session tokens** | None | **256-bit, 1-hour TTL** | Same | Same |
| **Automation / API** | None | None | None | **n8n: HMAC-SHA256 + replay protection + rate limiting** |
| **Video generation** | None | None | None | **SVD + AnimateDiff + 8 motion presets — same pipeline reused** |
| **Policy config** | Hardcoded | `safety_policy.json` (thresholds only) | Same | **Policy profiles: balanced / creative / strict / api_safe** |
| **Architecture** | Monolithic | Modular but tightly coupled | **3-layer: core / orchestration / policy — no cross-layer calls** | Same |
| **Controller** | None | Single class (bottleneck risk) | **Split: scheduler + resource_manager + cache — no cross-blocking** | Same |
| **Apple Silicon** | Partial | **Mode 6: MPS + Metal** | Same | Same |
| **Safe model loading** | Raw `torch.load()` | **Pickle allowlist** | Same | Same |
| **Security manifest** | None | **SHA-256 boot verification of `content_filter.py`** | Same | Same |
| **Auto-update** | None | **Background git pull, channel config** | Same | Same |

---

## v2.5 Engineering Improvements

These modules were added in v2.5 as architectural upgrades rather than optional polish.

### Multi-GPU Scheduling

**File:** [modules/generation_controller/gpu_topology.py](modules/generation_controller/gpu_topology.py)

Detects all CUDA / Metal devices at startup and maintains a per-GPU capacity model. The scheduler now routes jobs to the least-loaded GPU instead of stacking onto a single device.

```python
from modules.generation_controller.gpu_topology import gpu_topology

device = gpu_topology.least_loaded()
gpu_topology.mark_job_start(device.index, vram_required_gb=4.5)
# ... generation runs on device.index ...
gpu_topology.mark_job_done(device.index, actual_vram_gb=4.2, elapsed_s=11.0)

print(gpu_topology.summary())
# [{"index": 0, "name": "RTX 4090", "free_vram_gb": 18.4, "active_jobs": 0, ...},
#  {"index": 1, "name": "RTX 3080", "free_vram_gb": 7.1,  "active_jobs": 1, ...}]
```

Target outcome: linear scaling across GPUs instead of queue stacking on one device.

### Distributed Queue Mode

**File:** [modules/generation_controller/worker_protocol.py](modules/generation_controller/worker_protocol.py)

Splits the scheduler into a control plane (job assignment) and execution plane (worker nodes). Enables multi-machine rendering farm support.

```python
from modules.generation_controller.worker_protocol import control_plane, HttpWorkerNode

control_plane.register_worker(HttpWorkerNode("http://render-node-2:7866"))
control_plane.start()

job_id = control_plane.submit("job-001", params={"prompt": "..."}, priority=0)
result = control_plane.get_result(job_id)
```

Features: heartbeat monitoring, lease-based job ownership, automatic reclaim on worker timeout, HTTP transport for remote nodes.

### Cache Lifecycle Hardening

**File:** [modules/cache/prompt_cache.py](modules/cache/prompt_cache.py)

Adds tiered eviction to the L2 SQLite prompt cache so disk usage stays predictable over long runtimes.

| Policy | Detail |
|--------|--------|
| Soft cap | Evict oldest rows when L2 exceeds 5,000 entries |
| Hard cap | Never exceed 10,000 rows |
| Age pruning | Remove entries older than 30 days |
| Compaction | VACUUM runs after eviction (async, max once per hour) |
| Metrics | `evictions` and `stale_pruned` added to `stats()` |

```python
from modules.cache import prompt_cache
report = prompt_cache.prune()   # returns count of removed entries
stats  = prompt_cache.stats()
# {"hits_l1": ..., "evictions": 42, "stale_pruned": 17, ...}
```

### VRAM Model Calibration Tool

**File:** [modules/generation_controller/resource_manager.py](modules/generation_controller/resource_manager.py)

The EWA feedback model can drift over time as hardware changes. Two new methods address this:

```python
from modules.generation_controller import governor

# Run a benchmark sweep — no GPU work, pure model validation
report = governor.calibrate()
# {"max_drift_pct": 3.2, "recommendation": "ok", "combinations": [...]}

# If drift > 25%:
if report["recommendation"] == "reset":
    governor.reset_vram_model()
```

`calibrate()` compares current model predictions against the uncorrected baseline across the full resolution/step ladder and reports drift per combination.

### Safety Explainability

**File:** [modules/safety/explainability.py](modules/safety/explainability.py)

Formats the per-job `decision_chain` for display in the UI. Surfaces what every pipeline stage decided about the request and why.

```python
from modules.safety.explainability import (
    format_decision_chain_text,
    format_decision_chain_html,
    format_safety_decision_html,
)

# Plain text for logs
print(format_decision_chain_text(chain.to_dict()))
# ── Decision Chain  (job: cf-abc123) ──
#    1. 💾 VRAM Governor         reduce_steps          (predicted_vram_exceeds_budget)
#          before: {"steps": 30}
#          after:  {"steps": 20}
#    2. ✅ Cost Validator         approve
#    3. ⏳ Scheduler              acquire_slot

# HTML for Gradio UI (gr.HTML component)
html = format_decision_chain_html(chain.to_dict())
```

### Server Mode: macOS Restriction

`--server` mode now exits immediately on Apple Silicon / macOS with a clear error message. Server mode requires a Linux host with a CUDA GPU. Local mode (`bash run.sh`) continues to work normally on Mac.

---

## v2.5 Engineering Roadmap (Next Phase)

v2.5 is structurally solid but still a single-node, single-GPU-first system with partially self-tuning subsystems. The following areas are next-phase architectural targets:

### 1. True Multi-GPU Scheduling *(foundation shipped)*

The `GPUTopology` layer is in place. Next: extend `VRAMGovernor` to call `gpu_topology.device_for_job()` automatically and pass the selected device index into the generation pipeline.

### 2. Optional Distributed Queue Mode *(foundation shipped)*

The `ControlPlane` and `WorkerNode` protocol are in place. Next: wire `ControlPlane` into `webui.py` server startup and expose a `/cf/worker/execute` endpoint for remote nodes.

### 3. Cache Invalidation & Lifecycle Hardening *(shipped)*

Size-based eviction, age-based pruning, and compaction are now implemented. Next: expose cache metrics in the telemetry dashboard.

### 4. VRAM Model Calibration Tool *(shipped)*

`calibrate()` and `reset_vram_model()` are implemented. Next: add an optional periodic recalibration trigger in the telemetry monitor.

### 5. Safety Explainability UI *(shipped)*

Formatters are implemented. Next: wire `format_decision_chain_html()` into the Gradio UI as a collapsible "Generation Audit" panel on every result card.

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

- **Provided by** [CookieHostUK](https://cookiehost.uk)
- **Coded with** Claude AI assistance (Anthropic)
- **Based on** [Fooocus](https://github.com/lllyasviel/Fooocus) by lllyasviel
- **License:** GPL-3.0
