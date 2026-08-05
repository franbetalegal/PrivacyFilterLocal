"""Pick the best inference device for opf.

Order of preference:
    1. ``PF_DEVICE`` env var (``cpu``, ``cuda``, ``mps``) — explicit override,
       for benchmarking or for forcing a specific backend.
    2. NVIDIA CUDA when available — the fastest option by a wide margin, and
       the only one where opf's Triton MoE kernels work.
    3. Plain CPU — with :mod:`server.moe_fast` installed this is fast enough
       that it beats MPS on this model (see below).

MPS (Apple Metal) is deliberately **not** auto-selected. Measured on an M1
with this checkpoint, a 10-page document took ~145 s on MPS versus ~90 s on
unoptimised CPU: opf's Triton MoE kernels are CUDA-only, so MPS falls back to
the same pure-PyTorch path as CPU but pays Metal kernel-launch overhead on
top. With :mod:`server.moe_fast` the CPU path is roughly 29× faster in the MoE
block, widening the gap further. Set ``PF_DEVICE=mps`` to opt in anyway.

On the Dell R630 target (no GPU) this returns ``cpu``. On a box with an
NVIDIA card and a CUDA-enabled torch build, ``cuda``.
"""
from __future__ import annotations

import logging
import os
from typing import Literal

logger = logging.getLogger("privacy_filter.device")

Device = Literal["cpu", "cuda", "mps"]
_VALID: tuple[Device, ...] = ("cpu", "cuda", "mps")


def _override() -> Device | None:
    """Return the value of ``PF_DEVICE`` if it's a recognised device name."""
    raw = os.environ.get("PF_DEVICE", "").strip().lower()
    if raw in _VALID:
        return raw  # type: ignore[return-value]
    if raw:
        logger.warning(
            "PF_DEVICE=%r is not one of %s — falling back to auto-detect.",
            raw, _VALID,
        )
    return None


def detect_device() -> Device:
    """Return the best inference device available on this host right now.

    MPS is never auto-selected — it benchmarks slower than the optimised CPU
    path for this model (see the module docstring). Request it explicitly with
    ``PF_DEVICE=mps`` if you want to measure it yourself.
    """
    override = _override()
    if override is not None:
        return override
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def mps_available() -> bool:
    """True if Apple Metal is usable — reported in the UI, not auto-selected."""
    try:
        import torch
    except ImportError:
        return False
    backend = getattr(torch.backends, "mps", None)
    return bool(
        backend is not None and backend.is_available() and backend.is_built()
    )


def describe(device: Device) -> str:
    """Human-readable label for the device, shown in logs and the UI."""
    if device == "cuda":
        try:
            import torch
            name = torch.cuda.get_device_name(0)
            return f"cuda ({name})"
        except Exception:  # noqa: BLE001
            return "cuda"
    if device == "mps":
        return "mps (Apple Silicon GPU via Metal)"
    return "cpu"
