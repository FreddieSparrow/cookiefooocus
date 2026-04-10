"""
Cookie-Fooocus Hardware Capability Check
─────────────────────────────────────────
Determines whether the current machine meets the minimum requirements for
Ollama-based prompt expansion (which loads a full LLM into memory).

Requirements for Ollama to be enabled:
  • Apple Silicon Mac  — ≥ 32 GB unified memory
  • Windows / Linux PC — ≥ 26 GB system RAM  AND  ≥ 12 GB GPU VRAM

If requirements are not met, expansion falls back to the lightweight
GPT-2 local model (original Fooocus behaviour).

Provided by CookieHostUK — coded with Claude AI assistance.
"""

import logging
import platform
import subprocess

log = logging.getLogger("cookiefooocus.hardware_check")

# ── Thresholds ─────────────────────────────────────────────────────────────────
_APPLE_MIN_RAM_GB  = 32    # Apple Silicon unified memory minimum
_PC_MIN_RAM_GB     = 26    # Windows/Linux system RAM minimum
_PC_MIN_VRAM_GB    = 12    # Windows/Linux GPU VRAM minimum


def _get_total_ram_gb() -> float:
    """Return total system RAM in GB."""
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        pass
    # Fallback: platform-specific
    try:
        if platform.system() == "Darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
            return int(out.strip()) / (1024 ** 3)
        if platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / (1024 ** 2)  # kB → GB
        if platform.system() == "Windows":
            import ctypes
            status = ctypes.Structure
            class _MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength",                ctypes.c_ulong),
                    ("dwMemoryLoad",            ctypes.c_ulong),
                    ("ullTotalPhys",            ctypes.c_ulonglong),
                    ("ullAvailPhys",            ctypes.c_ulonglong),
                    ("ullTotalPageFile",        ctypes.c_ulonglong),
                    ("ullAvailPageFile",        ctypes.c_ulonglong),
                    ("ullTotalVirtual",         ctypes.c_ulonglong),
                    ("ullAvailVirtual",         ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(stat)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullTotalPhys / (1024 ** 3)
    except Exception as exc:
        log.debug("[hardware] RAM detection failed: %s", exc)
    return 0.0


def _get_vram_gb() -> float:
    """Return total GPU VRAM in GB (best-effort, 0.0 if unavailable)."""
    try:
        import torch
        if torch.cuda.is_available():
            return torch.cuda.get_device_properties(0).total_memory / (1024 ** 3)
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            # MPS unified memory — not a separate VRAM pool.
            # Return 0 here; Apple Silicon uses the unified memory check instead.
            return 0.0
    except Exception:
        pass
    return 0.0


def _is_apple_silicon() -> bool:
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def check_ollama_capable() -> tuple[bool, str]:
    """
    Returns (capable: bool, reason: str).

    capable=True   → machine meets requirements, Ollama may be used.
    capable=False  → machine does not meet requirements, use GPT-2 fallback.

    The reason string is a human-readable explanation suitable for logging.
    """
    ram_gb  = _get_total_ram_gb()
    vram_gb = _get_vram_gb()

    if _is_apple_silicon():
        if ram_gb >= _APPLE_MIN_RAM_GB:
            return True, (
                f"Apple Silicon detected with {ram_gb:.1f} GB unified memory "
                f"(≥ {_APPLE_MIN_RAM_GB} GB required) — Ollama enabled."
            )
        return False, (
            f"Apple Silicon detected but only {ram_gb:.1f} GB unified memory "
            f"(≥ {_APPLE_MIN_RAM_GB} GB required). "
            "Falling back to GPT-2 prompt expansion."
        )

    # Windows / Linux PC
    if ram_gb >= _PC_MIN_RAM_GB and vram_gb >= _PC_MIN_VRAM_GB:
        return True, (
            f"PC: {ram_gb:.1f} GB RAM / {vram_gb:.1f} GB VRAM — Ollama enabled."
        )

    reasons = []
    if ram_gb < _PC_MIN_RAM_GB:
        reasons.append(f"{ram_gb:.1f} GB RAM (need ≥ {_PC_MIN_RAM_GB} GB)")
    if vram_gb < _PC_MIN_VRAM_GB:
        reasons.append(f"{vram_gb:.1f} GB VRAM (need ≥ {_PC_MIN_VRAM_GB} GB)")
    return False, (
        "Hardware requirements not met for Ollama: "
        + ", ".join(reasons)
        + ". Falling back to GPT-2 prompt expansion."
    )
