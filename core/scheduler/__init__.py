"""
core.scheduler — Priority queue and job lifecycle
===================================================
Wraps modules.generation_controller.scheduler.

Job states:
    QUEUED → SCHEDULED → RUNNING → COMPLETE | FAILED | CANCELLED | TIMED_OUT

Features:
    - Priority 0 (user) / 1 (batch) / 2 (background)
    - Starvation prevention: low-priority jobs promoted after 30s wait
    - Per-job timeout (default 600s)
    - Cancellation tokens checked before GPU work starts
    - Per-user job cap (raises TooManyJobsError at submit)

Public API:
    controller          — singleton GenerationController
    JobPriority         — enum
    TooManyJobsError    — raised when user exceeds queue limit
"""

from modules.generation_controller.scheduler import (  # noqa: F401
    JobPriority,
    TooManyJobsError,
)
from modules.generation_controller import controller  # noqa: F401
