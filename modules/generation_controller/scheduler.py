"""
Cookie-Fooocus — Job Scheduler
────────────────────────────────────────────────────────────────────────────────
Priority queue with full job lifecycle, starvation prevention, timeout,
cancellation, and per-user active-job limits.

Job lifecycle:
  QUEUED → SCHEDULED → RUNNING → COMPLETE | FAILED | CANCELLED | TIMED_OUT

Priority levels (lower = higher priority):
  0  user foreground request
  1  batch / background generation
  2  background check (NSFW, pattern analysis)

Starvation prevention:
  A lower-priority job that has waited longer than MAX_STARVATION_S is
  temporarily promoted to priority 0 for one scheduling cycle.

Cancellation:
  cancel(job_id) marks the job; the worker checks job.is_cancelled() and
  short-circuits before touching the GPU.

Per-user limits:
  A single user_id cannot hold more than MAX_ACTIVE_JOBS_PER_USER active jobs
  (QUEUED + SCHEDULED + RUNNING).  submit() raises TooManyJobsError immediately
  if the limit would be exceeded — the caller should return a 429 to the client.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import heapq
import logging
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("cookiefooocus.scheduler")

MAX_STARVATION_S       = 30.0   # promote low-priority job after this many seconds waiting
DEFAULT_TIMEOUT_S      = 600.0  # per-job timeout before giving up
MAX_ACTIVE_JOBS_PER_USER = 2    # concurrent active jobs per user_id (0 = unlimited)


class TooManyJobsError(RuntimeError):
    """
    Raised by submit() when a user_id already has MAX_ACTIVE_JOBS_PER_USER
    active (queued / scheduled / running) jobs.

    The caller should respond with HTTP 429 or equivalent.
    """


class JobState(str, Enum):
    QUEUED     = "queued"
    SCHEDULED  = "scheduled"
    RUNNING    = "running"
    COMPLETE   = "complete"
    FAILED     = "failed"
    CANCELLED  = "cancelled"
    TIMED_OUT  = "timed_out"


@dataclass
class Job:
    """Represents one generation request in the scheduling system."""
    job_id:    str
    priority:  int
    timeout_s: float
    user_id:   str                = ""    # owner — used for per-user quota enforcement
    _state:    JobState           = field(default=JobState.QUEUED, repr=False)
    _created:  float              = field(default_factory=time.monotonic, repr=False)
    _lock:     threading.Lock     = field(default_factory=threading.Lock, repr=False)
    _event:    threading.Event    = field(default_factory=threading.Event, repr=False)
    _cancel:   threading.Event    = field(default_factory=threading.Event, repr=False)

    @property
    def state(self) -> JobState:
        return self._state

    @property
    def wait_time(self) -> float:
        return time.monotonic() - self._created

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> bool:
        """Cancel this job. Returns True if the job was in a cancellable state."""
        with self._lock:
            if self._state in (JobState.QUEUED, JobState.SCHEDULED):
                self._cancel.set()
                self._state = JobState.CANCELLED
                self._event.set()  # unblock any waiter
                return True
            return False

    def _transition(self, new_state: JobState) -> None:
        with self._lock:
            self._state = new_state
            if new_state in (JobState.COMPLETE, JobState.FAILED,
                             JobState.CANCELLED, JobState.TIMED_OUT):
                self._event.set()

    def wait(self, timeout: Optional[float] = None) -> JobState:
        """Block until the job reaches a terminal state."""
        self._event.wait(timeout=timeout or self.timeout_s)
        return self._state


@dataclass(order=True)
class _HeapEntry:
    effective_priority: int
    seq:                int
    job_id:             str   = field(compare=False)
    enqueued_at:        float = field(compare=False)


class JobScheduler:
    """
    Priority queue with starvation prevention, cancellation, timeout, and
    per-user active-job limits.

    Usage:
        sched = JobScheduler(max_concurrent=1)
        job   = sched.submit(priority=0, user_id="alice")
        sched.start_job(job.job_id)
        # ... run GPU work, checking job.is_cancelled() periodically ...
        sched.finish_job(job.job_id, success=True)

    Per-user limits:
        sched.submit(priority=0, user_id="alice")   # ok (first job)
        sched.submit(priority=0, user_id="alice")   # ok (second job)
        sched.submit(priority=0, user_id="alice")   # raises TooManyJobsError
    """

    def __init__(self, max_concurrent: int = 1, max_per_user: int = MAX_ACTIVE_JOBS_PER_USER):
        self._max         = max_concurrent
        self._max_per_user = max_per_user
        self._active      = 0
        self._seq         = 0
        self._heap:    list[_HeapEntry]          = []
        self._jobs:    dict[str, Job]            = {}
        self._events:  dict[str, threading.Event] = {}   # job_id → slot-granted event
        self._per_user: dict[str, int]           = defaultdict(int)
        self._lock     = threading.Lock()
        self._start_starvation_monitor()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def submit(
        self,
        priority:  int   = 0,
        job_id:    Optional[str] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        user_id:   str   = "",
    ) -> Job:
        """
        Add a job to the queue.  Returns immediately with the Job object.
        Call job.wait() to block until a slot is granted.

        Raises TooManyJobsError if user_id already has max_per_user active jobs.
        """
        if job_id is None:
            job_id = str(uuid.uuid4())[:12]

        job   = Job(job_id=job_id, priority=priority, timeout_s=timeout_s, user_id=user_id)
        event = threading.Event()

        with self._lock:
            # Per-user quota check
            if user_id and self._max_per_user > 0:
                active_for_user = self._per_user[user_id]
                if active_for_user >= self._max_per_user:
                    raise TooManyJobsError(
                        f"User '{user_id}' already has {active_for_user} active "
                        f"job(s) (limit: {self._max_per_user}). "
                        f"Wait for a job to complete before submitting more."
                    )
                self._per_user[user_id] += 1

            self._jobs[job_id]   = job
            self._events[job_id] = event
            entry = _HeapEntry(
                effective_priority=priority,
                seq=self._next_seq(),
                job_id=job_id,
                enqueued_at=time.monotonic(),
            )
            heapq.heappush(self._heap, entry)
            self._try_dispatch()

        return job

    def acquire(
        self,
        priority:  int   = 0,
        job_id:    Optional[str] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        user_id:   str   = "",
    ) -> Job:
        """
        Submit and block until a slot is granted (or timeout/cancellation).
        Convenience wrapper for synchronous callers.

        Raises TooManyJobsError (from submit) if the per-user limit is exceeded.
        """
        job = self.submit(priority=priority, job_id=job_id, timeout_s=timeout_s, user_id=user_id)
        granted = self._events[job.job_id].wait(timeout=timeout_s)
        if not granted:
            job._transition(JobState.TIMED_OUT)
            with self._lock:
                self._remove_from_heap(job.job_id)
                if job.user_id:
                    self._per_user[job.user_id] = max(0, self._per_user[job.user_id] - 1)
            log.warning("[scheduler] Job %s timed out waiting for a slot.", job.job_id)
        return job

    def start_job(self, job_id: str) -> None:
        """Mark a job as actively running (after slot granted)."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job:
            job._transition(JobState.RUNNING)

    def finish_job(self, job_id: str, success: bool = True) -> None:
        """Release the slot and mark the job terminal."""
        with self._lock:
            job = self._jobs.pop(job_id, None)
            self._events.pop(job_id, None)
            if job and job.user_id:
                self._per_user[job.user_id] = max(0, self._per_user[job.user_id] - 1)
            self._active = max(0, self._active - 1)
            self._try_dispatch()
        if job:
            job._transition(JobState.COMPLETE if success else JobState.FAILED)

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued or scheduled job."""
        with self._lock:
            job = self._jobs.get(job_id)
        if job:
            cancelled = job.cancel()
            if cancelled:
                with self._lock:
                    if job.user_id:
                        self._per_user[job.user_id] = max(0, self._per_user[job.user_id] - 1)
                    self._jobs.pop(job_id, None)
                    self._events.pop(job_id, None)
                log.info("[scheduler] Job %s cancelled.", job_id)
            return cancelled
        return False

    def _try_dispatch(self) -> None:
        """Dispatch the highest-priority waiting job. Must hold self._lock."""
        while self._heap and self._active < self._max:
            entry = heapq.heappop(self._heap)
            job   = self._jobs.get(entry.job_id)
            if job is None or job.is_cancelled():
                continue  # already cancelled — skip
            self._active += 1
            job._transition(JobState.SCHEDULED)
            event = self._events.get(entry.job_id)
            if event:
                event.set()

    def _remove_from_heap(self, job_id: str) -> None:
        """Remove a job from the heap (e.g. on timeout). Must hold self._lock."""
        self._heap = [e for e in self._heap if e.job_id != job_id]
        heapq.heapify(self._heap)
        self._jobs.pop(job_id, None)
        self._events.pop(job_id, None)

    def _promote_starved(self) -> None:
        """Temporarily elevate long-waiting low-priority jobs to priority 0."""
        now = time.monotonic()
        with self._lock:
            for entry in self._heap:
                waited = now - entry.enqueued_at
                if waited > MAX_STARVATION_S and entry.effective_priority > 0:
                    entry.effective_priority = 0
                    log.debug(
                        "[scheduler] Job %s promoted (waited %.1fs).",
                        entry.job_id, waited,
                    )
            heapq.heapify(self._heap)

    def _start_starvation_monitor(self) -> None:
        def _loop():
            while True:
                time.sleep(5)
                try:
                    self._promote_starved()
                except Exception as exc:
                    log.debug("[scheduler] Starvation monitor error: %s", exc)

        t = threading.Thread(target=_loop, daemon=True, name="scheduler-starvation")
        t.start()

    def stats(self) -> dict:
        with self._lock:
            return {
                "active":    self._active,
                "waiting":   len(self._heap),
                "max":       self._max,
                "per_user":  dict(self._per_user),
            }

    class _Slot:
        """Context manager that handles start/finish automatically."""
        def __init__(self, sched: "JobScheduler", job: Job):
            self._sched = sched
            self._job   = job

        def __enter__(self) -> Job:
            self._sched.start_job(self._job.job_id)
            return self._job

        def __exit__(self, exc_type, *_):
            self._sched.finish_job(self._job.job_id, success=(exc_type is None))

    def slot(
        self,
        priority:  int   = 0,
        job_id:    Optional[str] = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        user_id:   str   = "",
    ) -> "_Slot":
        """
        Context manager: acquires slot, starts job, finishes on exit.
        Raises TooManyJobsError if the per-user limit is exceeded.

        with scheduler.slot(priority=0, job_id="user-42", user_id="alice") as job:
            if job.is_cancelled():
                return
            run_sdxl(...)
        """
        job = self.acquire(priority=priority, job_id=job_id, timeout_s=timeout_s, user_id=user_id)
        return self._Slot(self, job)


# Default singleton — 1 GPU slot
scheduler = JobScheduler(max_concurrent=1)
