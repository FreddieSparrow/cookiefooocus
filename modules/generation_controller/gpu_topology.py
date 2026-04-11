"""
Cookie-Fooocus — GPU Topology Layer
────────────────────────────────────────────────────────────────────────────────
Multi-device detection and job routing.

Detects all CUDA / Metal devices and maintains a per-GPU capacity model used
by the VRAMGovernor and JobScheduler to route jobs to the least-loaded device
instead of stacking onto a single GPU.

Per-GPU capacity model tracks:
  - total / free VRAM
  - active job count
  - throughput score (EWA, updated after each completed job)

Routing:
  least_loaded()        — lowest utilisation, tie-breaks by free VRAM
  device_for_job(vram)  — least-loaded device that can fit the job

Fallback:
  If only one device exists, routing is a no-op.
  If a GPU saturates, jobs fall back to the next device with sufficient VRAM.

Provided by CookieHostUK — coded with Claude AI assistance.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger("cookiefooocus.gpu_topology")


# ═══════════════════════════════════════════════════════════════════════════════
#  Per-device model
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class GPUDevice:
    """Runtime model for a single compute device."""
    index:            int
    name:             str
    total_vram_gb:    float
    backend:          str        # "cuda" | "mps" | "cpu"

    # Mutable runtime state — protected by GPUTopology._lock
    free_vram_gb:     float = 0.0
    active_jobs:      int   = 0
    throughput_score: float = 1.0   # relative; jobs/minute EWA estimate

    def utilisation(self) -> float:
        """0.0 = idle, 1.0 = VRAM fully consumed."""
        if self.total_vram_gb <= 0:
            return 1.0
        return max(0.0, 1.0 - self.free_vram_gb / self.total_vram_gb)


# ═══════════════════════════════════════════════════════════════════════════════
#  Topology
# ═══════════════════════════════════════════════════════════════════════════════

class GPUTopology:
    """
    Detects all available CUDA / Metal devices and maintains a per-GPU
    capacity model used for job routing.

    Usage:
        topology = gpu_topology          # module-level singleton
        device   = topology.least_loaded()
        topology.mark_job_start(device.index, vram_required_gb=4.0)
        # ... generation runs ...
        topology.mark_job_done(device.index, actual_vram_gb=3.8, elapsed_s=12.0)

    Multi-GPU example:
        device = topology.device_for_job(required_vram_gb=5.0)
        if device is None:
            raise RuntimeError("No GPU has sufficient VRAM")
        with torch.cuda.device(device.index):
            run_sdxl(...)
    """

    def __init__(self) -> None:
        self._lock:     threading.Lock = threading.Lock()
        self._devices:  List[GPUDevice] = []
        self._detected: bool = False

    # ── Detection ──────────────────────────────────────────────────────────────

    def detect(self) -> List[GPUDevice]:
        """Detect all compute devices. Safe to call multiple times — runs once."""
        with self._lock:
            if self._detected:
                return list(self._devices)
            self._devices  = self._do_detect()
            self._detected = True

        log.info(
            "[gpu_topology] Detected %d device(s): %s",
            len(self._devices),
            [f"{d.name} ({d.backend}, {d.total_vram_gb:.1f}GB)" for d in self._devices],
        )
        return list(self._devices)

    def _do_detect(self) -> List[GPUDevice]:
        devices: List[GPUDevice] = []

        try:
            import torch

            # CUDA — enumerate all physical GPUs
            if torch.cuda.is_available():
                for i in range(torch.cuda.device_count()):
                    props    = torch.cuda.get_device_properties(i)
                    total_gb = props.total_memory / 1024 ** 3
                    try:
                        free_bytes, _ = torch.cuda.mem_get_info(i)
                        free_gb = free_bytes / 1024 ** 3
                    except Exception:
                        free_gb = total_gb * 0.9
                    devices.append(GPUDevice(
                        index=i,
                        name=props.name,
                        total_vram_gb=round(total_gb, 2),
                        free_vram_gb=round(free_gb, 2),
                        backend="cuda",
                    ))

            # MPS — Apple Silicon unified memory
            elif getattr(getattr(torch, "backends", None), "mps", None) and torch.backends.mps.is_available():
                try:
                    import psutil
                    total_gb = psutil.virtual_memory().total    / 1024 ** 3
                    free_gb  = psutil.virtual_memory().available / 1024 ** 3
                except ImportError:
                    total_gb, free_gb = 16.0, 8.0

                devices.append(GPUDevice(
                    index=0,
                    name="Apple Silicon MPS",
                    total_vram_gb=round(total_gb, 2),
                    free_vram_gb=round(free_gb, 2),
                    backend="mps",
                ))

        except ImportError:
            pass

        # CPU fallback
        if not devices:
            try:
                import psutil
                total_gb = psutil.virtual_memory().total    / 1024 ** 3
                free_gb  = psutil.virtual_memory().available / 1024 ** 3
            except ImportError:
                total_gb, free_gb = 16.0, 8.0

            devices.append(GPUDevice(
                index=0,
                name="CPU",
                total_vram_gb=round(total_gb, 2),
                free_vram_gb=round(free_gb, 2),
                backend="cpu",
            ))

        return devices

    # ── Routing ────────────────────────────────────────────────────────────────

    def devices(self) -> List[GPUDevice]:
        """Return current device list. Detects on first call."""
        if not self._detected:
            self.detect()
        with self._lock:
            return list(self._devices)

    def least_loaded(self) -> GPUDevice:
        """
        Return the device with the lowest utilisation.
        Tie-breaks by highest free VRAM, then lowest active job count.
        """
        devs = self.devices()
        if not devs:
            raise RuntimeError("GPUTopology: no compute devices available")
        return min(devs, key=lambda d: (d.utilisation(), d.active_jobs, -d.free_vram_gb))

    def device_for_job(self, required_vram_gb: float) -> Optional[GPUDevice]:
        """
        Return the least-loaded device that can accommodate a job requiring
        at least `required_vram_gb` of free VRAM.  Returns None if no device
        can fit the job.
        """
        candidates = [d for d in self.devices() if d.free_vram_gb >= required_vram_gb]
        if not candidates:
            return None
        return min(candidates, key=lambda d: (d.active_jobs, d.utilisation()))

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def mark_job_start(self, device_index: int, vram_required_gb: float) -> None:
        """
        Reserve VRAM and increment active job count for the given device.
        Call this immediately before submitting a job to that GPU.
        """
        with self._lock:
            for dev in self._devices:
                if dev.index == device_index:
                    dev.active_jobs  += 1
                    dev.free_vram_gb  = max(0.0, dev.free_vram_gb - vram_required_gb)
                    return
        log.warning("[gpu_topology] mark_job_start: unknown device index %d", device_index)

    def mark_job_done(
        self,
        device_index: int,
        actual_vram_gb: float = 0.0,
        elapsed_s:     float  = 0.0,
    ) -> None:
        """
        Release a job slot and refresh free VRAM from a live reading.
        Also updates the throughput score (EWA: jobs per minute).
        """
        with self._lock:
            for dev in self._devices:
                if dev.index == device_index:
                    dev.active_jobs = max(0, dev.active_jobs - 1)
                    # Refresh free VRAM from hardware
                    live = self._live_free_vram(dev)
                    if live > 0:
                        dev.free_vram_gb = live
                    # Update throughput EWA
                    if elapsed_s > 0:
                        jobs_per_min     = 60.0 / elapsed_s
                        dev.throughput_score = 0.8 * dev.throughput_score + 0.2 * jobs_per_min
                    return

    def refresh_free_vram(self) -> None:
        """Refresh free VRAM readings from all devices (useful after model loads)."""
        with self._lock:
            for dev in self._devices:
                live = self._live_free_vram(dev)
                if live > 0:
                    dev.free_vram_gb = live

    def _live_free_vram(self, dev: GPUDevice) -> float:
        """Read live free VRAM for a device. Returns 0.0 on failure."""
        try:
            import torch
            if dev.backend == "cuda":
                free_bytes, _ = torch.cuda.mem_get_info(dev.index)
                return round(free_bytes / 1024 ** 3, 2)
            elif dev.backend == "mps":
                import psutil
                return round(psutil.virtual_memory().available / 1024 ** 3, 2)
        except Exception:
            pass
        return 0.0

    # ── Diagnostics ────────────────────────────────────────────────────────────

    def summary(self) -> List[dict]:
        """Return a JSON-safe summary of all devices for dashboards / logs."""
        return [
            {
                "index":             d.index,
                "name":              d.name,
                "backend":           d.backend,
                "total_vram_gb":     d.total_vram_gb,
                "free_vram_gb":      round(d.free_vram_gb, 2),
                "active_jobs":       d.active_jobs,
                "utilisation_pct":   round(d.utilisation() * 100, 1),
                "throughput_score":  round(d.throughput_score, 2),
            }
            for d in self.devices()
        ]


# Module-level singleton — detected lazily on first access.
gpu_topology = GPUTopology()
