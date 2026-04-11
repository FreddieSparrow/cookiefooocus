"""
Cookie-Fooocus — Worker Node Protocol (Distributed Queue)
────────────────────────────────────────────────────────────────────────────────
Enables multi-machine rendering farm support by splitting the scheduler into:

  Control plane  — job assignment, lease management, heartbeat monitoring
  Execution plane — worker nodes that do the actual GPU work

Architecture:

    ControlPlane ──→  WorkerNode(s)
         ↑                   ↓
    submit(job)         heartbeat()
    assign(job)         execute(job)
    lease_renew()       result_callback()

Transports:
  LocalWorkerNode   — in-process (default, no network required)
  HttpWorkerNode    — HTTP POST JSON (home lab / simple cluster)
  Redis/NATS        — can replace the transport layer without changing the API

Lease system:
  Each assigned job is given a lease with a TTL.  The worker must call
  renew_lease() periodically or the control plane reclaims the job and
  re-queues it.  This prevents hung workers from blocking the queue.

Usage:
    # Local (default — distributed mode disabled)
    from modules.generation_controller.worker_protocol import control_plane
    control_plane.register_worker(LocalWorkerNode())
    control_plane.start()

    # Remote worker
    worker = HttpWorkerNode("http://render-node-2:7866")
    control_plane.register_worker(worker)

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

log = logging.getLogger("cookiefooocus.worker_protocol")


# ═══════════════════════════════════════════════════════════════════════════════
#  Shared data types
# ═══════════════════════════════════════════════════════════════════════════════

class WorkerState(str, Enum):
    IDLE     = "idle"
    BUSY     = "busy"
    DRAINING = "draining"   # finishing current jobs, accepting no new ones
    OFFLINE  = "offline"


@dataclass
class WorkerInfo:
    worker_id:       str
    address:         str            # "local" | "http://host:port"
    gpu_indices:     List[int]
    max_concurrent:  int
    state:           WorkerState = WorkerState.IDLE
    last_heartbeat:  float       = field(default_factory=time.monotonic)
    active_jobs:     int         = 0
    total_completed: int         = 0


@dataclass
class JobLease:
    """Exclusive execution rights granted to a worker for a single job."""
    lease_id:        str
    job_id:          str
    worker_id:       str
    granted_at:      float
    expires_at:      float
    lease_duration_s: float = 60.0


# ═══════════════════════════════════════════════════════════════════════════════
#  Worker interface
# ═══════════════════════════════════════════════════════════════════════════════

class WorkerNode:
    """
    Abstract worker node.  Override execute() for custom backends.
    The default LocalWorkerNode delegates to the existing generation pipeline.
    """

    def __init__(
        self,
        worker_id:   Optional[str]       = None,
        gpu_indices: Optional[List[int]] = None,
    ) -> None:
        self.worker_id      = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.gpu_indices    = gpu_indices or [0]
        self.max_concurrent = len(self.gpu_indices)

    def execute(self, job_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a generation job.
        Returns {"status": "complete"|"failed", "result": ..., "error": ...}
        """
        raise NotImplementedError

    def heartbeat(self) -> Dict[str, Any]:
        """Return current worker state for control plane monitoring."""
        return {
            "worker_id": self.worker_id,
            "state":     WorkerState.IDLE.value,
            "ts":        time.time(),
        }


