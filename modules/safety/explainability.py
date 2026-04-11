"""
Cookie-Fooocus — Safety Explainability
────────────────────────────────────────────────────────────────────────────────
Exposes the decision_chain and safety decisions in a format suitable for the
Gradio UI.  Every generation pipeline stage records what it decided, why, and
what (if anything) it changed.  This module formats that audit log into:

  - A flat summary string for display in the prompt trace panel
  - A structured dict for API / n8n response payloads
  - An HTML timeline for the dedicated Explainability panel in the UI

The UI panel surfaces:
  - Prompt filtering decision (allowed / warned / blocked)
  - VRAM adjustments (what was scaled and why)
  - Scheduler queue decision (slot acquired, priority, wait time)
  - Per-stage action / reason / outcome

Design intent:
  "Fully auditable generation pipeline for end users — trust + debugging."
  The goal is to answer: what did the system change about my request and why?

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import html as _html_lib
from typing import Any, Dict, List, Optional


# ═══════════════════════════════════════════════════════════════════════════════
#  Stage colour / icon map (for HTML timeline)
# ═══════════════════════════════════════════════════════════════════════════════

_STAGE_META: Dict[str, Dict[str, str]] = {
    "safety_l1":       {"label": "Safety Layer 1",    "colour": "#e74c3c", "icon": "🛡"},
    "safety_l2":       {"label": "Safety Layer 2",    "colour": "#e67e22", "icon": "🤖"},
    "vram_model":      {"label": "VRAM Governor",     "colour": "#3498db", "icon": "💾"},
    "cost_validator":  {"label": "Cost Validator",    "colour": "#2ecc71", "icon": "✅"},
    "scheduler":       {"label": "Scheduler",         "colour": "#9b59b6", "icon": "⏳"},
    "prompt_cache":    {"label": "Prompt Cache",      "colour": "#1abc9c", "icon": "📦"},
    "prompt_engine":   {"label": "Prompt Engine",     "colour": "#f39c12", "icon": "✏️"},
    "nsfw_check":      {"label": "NSFW Check",        "colour": "#e74c3c", "icon": "👁"},
}

_ACTION_COLOURS: Dict[str, str] = {
    "approve":           "#2ecc71",
    "allow":             "#2ecc71",
    "pass":              "#2ecc71",
    "hit":               "#2ecc71",
    "acquire_slot":      "#2ecc71",
    "reduce_steps":      "#e67e22",
    "reduce_resolution": "#e67e22",
    "reduce_precision":  "#e67e22",
    "warn":              "#e67e22",
    "blur":              "#e67e22",
    "reject":            "#e74c3c",
    "block":             "#e74c3c",
    "hide":              "#e74c3c",
    "miss":              "#95a5a6",
}


def _action_colour(action: str) -> str:
    for key, colour in _ACTION_COLOURS.items():
        if key in action.lower():
            return colour
    return "#95a5a6"


# ═══════════════════════════════════════════════════════════════════════════════
#  Plain-text summary
# ═══════════════════════════════════════════════════════════════════════════════

def format_decision_chain_text(chain_dict: Optional[Dict]) -> str:
    """
    Return a human-readable multi-line summary of a decision chain for the
    prompt trace panel.

    Args:
        chain_dict: the output of DecisionChain.to_dict()

    Returns:
        Formatted string.  Returns empty string if chain is None or empty.
    """
    if not chain_dict or not chain_dict.get("entries"):
        return ""

    lines = [f"── Decision Chain  (job: {chain_dict.get('job_id', '?')}) ──"]
    for i, entry in enumerate(chain_dict["entries"], start=1):
        stage  = entry.get("stage",  "?")
        action = entry.get("action", "?")
        reason = entry.get("reason", "")

        meta  = _STAGE_META.get(stage, {})
        label = meta.get("label", stage)
        icon  = meta.get("icon",  "•")

        line  = f"  {i:2d}. {icon} {label:<20}  {action:<24}"
        if reason:
            line += f"  ({reason})"
        lines.append(line)

        original = entry.get("original")
        final    = entry.get("final")
        if original and final and original != final:
            lines.append(f"          before: {original}")
            lines.append(f"          after:  {final}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  Structured dict (for API / n8n)
# ═══════════════════════════════════════════════════════════════════════════════

def format_decision_chain_dict(chain_dict: Optional[Dict]) -> List[Dict]:
    """
    Return a list of structured stage summaries suitable for JSON API responses.
    Each item has: stage, label, action, reason, changed (bool), original, final.
    """
    if not chain_dict or not chain_dict.get("entries"):
        return []

    result = []
    for entry in chain_dict["entries"]:
        stage    = entry.get("stage",    "")
        action   = entry.get("action",   "")
        original = entry.get("original")
        final    = entry.get("final")
        result.append({
            "stage":    stage,
            "label":    _STAGE_META.get(stage, {}).get("label", stage),
            "action":   action,
            "reason":   entry.get("reason", ""),
            "changed":  bool(original and final and original != final),
            "original": original,
            "final":    final,
            "ts":       entry.get("ts"),
        })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
#  HTML timeline (for Gradio UI panel)
# ═══════════════════════════════════════════════════════════════════════════════

def format_decision_chain_html(chain_dict: Optional[Dict]) -> str:
    """
    Return an HTML string rendering a visual timeline of the decision chain.
    Designed to be embedded in a Gradio gr.HTML() component.

    Stages are rendered as coloured cards in sequence.
    Modifications are highlighted in amber with before/after values.
    """
    if not chain_dict or not chain_dict.get("entries"):
        return "<p style='color:#888;font-family:monospace'>No decision chain available.</p>"

    job_id  = _html_lib.escape(chain_dict.get("job_id", ""))
    entries = chain_dict.get("entries", [])

    cards = []
    for entry in entries:
        stage    = entry.get("stage",  "")
        action   = entry.get("action", "")
        reason   = entry.get("reason", "")
        original = entry.get("original")
        final    = entry.get("final")
        changed  = bool(original and final and original != final)

        meta         = _STAGE_META.get(stage, {})
        stage_label  = _html_lib.escape(meta.get("label", stage))
        stage_colour = meta.get("colour", "#7f8c8d")
        action_col   = _action_colour(action)
        icon         = meta.get("icon", "•")

        border = "2px solid #e67e22" if changed else f"1px solid {stage_colour}33"

        card = f"""
        <div style="
            border-left: 4px solid {stage_colour};
            border: {border};
            border-radius: 6px;
            padding: 8px 12px;
            margin: 6px 0;
            background: #1a1a2e;
            font-family: monospace;
            font-size: 13px;
        ">
          <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
            <span style="font-size:16px">{icon}</span>
            <span style="color:{stage_colour}; font-weight:bold">{stage_label}</span>
            <span style="
                background:{action_col};
                color:#fff;
                border-radius:4px;
                padding:1px 7px;
                font-size:11px;
                font-weight:bold;
                margin-left:auto;
            ">{_html_lib.escape(action)}</span>
          </div>
          {"" if not reason else f'<div style="color:#aaa; font-size:12px; margin-bottom:4px">{_html_lib.escape(reason)}</div>'}
          {_render_diff_html(original, final) if changed else ""}
        </div>"""
        cards.append(card)

    joined = "\n".join(cards)
    return f"""
    <div style="font-family:monospace; padding:4px">
      <div style="color:#666; font-size:11px; margin-bottom:8px">
        Job: <span style="color:#aaa">{job_id}</span>
        &nbsp;·&nbsp; {len(entries)} stage(s)
      </div>
      {joined}
    </div>
    """


def _render_diff_html(original: Any, final: Any) -> str:
    """Render a before/after diff block for a modified parameter."""
    orig_str  = _html_lib.escape(str(original))
    final_str = _html_lib.escape(str(final))
    return f"""
    <div style="
        background:#2d1a00;
        border-radius:4px;
        padding:4px 8px;
        margin-top:4px;
        font-size:12px;
    ">
      <span style="color:#e67e22">before:</span>
      <span style="color:#ffa07a; margin-left:4px">{orig_str}</span>
      &nbsp;→&nbsp;
      <span style="color:#e67e22">after:</span>
      <span style="color:#98ff98; margin-left:4px">{final_str}</span>
    </div>"""


# ═══════════════════════════════════════════════════════════════════════════════
#  Safety decision formatter
# ═══════════════════════════════════════════════════════════════════════════════

def format_safety_decision_html(safety_decision) -> str:
    """
    Render a SafetyDecision as an HTML summary card.

    Args:
        safety_decision: SafetyDecision from modules.safety.check_prompt()

    Returns:
        HTML string for a Gradio gr.HTML() component.
    """
    if safety_decision is None:
        return "<p style='color:#888;font-family:monospace'>No safety decision.</p>"

    allowed    = getattr(safety_decision, "allowed", True)
    reason     = getattr(safety_decision, "reason", None)
    layer      = getattr(reason, "layer",      "none") if reason else "none"
    rule       = getattr(reason, "rule",       "pass") if reason else "pass"
    confidence = getattr(reason, "confidence", 1.0)    if reason else 1.0
    image_act  = getattr(safety_decision, "image_action", None)
    nsfw_score = getattr(safety_decision, "nsfw_score",   None)

    bg_colour = "#0d2b0d" if allowed else "#2b0d0d"
    status_colour = "#2ecc71" if allowed else "#e74c3c"
    status_label  = "ALLOWED" if allowed else "BLOCKED"

    nsfw_html = ""
    if nsfw_score is not None:
        nsfw_html = (
            f'<div style="color:#aaa;font-size:12px;margin-top:4px">'
            f'NSFW score: <span style="color:#f39c12">{nsfw_score:.3f}</span>'
            f'{"  →  " + str(image_act) if image_act else ""}'
            f'</div>'
        )

    return f"""
    <div style="
        background:{bg_colour};
        border: 1px solid {status_colour}55;
        border-left: 4px solid {status_colour};
        border-radius:6px;
        padding:8px 12px;
        font-family:monospace;
        font-size:13px;
    ">
      <span style="color:{status_colour}; font-weight:bold; font-size:14px">{status_label}</span>
      <span style="color:#aaa; margin-left:10px">layer: {_html_lib.escape(str(layer))}</span>
      <span style="color:#aaa; margin-left:10px">rule: {_html_lib.escape(str(rule))}</span>
      <span style="color:#aaa; margin-left:10px">confidence: {confidence:.2f}</span>
      {nsfw_html}
    </div>"""
