"""
runtime.server.billing_stub — SaaS billing hook (stub)
========================================================
Placeholder for future billing/metering integration.

In the current open-source version this module does nothing — all
calls are no-ops. Swap the implementations here when connecting a
real billing provider (Stripe, Paddle, etc.).

Usage:
    from runtime.server.billing_stub import record_job, check_quota

    # Before accepting a job:
    if not check_quota(user_id):
        raise QuotaExceededError(user_id)

    # After a job completes:
    record_job(user_id, job_id, cost_units=1)

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import logging

log = logging.getLogger("cookiefooocus.server.billing")


class QuotaExceededError(Exception):
    """Raised when a user has exhausted their usage quota."""
    def __init__(self, user_id: str):
        super().__init__(f"Quota exceeded for user {user_id!r}")
        self.user_id = user_id


def check_quota(user_id: str) -> bool:
    """
    Return True if the user is allowed to submit another job.
    Stub: always returns True (no quota enforcement).
    """
    return True


def record_job(user_id: str, job_id: str, cost_units: int = 1) -> None:
    """
    Record that a job was completed and deduct from quota.
    Stub: logs only, no actual deduction.
    """
    log.debug(
        "[billing] Job recorded — user=%s job=%s units=%d (stub, no-op)",
        user_id, job_id, cost_units,
    )


def get_usage(user_id: str) -> dict:
    """
    Return current usage stats for a user.
    Stub: returns zeros.
    """
    return {
        "user_id":    user_id,
        "jobs_run":   0,
        "units_used": 0,
        "quota":      None,   # None = unlimited
    }
