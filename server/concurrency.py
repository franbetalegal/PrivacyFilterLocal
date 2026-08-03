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
