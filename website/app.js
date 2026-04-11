/**
 * Cookie-Fooocus — Browser Demo
 * ─────────────────────────────────────────────────────────────────────────────
 * Generates images via Pollinations.ai (free, no API key) with Cookie-Fooocus
 * safety logic ported to JavaScript:
 *
 *   Layer 1  — deterministic prompt rules (keyword + pattern checks)
 *              mirrors modules/safety/__init__.py Layer 1
 *   Layer 2  — post-generation NSFW image classification via NSFWJS
 *              same SHOW / BLUR / HIDE thresholds as the Python version
 *
 * Safety settings (matches safety_policy.json defaults):
 *   NSFW block threshold:  0.65
 *   NSFW warn threshold:   0.35
 *
 * Provided by CookieHostUK — coded with Claude AI assistance.
 */

'use strict';

// ── Safety thresholds (mirror safety_policy.json) ───────────────────────────
const NSFW_BLOCK_THRESHOLD = 0.65;
const NSFW_WARN_THRESHOLD  = 0.35;

// ── Layer 1 — deterministic prompt safety rules ──────────────────────────────
// Direct port of the key deterministic checks from modules/safety/__init__.py.
// Hard blocks are string matches; intent patterns are regex.

const HARD_BLOCK_TERMS = [
  // CSAM
  'child porn', 'cp ', 'loli', 'shota', 'jailbait', 'preteen',
  'underage sex', 'minor sex', 'child sex', 'csam',
  // WMD / weapons synthesis
  'synthesise sarin', 'synthesize sarin', 'make nerve agent',
  'bioweapon synthesis', 'chemical weapon recipe', 'nerve gas formula',
  // Deepfake nudity of named real people
  'deepfake nude', 'fake nude celebrity',
  // Prompt injection attempts
  'ignore previous instructions', 'disregard safety', 'bypass filter',
  'jailbreak', 'act as dan', 'ignore your rules',
];

const ADULT_BLOCK_TERMS = [
  'nsfw', 'nude', 'naked', 'pornographic', 'explicit sexual',
  'genitalia', 'penis', 'vagina', 'breast nipple',
];

// Intent patterns — indirect phrasing attempts
const INTENT_PATTERNS = [
  /remove\s+(her|his|their)\s+clothes/i,
  /undress\s+the\s+(person|subject|figure|woman|man|girl|boy)/i,
  /without\s+(clothes|clothing|shirt|pants|underwear)/i,
  /make\s+(her|him|them)\s+naked/i,
  /show\s+(private|intimate)\s+parts/i,
];

/**
 * Layer 1 safety check on the prompt.
 * Returns { allowed: bool, reason: string }
 */
function checkPromptSafety(prompt) {
  const lower = prompt.toLowerCase();

  // Hard block terms
  for (const term of HARD_BLOCK_TERMS) {
    if (lower.includes(term)) {
      return { allowed: false, reason: `Blocked: contains prohibited term.` };
    }
  }

  // Adult content filter
  for (const term of ADULT_BLOCK_TERMS) {
    if (lower.includes(term)) {
      return { allowed: false, reason: `Blocked: contains adult content terms.` };
    }
  }

  // Intent patterns
  for (const pattern of INTENT_PATTERNS) {
    if (pattern.test(prompt)) {
      return { allowed: false, reason: `Blocked: intent pattern detected.` };
    }
  }

  return { allowed: true, reason: 'pass' };
}

// ── NSFWJS loader ─────────────────────────────────────────────────────────────
// NSFWJS runs in the browser via TensorFlow.js.
// We load it lazily (only when first image is generated) to avoid blocking page load.

let nsfwModel = null;
let nsfwLoading = false;

async function loadNSFWModel() {
  if (nsfwModel) return nsfwModel;
  if (nsfwLoading) {
    // Wait for in-progress load
    while (nsfwLoading) {
      await new Promise(r => setTimeout(r, 100));
    }
    return nsfwModel;
  }
  nsfwLoading = true;
  try {
    // nsfwjs requires tf to be loaded first (included in index.html via CDN)
    nsfwModel = await nsfwjs.load();
    console.log('[safety] NSFW model loaded.');
  } catch (e) {
    console.warn('[safety] NSFW model failed to load — image check skipped:', e);
    nsfwModel = null;
  }
  nsfwLoading = false;
  return nsfwModel;
}

/**
 * Layer 2 — check a loaded <img> element with NSFWJS.
 * Returns { action: 'show'|'blur'|'hide', score: float, label: string }
 */
async function checkImageSafety(imgElement) {
  const model = await loadNSFWModel();
  if (!model) return { action: 'show', score: 0, label: 'unchecked' };

  try {
    const predictions = await model.classify(imgElement);
    // predictions: [{ className: 'Porn', probability: 0.xx }, ...]
    const unsafe = predictions
      .filter(p => ['Porn', 'Hentai', 'Sexy'].includes(p.className))
      .reduce((sum, p) => sum + p.probability, 0);

    const topClass = predictions.sort((a, b) => b.probability - a.probability)[0];

    if (unsafe >= NSFW_BLOCK_THRESHOLD) {
      return { action: 'hide',  score: unsafe, label: topClass.className };
    }
    if (unsafe >= NSFW_WARN_THRESHOLD) {
      return { action: 'blur',  score: unsafe, label: topClass.className };
    }
    return { action: 'show', score: unsafe, label: topClass.className };
  } catch (e) {
    console.warn('[safety] Image classification failed:', e);
    return { action: 'show', score: 0, label: 'error' };
  }
}

