"""
Cookie-Fooocus — On-Device Learning Engine
────────────────────────────────────────────
Logs blocked/borderline prompts locally so bypass patterns can be
studied and turned into new filter rules.

DESIGN PRINCIPLES (safety-critical):
  ✔ Runs fully async — never blocks the generation pipeline
  ✔ Stores only SHA-256 hashes of prompts — never raw text
  ✔ Never modifies the core filter automatically (human-in-the-loop only)
  ✔ All data stays on-device in ~/.local/share/cookiefooocus/learning/
  ✔ The learning store can be deleted at any time without breaking the app

WORKFLOW:
  1. A blocked prompt is logged here (hash + metadata)
  2. pattern_suggester.py clusters similar blocked hashes
  3. Suggestions are written to learning/suggestions.json
  4. A developer reviews suggestions and adds them to content_filter.py
  5. update_manifest.py is re-run to update the manifest

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import hashlib
import json
import logging
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("cookiefooocus.learning")

# ── Storage ────────────────────────────────────────────────────────────────────
_LEARN_DIR  = Path.home() / ".local" / "share" / "cookiefooocus" / "learning"
_EVENTS_LOG = _LEARN_DIR / "bypass_events.jsonl"
_STATS_FILE = _LEARN_DIR / "stats.json"

# ── Async write queue (fire-and-forget from calling thread) ───────────────────
_write_queue: queue.Queue = queue.Queue(maxsize=500)
_writer_started = False
_writer_lock    = threading.Lock()


def _writer_thread() -> None:
    """Background thread — drains the queue and writes events to disk."""
    while True:
        try:
            entry = _write_queue.get(timeout=5)
            if entry is None:   # poison pill — stop thread
                break
            _flush_entry(entry)
        except queue.Empty:
            continue
        except Exception as exc:
            log.debug("[learning] Writer error: %s", exc)


def _ensure_writer() -> None:
    global _writer_started
    with _writer_lock:
        if not _writer_started:
            t = threading.Thread(target=_writer_thread, daemon=True, name="cf-learning-writer")
            t.start()
            _writer_started = True


def _flush_entry(entry: dict) -> None:
    try:
        _EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _EVENTS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        _update_stats(entry)
    except Exception as exc:
        log.debug("[learning] Flush failed: %s", exc)


def _update_stats(entry: dict) -> None:
    """Maintain a simple counter file for quick inspection."""
    try:
        stats: dict = {}
        if _STATS_FILE.exists():
            stats = json.loads(_STATS_FILE.read_text())

        cat = entry.get("category", "unknown")
        stats[cat] = stats.get(cat, 0) + 1
        stats["_total"] = stats.get("_total", 0) + 1
        stats["_last_event"] = entry.get("ts", "")

        _STATS_FILE.write_text(json.dumps(stats, indent=2))
    except Exception:
        pass


# ── Public API ─────────────────────────────────────────────────────────────────

def log_blocked_prompt(
    prompt:   str,
    category: str,
    reasons:  list[str],
    score:    float = 0.0,
    user_id:  str   = "anonymous",
) -> None:
    """
    Async-log a blocked prompt for later pattern analysis.

    Only the SHA-256 hash of the prompt is stored — never raw text.
    This means raw prompts cannot be reconstructed from the learning store.
    """
    _ensure_writer()

    # Double-hash: sha256(sha256(prompt)) — extra precaution
    inner = hashlib.sha256(prompt.encode("utf-8", errors="replace")).hexdigest()
    outer = hashlib.sha256(inner.encode()).hexdigest()

    entry = {
        "ts":          datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prompt_hash": outer[:32],          # 128-bit prefix, unrecoverable
        "prompt_len":  len(prompt),
        "category":    category,
        "reasons":     reasons,
        "score":       round(score, 4),
        "user_hash":   hashlib.sha256(user_id.encode()).hexdigest()[:12],
    }

    try:
        _write_queue.put_nowait(entry)
    except queue.Full:
        log.debug("[learning] Queue full — event dropped.")


def log_borderline_prompt(
    prompt:   str,
    score:    float,
    user_id:  str = "anonymous",
) -> None:
    """
    Log a prompt that scored just below the risk threshold.
    These are valuable for tuning thresholds and detecting emerging bypass patterns.
    """
    log_blocked_prompt(
        prompt=prompt,
        category="borderline",
        reasons=["near_threshold"],
        score=score,
        user_id=user_id,
    )


def get_stats() -> dict:
    """Return the current learning stats (category counts)."""
    try:
        if _STATS_FILE.exists():
            return json.loads(_STATS_FILE.read_text())
    except Exception:
        pass
    return {}


def event_count() -> int:
    """Return the number of logged events."""
    return get_stats().get("_total", 0)
