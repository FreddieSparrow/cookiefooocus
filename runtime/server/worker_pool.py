"""
runtime.server.worker_pool — Server-mode GPU worker management
===============================================================
Manages the pool of worker threads that pull jobs from the shared
priority queue and execute them on GPU.

Rules:
    - Global VRAM cap enforced before job dispatch (hard rejection, no guessing)
    - Per-job VRAM prediction happens before queue entry (in api.py)
    - No uncontrolled filesystem writes
    - No direct git operations in runtime thread
    - Worker count is configured in config/server.json

Architecture:
    api.py
      ↓ submit job (after VRAM pre-check + tenant capacity check)
    core.scheduler (priority queue)
      ↓ acquire slot
    WorkerPool (thread pool pulling from scheduler)
      ↓ run job on GPU
    core.pipeline
      ↓ result
    api.py (response)

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

log = logging.getLogger("cookiefooocus.server.workers")


class WorkerPool:
    """
    Thread pool that consumes jobs from the core scheduler and
    runs them on GPU within per-tenant VRAM budgets.
    """

    def __init__(self, max_workers: int = 2, global_vram_cap_mb: int = 0) -> None:
        """
        Args:
            max_workers:        Number of concurrent GPU workers.
            global_vram_cap_mb: Hard VRAM ceiling across all tenants.
                                0 means use full available VRAM.
        """
        self._max_workers     = max_workers
        self._vram_cap_mb     = global_vram_cap_mb
        self._executor: Optional[ThreadPoolExecutor] = None
        self._active_vram_mb  = 0
        self._lock            = threading.Lock()
        self._running         = False

    def start(self) -> None:
        if self._running:
            return
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="cf-worker",
        )
        self._running = True
        log.info(
            "[workers] Pool started — %d worker(s), VRAM cap: %s MB",
            self._max_workers,
            self._vram_cap_mb or "unlimited",
        )

    def stop(self, wait: bool = True) -> None:
        if self._executor:
            self._executor.shutdown(wait=wait)
        self._running = False
        log.info("[workers] Pool stopped.")

    def can_accept(self, job_vram_mb: int) -> bool:
        """
        Check whether a new job fits within the global VRAM cap.
        Called before submitting to the queue — hard rejection here.
        """
        if self._vram_cap_mb == 0:
            return True
        with self._lock:
            return (self._active_vram_mb + job_vram_mb) <= self._vram_cap_mb

    def submit(
        self,
        fn: Callable,
        job_vram_mb: int,
        on_complete: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ) -> bool:
        """
        Submit a GPU job to the worker pool.
        Returns False if global VRAM cap would be exceeded (hard reject).
        """
        if not self._running or not self._executor:
            log.error("[workers] Pool is not running — cannot accept job.")
            return False

        if not self.can_accept(job_vram_mb):
            log.warning(
                "[workers] HARD REJECT — VRAM cap would be exceeded "
                "(active=%d MB, job=%d MB, cap=%d MB).",
                self._active_vram_mb, job_vram_mb, self._vram_cap_mb,
            )
            return False

        with self._lock:
            self._active_vram_mb += job_vram_mb

        def _run():
            try:
                result = fn()
                if on_complete:
                    on_complete(result)
            except Exception as exc:
                log.error("[workers] Job failed: %s", exc, exc_info=True)
                if on_error:
                    on_error(exc)
            finally:
                with self._lock:
                    self._active_vram_mb = max(0, self._active_vram_mb - job_vram_mb)

        self._executor.submit(_run)
        return True

    @property
    def stats(self) -> dict:
        return {
            "max_workers":    self._max_workers,
            "vram_cap_mb":    self._vram_cap_mb,
            "active_vram_mb": self._active_vram_mb,
            "running":        self._running,
        }


# Singleton — configured and started in api.py on server startup
pool: Optional[WorkerPool] = None


def get_pool() -> WorkerPool:
    if pool is None:
        raise RuntimeError("[workers] WorkerPool not initialised — call api.start() first.")
    return pool