class LocalWorkerNode(WorkerNode):
    """
    In-process worker.  Wraps the existing generation pipeline.
    Used when distributed mode is not enabled (the default).
    """

    def execute(self, job_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from modules.generation_controller import controller
            result = controller.run_job(job_id=job_id, params=params)
            return {"status": "complete", "result": result}
        except Exception as exc:
            log.exception("[local_worker] Job %s failed", job_id)
            return {"status": "failed", "error": str(exc)}


class HttpWorkerNode(WorkerNode):
    """
    Remote worker reachable over HTTP.
    The remote node must expose POST /cf/worker/execute accepting JSON.
    """

    def __init__(self, address: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.address = address.rstrip("/")

    def execute(self, job_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        import urllib.request, urllib.error
        payload = json.dumps({"job_id": job_id, "params": params}).encode()
        try:
            req = urllib.request.Request(
                f"{self.address}/cf/worker/execute",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=600) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            log.error("[http_worker] Job %s failed: %s", job_id, exc)
            return {"status": "failed", "error": str(exc)}

    def heartbeat(self) -> Dict[str, Any]:
        import urllib.request, urllib.error
        try:
            with urllib.request.urlopen(f"{self.address}/cf/worker/heartbeat", timeout=5) as resp:
                return json.loads(resp.read())
        except Exception:
            return {"worker_id": self.worker_id, "state": WorkerState.OFFLINE.value, "ts": time.time()}


# ═══════════════════════════════════════════════════════════════════════════════
#  Control plane
# ═══════════════════════════════════════════════════════════════════════════════

class ControlPlane:
    """
    Control plane: assigns pending jobs to worker nodes, manages leases,
    monitors heartbeats, and reclaims expired leases.

    Usage:
        plane = ControlPlane()
        plane.register_worker(LocalWorkerNode())
        plane.start()
        job_id = plane.submit("my-job-001", params={"prompt": "..."}, priority=0)
        result = plane.get_result(job_id)
    """

    HEARTBEAT_TIMEOUT_S = 30.0    # worker marked OFFLINE after this silence
    LEASE_DURATION_S    = 60.0    # job lease TTL — worker must renew or job is reclaimed
    DISPATCH_INTERVAL_S = 0.25    # dispatch loop tick

    def __init__(self) -> None:
        self._lock           = threading.Lock()
        self._workers:      Dict[str, WorkerNode]   = {}
        self._worker_info:  Dict[str, WorkerInfo]   = {}
        self._leases:       Dict[str, JobLease]     = {}   # lease_id → lease
        self._job_worker:   Dict[str, str]          = {}   # job_id → worker_id
        self._job_params:   Dict[str, Any]          = {}   # job_id → params (for reclaim)
        self._pending:      List[tuple]             = []   # (priority, ts, job_id)
        self._results:      Dict[str, Any]          = {}
        self._running:      bool                    = False
        self._dispatch_thread:   Optional[threading.Thread] = None
        self._heartbeat_thread:  Optional[threading.Thread] = None

    # ── Worker registration ────────────────────────────────────────────────────

    def register_worker(self, worker: WorkerNode) -> None:
        with self._lock:
            self._workers[worker.worker_id] = worker
            self._worker_info[worker.worker_id] = WorkerInfo(
                worker_id=worker.worker_id,
                address=getattr(worker, "address", "local"),
                gpu_indices=worker.gpu_indices,
                max_concurrent=worker.max_concurrent,
            )
        log.info("[control_plane] Registered worker %s", worker.worker_id)

    def deregister_worker(self, worker_id: str) -> None:
        with self._lock:
            self._workers.pop(worker_id, None)
            info = self._worker_info.pop(worker_id, None)
            if info:
                info.state = WorkerState.OFFLINE
        log.info("[control_plane] Deregistered worker %s", worker_id)

    # ── Job submission ─────────────────────────────────────────────────────────

    def submit(
        self,
        job_id:   str,
        params:   Dict[str, Any],
        priority: int = 0,
    ) -> str:
        """Enqueue a job for execution. Returns job_id immediately."""
        with self._lock:
            self._job_params[job_id] = params
            self._pending.append((priority, time.monotonic(), job_id))
            self._pending.sort(key=lambda x: (x[0], x[1]))
        log.debug("[control_plane] Queued job %s (priority=%d)", job_id, priority)
        return job_id

    def get_result(self, job_id: str, timeout_s: float = 600.0) -> Optional[Dict]:
        """Block until a result is available or timeout. Returns None on timeout."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                if job_id in self._results:
                    return self._results.pop(job_id)
            time.sleep(0.1)
        return None

    # ── Lease management ──────────────────────────────────────────────────────

    def renew_lease(self, lease_id: str) -> bool:
        """Extend a job lease. Returns False if lease not found (already expired)."""
        with self._lock:
            lease = self._leases.get(lease_id)
            if not lease:
                return False
            lease.expires_at = time.monotonic() + lease.lease_duration_s
            return True

    def _grant_lease(self, job_id: str, worker_id: str) -> JobLease:
        lease = JobLease(
            lease_id=uuid.uuid4().hex,
            job_id=job_id,
            worker_id=worker_id,
            granted_at=time.monotonic(),
            expires_at=time.monotonic() + self.LEASE_DURATION_S,
            lease_duration_s=self.LEASE_DURATION_S,
        )
        self._leases[lease.lease_id]  = lease
        self._job_worker[job_id]      = worker_id
        return lease

    def _reclaim_expired_leases(self) -> None:
        now = time.monotonic()
        with self._lock:
            expired = [
                lid for lid, lease in self._leases.items()
                if now > lease.expires_at
            ]
            for lid in expired:
                lease = self._leases.pop(lid)
                self._job_worker.pop(lease.job_id, None)
                info  = self._worker_info.get(lease.worker_id)
                if info:
                    info.active_jobs = max(0, info.active_jobs - 1)
                # Re-queue with original params at priority 0
                params = self._job_params.get(lease.job_id)
                if params is not None:
                    self._pending.insert(0, (0, time.monotonic(), lease.job_id))
                log.warning(
                    "[control_plane] Lease %s expired for job %s on worker %s — reclaimed",
                    lid, lease.job_id, lease.worker_id,
                )

    # ── Heartbeat monitoring ───────────────────────────────────────────────────

    def record_heartbeat(self, worker_id: str) -> None:
        with self._lock:
            info = self._worker_info.get(worker_id)
            if info:
                info.last_heartbeat = time.monotonic()
                if info.state == WorkerState.OFFLINE:
                    info.state = WorkerState.IDLE
                    log.info("[control_plane] Worker %s came back online", worker_id)

    def _check_heartbeats(self) -> None:
        now = time.monotonic()
        with self._lock:
            for wid, info in self._worker_info.items():
                if info.state != WorkerState.OFFLINE:
                    age = now - info.last_heartbeat
                    if age > self.HEARTBEAT_TIMEOUT_S:
                        info.state = WorkerState.OFFLINE
                        log.warning(
                            "[control_plane] Worker %s timed out (%.1fs since heartbeat)",
                            wid, age,
                        )

    # ── Dispatch loop ─────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background dispatch and heartbeat threads."""
        if self._running:
            return
        self._running = True
        self._dispatch_thread = threading.Thread(
            target=self._dispatch_loop, daemon=True, name="cf-dispatch"
        )
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="cf-heartbeat"
        )
        self._dispatch_thread.start()
        self._heartbeat_thread.start()
        log.info("[control_plane] Started")

    def stop(self) -> None:
        """Stop background threads (jobs in flight will complete)."""
        self._running = False

    def _dispatch_loop(self) -> None:
        while self._running:
            try:
                self._dispatch_one()
            except Exception:
                log.exception("[control_plane] Dispatch error")
            time.sleep(self.DISPATCH_INTERVAL_S)

    def _heartbeat_loop(self) -> None:
        interval = self.HEARTBEAT_TIMEOUT_S / 3
        while self._running:
            try:
                self._check_heartbeats()
                self._reclaim_expired_leases()
            except Exception:
                log.exception("[control_plane] Heartbeat error")
            time.sleep(interval)

    def _dispatch_one(self) -> None:
        """Assign one pending job to an available worker."""
        with self._lock:
            if not self._pending:
                return
            available = [
                (wid, info)
                for wid, info in self._worker_info.items()
                if info.state == WorkerState.IDLE
                and info.active_jobs < info.max_concurrent
            ]
            if not available:
                return
            worker_id, worker_info = min(available, key=lambda x: x[1].active_jobs)
            priority, ts, job_id  = self._pending.pop(0)
            worker_info.active_jobs += 1
            lease = self._grant_lease(job_id, worker_id)

        worker = self._workers.get(worker_id)
        if worker is None:
            return
        params = self._job_params.get(job_id, {})

        def _run() -> None:
            start = time.monotonic()
            try:
                result = worker.execute(job_id, params)
            except Exception as exc:
                result = {"status": "failed", "error": str(exc)}
            finally:
                elapsed = time.monotonic() - start
                with self._lock:
                    info = self._worker_info.get(worker_id)
                    if info:
                        info.active_jobs     = max(0, info.active_jobs - 1)
                        info.total_completed += 1
                    self._leases.pop(lease.lease_id, None)
                    self._job_worker.pop(job_id, None)
                    self._results[job_id] = result
                log.debug(
                    "[control_plane] Job %s completed in %.1fs on worker %s",
                    job_id, elapsed, worker_id,
                )

        threading.Thread(target=_run, daemon=True, name=f"cf-worker-{job_id[:8]}").start()

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> Dict:
        with self._lock:
            return {
                "workers": [
                    {
                        "worker_id":            info.worker_id,
                        "address":              info.address,
                        "state":                info.state.value,
                        "active_jobs":          info.active_jobs,
                        "total_completed":      info.total_completed,
                        "last_heartbeat_age_s": round(time.monotonic() - info.last_heartbeat, 1),
                    }
                    for info in self._worker_info.values()
                ],
                "pending_jobs":  len(self._pending),
                "active_leases": len(self._leases),
            }


# ─────────────────────────────────────────────────────────────────────────────
# Default singleton — a single local worker is registered but NOT started.
# Call control_plane.start() explicitly in server mode or distributed setup.
# ─────────────────────────────────────────────────────────────────────────────
control_plane = ControlPlane()
_local_worker  = LocalWorkerNode(worker_id="local-0")
# Note: auto-start deferred to avoid side effects at import time.
