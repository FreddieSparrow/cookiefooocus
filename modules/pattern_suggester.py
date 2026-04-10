"""
Cookie-Fooocus — On-Device Pattern Suggester
─────────────────────────────────────────────
Reads the local learning store (bypass_events.jsonl) and surfaces
category clusters that may warrant new filter rules.

SAFETY RULES:
  ✔ Read-only access to learning store (never modifies content_filter.py)
  ✔ Writes suggestions to learning/suggestions.json only
  ✔ Suggestions require HUMAN REVIEW before adoption
  ✔ All on-device — no network calls

Run manually or schedule periodically:
    python -m modules.pattern_suggester

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("cookiefooocus.pattern_suggester")

_LEARN_DIR    = Path.home() / ".local" / "share" / "cookiefooocus" / "learning"
_EVENTS_LOG   = _LEARN_DIR / "bypass_events.jsonl"
_SUGGEST_FILE = _LEARN_DIR / "suggestions.json"

# ── Thresholds ─────────────────────────────────────────────────────────────────
# A suggestion is raised when a category appears this many times
_MIN_OCCURRENCES = 10

# A "high-volume spike" alert is raised above this count
_SPIKE_THRESHOLD = 50


def _load_events() -> list[dict]:
    if not _EVENTS_LOG.exists():
        return []
    events = []
    try:
        with _EVENTS_LOG.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception as exc:
        log.warning("[suggester] Could not read events: %s", exc)
    return events


def _cluster_by_category(events: list[dict]) -> dict[str, list[dict]]:
    clusters: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        cat = ev.get("category", "unknown")
        clusters[cat].append(ev)
    return dict(clusters)


def _cluster_by_reasons(events: list[dict]) -> Counter:
    reason_counts: Counter = Counter()
    for ev in events:
        for r in ev.get("reasons", []):
            reason_counts[r] += 1
    return reason_counts


def analyse() -> dict:
    """
    Analyse the learning store and return a suggestions report.

    Returns a dict with:
      - category_counts:  {category: count}
      - top_reasons:      [(reason, count), ...]
      - suggestions:      [{category, count, suggestion, priority}, ...]
      - spikes:           [category, ...]  (unusually high volume)
      - total_events:     int
    """
    events = _load_events()
    if not events:
        return {"total_events": 0, "suggestions": [], "spikes": []}

    clusters    = _cluster_by_category(events)
    reason_cnts = _cluster_by_reasons(events)
    total       = len(events)

    suggestions = []
    spikes      = []

    for category, evs in clusters.items():
        count = len(evs)
        if count >= _SPIKE_THRESHOLD:
            spikes.append({"category": category, "count": count})

        if count >= _MIN_OCCURRENCES:
            # Determine priority
            if count >= _SPIKE_THRESHOLD:
                priority = "high"
            elif count >= _MIN_OCCURRENCES * 3:
                priority = "medium"
            else:
                priority = "low"

            # Build a human-readable suggestion
            reasons = Counter(r for ev in evs for r in ev.get("reasons", []))
            top_reason = reasons.most_common(1)[0][0] if reasons else "unknown"

            suggestions.append({
                "category":   category,
                "count":      count,
                "top_reason": top_reason,
                "priority":   priority,
                "suggestion": (
                    f"Category '{category}' triggered {count} times "
                    f"(top reason: {top_reason}). "
                    f"Review bypass_events.jsonl for patterns and consider "
                    f"adding a new rule or lowering the threshold."
                ),
            })

    suggestions.sort(key=lambda s: s["count"], reverse=True)

    report = {
        "generated_at":    datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_events":    total,
        "category_counts": {cat: len(evs) for cat, evs in clusters.items()},
        "top_reasons":     reason_cnts.most_common(10),
        "spikes":          spikes,
        "suggestions":     suggestions,
        "note": (
            "These are SUGGESTIONS only. "
            "No changes have been made to content_filter.py. "
            "Review and apply manually, then run: python update_manifest.py"
        ),
    }
    return report


def run() -> None:
    """Run analysis and write suggestions.json, then print a summary."""
    report = analyse()
    total  = report.get("total_events", 0)

    if total == 0:
        print("[pattern_suggester] No learning events found. Nothing to suggest.")
        return

    _SUGGEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    _SUGGEST_FILE.write_text(json.dumps(report, indent=2))

    suggestions = report.get("suggestions", [])
    spikes      = report.get("spikes", [])

    print(f"\n[pattern_suggester] Analysed {total} events.")
    print(f"  Suggestions: {len(suggestions)}")
    print(f"  Spikes:      {len(spikes)}")
    print(f"  Report:      {_SUGGEST_FILE}\n")

    for s in suggestions[:5]:
        marker = "🔴" if s["priority"] == "high" else "🟡" if s["priority"] == "medium" else "🟢"
        print(f"  {marker} [{s['priority'].upper()}] {s['category']} × {s['count']} — {s['top_reason']}")

    if spikes:
        print("\n  ⚠️  HIGH VOLUME SPIKES (possible active attack pattern):")
        for sp in spikes:
            print(f"     {sp['category']} × {sp['count']}")

    print("\n  ⚠️  Review suggestions.json before making any filter changes.")


if __name__ == "__main__":
    run()
