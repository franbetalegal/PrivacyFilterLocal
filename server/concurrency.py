"""Shared CPU budget: how many worker processes/threads this host may use.

A single anonymization job can run on very different machines — a developer's
laptop actively used for other things, or a shared office server dedicated to
this app. This module answers "how many workers may I spawn right now?" so
CPU-bound stages (PDF page extraction/OCR, and torch's own intra-op
parallelism) can use the available cores without starving the host machine.

The policy is simple: always reserve a fixed number of cores for the OS and
whatever else is running there, and never use fewer than 1 worker even on a
1-2 core machine.

Configuration via environment variables:
    - ``PF_RESERVED_CORES``  Cores to keep free (default: 2).
    - ``PF_MAX_WORKERS``     Optional hard cap on top of the reserved-core
      budget, e.g. to deliberately under-use a big shared server.
"""
from __future__ import annotations

import os

_DEFAULT_RESERVED_CORES = 2


def worker_count() -> int:
    """Return how many worker processes/threads this host may use right now.

    Always at least 1. Recomputed on every call (cheap) so tests can
    monkeypatch ``os.cpu_count`` or the env vars without needing a restart.
    """
    total = os.cpu_count() or 1
    budget = max(1, total - _reserved_cores())
    cap = _max_workers_cap()
    if cap is not None:
        budget = min(budget, cap)
    return budget


# Resident cost of one OCR worker process. Each one is a spawn that re-imports
# the app; measured at a few hundred MB, rounded up so the estimate errs on the
# side of fewer workers.
_OCR_WORKER_BYTES = 600 * 1024 * 1024
# Memory left untouched for the OS and whatever else the user has open.
_MEMORY_HEADROOM_BYTES = 1536 * 1024 * 1024


def available_memory_bytes() -> int | None:
    """Physical memory available right now, or None when it cannot be read.

    Standard library only: Windows through ``GlobalMemoryStatusEx``, Linux
    through ``/proc/meminfo``. macOS exposes no cheap "available" figure, so it
    reports None and the caller keeps the CPU-based budget.
    """
    import sys

    if sys.platform == "win32":
        import ctypes

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return int(status.ullAvailPhys)
        except Exception:  # noqa: BLE001
            return None
        return None
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def ocr_worker_count() -> int:
    """How many OCR worker processes fit in both the CPU and memory budgets.

    The CPU budget alone sized the pool for the cores, and on an 8 GB laptop
    with the models loaded six re-imported workers did not fit.
    """
    budget = worker_count()
    available = available_memory_bytes()
    if available is None:
        return budget
    by_memory = (available - _MEMORY_HEADROOM_BYTES) // _OCR_WORKER_BYTES
    return max(1, min(budget, int(by_memory)))


def _reserved_cores() -> int:
    raw = os.environ.get("PF_RESERVED_CORES")
    if raw is None:
        return _DEFAULT_RESERVED_CORES
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_RESERVED_CORES
    return max(0, value)


def _max_workers_cap() -> int | None:
    raw = os.environ.get("PF_MAX_WORKERS")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None
