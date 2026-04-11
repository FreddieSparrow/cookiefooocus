"""
Cookie-Fooocus — Decision Chain
────────────────────────────────────────────────────────────────────────────────
Per-job audit log of every parameter decision made during generation setup.

Design principle:
  "Predictive systems may suggest.  Only validators may enforce."

Each stage in the pipeline that reads or modifies generation parameters
records a DecisionEntry here.  The chain is attached to the job and
available for logging, response payloads, and debugging.

This makes the difference between:
  "Something changed my steps and I don't know why"
and:
  "vram_model suggested 20 steps (predicted_vram_high).
   cost_validator approved.  scheduler did not change."

Usage:
    chain = DecisionChain(job_id="cf-123")

    # In VRAM governor:
    chain.record(
        stage="vram_model",
        action="reduce_steps",
        reason="predicted_vram_exceeds_budget",
        original={"steps": 30, "width": 1024},
        final={"steps": 20, "width": 1024},
    )

    # In cost validator:
    chain.record(stage="cost_validator", action="approve", reason="within_budget")

    # In scheduler:
    chain.record(stage="scheduler", action="no_change", reason="slot_available")

    print(chain.to_dict())

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class DecisionEntry:
    """A single recorded decision from one pipeline stage."""
    stage:    str               # e.g. "vram_model", "cost_validator", "scheduler"
    action:   str               # e.g. "reduce_steps", "approve", "reject", "no_change"
    reason:   str               # human-readable explanation
    original: Optional[dict] = None   # parameter values before this stage
    final:    Optional[dict] = None   # parameter values after this stage
    ts:       float = field(default_factory=time.monotonic)


class DecisionChain:
    """
    Thread-safe, ordered log of every parameter decision for one generation job.

    Pass the chain into each stage that may read or modify generation parameters.
    The chain is optional in all callsites — stages that receive None skip logging.

    The to_dict() output is safe to include in API responses and logs.
    """

    def __init__(self, job_id: str):
        self.job_id    = job_id
        self._entries: list[DecisionEntry] = []
        self._lock     = threading.Lock()

    def record(
        self,
        stage:    str,
        action:   str,
        reason:   str,
        original: Optional[dict] = None,
        final:    Optional[dict] = None,
    ) -> None:
        """Append a decision entry.  Never raises — recording must not fail jobs."""
        try:
            entry = DecisionEntry(
                stage=stage, action=action, reason=reason,
                original=original, final=final,
            )
            with self._lock:
                self._entries.append(entry)
        except Exception:
            pass  # recording failure must never propagate

    def to_dict(self) -> dict:
        """Serialise the chain to a plain dict suitable for JSON responses."""
        with self._lock:
            entries = list(self._entries)
        return {
            "job_id":  self.job_id,
            "entries": [
                {
                    "stage":  e.stage,
                    "action": e.action,
                    "reason": e.reason,
                    **({"original": e.original} if e.original is not None else {}),
                    **({"final":    e.final}    if e.final    is not None else {}),
                }
                for e in entries
            ],
        }

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __repr__(self) -> str:
        return f"DecisionChain(job_id={self.job_id!r}, entries={len(self)})"