// ── Image generation ──────────────────────────────────────────────────────────

let currentImageUrl = null;

async function generate() {
  const input    = document.getElementById('prompt-input');
  const btn      = document.getElementById('generate-btn');
  const sizeVal  = document.getElementById('size-select').value;
  const style    = document.getElementById('style-select').value;
  const safeMode = document.getElementById('safe-toggle').checked;

  const rawPrompt = input.value.trim();
  if (!rawPrompt) {
    input.focus();
    return;
  }

  // ── Layer 1: prompt safety check ──────────────────────────────────────────
  const safetyResult = checkPromptSafety(rawPrompt);
  if (!safetyResult.allowed) {
    showError(safetyResult.reason);
    return;
  }

  // ── Build prompt ───────────────────────────────────────────────────────────
  const fullPrompt = style ? `${rawPrompt}, ${style}` : rawPrompt;
  const [w, h]     = sizeVal.split('x').map(Number);

  // ── Show loading state ─────────────────────────────────────────────────────
  btn.disabled = true;
  btn.textContent = 'Generating…';
  showLoading('Generating image…');
  document.getElementById('demo-actions').style.display = 'none';

  // Pre-load the NSFW model in background while image generates
  loadNSFWModel();

  try {
    // ── Build Pollinations URL ───────────────────────────────────────────────
    // safe=true applies Pollinations' own filter as a secondary layer
    const seed  = Math.floor(Math.random() * 999999);
    const url   = `https://image.pollinations.ai/prompt/${encodeURIComponent(fullPrompt)}`
                + `?width=${w}&height=${h}&seed=${seed}&nologo=true`
                + (safeMode ? `&safe=true` : ``);

    currentImageUrl = url;

    // Load image
    const imgEl = document.getElementById('demo-image');
    await new Promise((resolve, reject) => {
      imgEl.onload  = resolve;
      imgEl.onerror = () => reject(new Error('Image generation failed. Try a different prompt.'));
      imgEl.src     = url;
    });

    // ── Layer 2: post-generation image safety check ────────────────────────
    showLoading('Checking image…');
    const imageCheck = await checkImageSafety(imgEl);

    hideLoading();
    hidePlaceholder();

    if (imageCheck.action === 'hide') {
      imgEl.style.display = 'none';
      showError('Image hidden by safety filter. Try a different prompt.');
      return;
    }

    if (imageCheck.action === 'blur') {
      imgEl.style.filter = 'blur(18px)';
      imgEl.style.display = 'block';
      showWarning('Image contains potentially sensitive content and has been blurred.');
    } else {
      imgEl.style.filter = '';
      imgEl.style.display = 'block';
    }

    // ── Show actions ───────────────────────────────────────────────────────
    const dlLink = document.getElementById('download-link');
    dlLink.href = url;
    document.getElementById('demo-actions').style.display = 'flex';

  } catch (err) {
    hideLoading();
    showError(err.message || 'Something went wrong. Please try again.');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Generate';
  }
}

// ── UI helpers ─────────────────────────────────────────────────────────────────

function showLoading(text) {
  document.getElementById('demo-placeholder').style.display = 'none';
  document.getElementById('demo-error').style.display = 'none';
  document.getElementById('demo-image').style.display = 'none';
  document.getElementById('demo-loading').style.display = 'flex';
  document.getElementById('loading-text').textContent = text || 'Generating…';
  document.getElementById('demo-loading').style.flexDirection = 'column';
  document.getElementById('demo-loading').style.alignItems = 'center';
}

function hideLoading() {
  document.getElementById('demo-loading').style.display = 'none';
}

function hidePlaceholder() {
  document.getElementById('demo-placeholder').style.display = 'none';
}

function showError(msg) {
  hideLoading();
  document.getElementById('demo-placeholder').style.display = 'none';
  document.getElementById('demo-image').style.display = 'none';
  document.getElementById('demo-actions').style.display = 'none';
  document.getElementById('error-text').textContent = msg;
  document.getElementById('demo-error').style.display = 'flex';
  document.getElementById('demo-error').style.flexDirection = 'column';
  document.getElementById('demo-error').style.alignItems = 'center';
}

function showWarning(msg) {
  // Insert a small warning bar above the image
  let warn = document.getElementById('safety-warning');
  if (!warn) {
    warn = document.createElement('div');
    warn.id = 'safety-warning';
    warn.style.cssText = `
      position: absolute; bottom: 0; left: 0; right: 0;
      background: rgba(251,191,36,0.15);
      border-top: 1px solid rgba(251,191,36,0.3);
      color: #fbbf24;
      font-size: 0.8rem;
      padding: 8px 14px;
      text-align: center;
    `;
    document.getElementById('demo-output').appendChild(warn);
  }
  warn.textContent = msg;
  warn.style.display = 'block';
}

// ── Tab switcher ───────────────────────────────────────────────────────────────

function showTab(id) {
  ['mac', 'win', 'server'].forEach(t => {
    document.getElementById(`tab-${t}`).style.display = t === id ? 'block' : 'none';
  });
  document.querySelectorAll('.tab-btn').forEach((btn, i) => {
    btn.classList.toggle('active', ['mac', 'win', 'server'][i] === id);
  });
}

// ── Enter key support ──────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('prompt-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      generate();
    }
  });
});
