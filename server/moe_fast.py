"""Faster CPU path for opf's mixture-of-experts feed-forward block.

Why this exists
---------------
``opf``'s MoE block has two implementations (see
``privacy-filter/opf/_model/model.py``): a Triton-kernel path used on GPU, and
a pure-PyTorch fallback used on CPU. The fallback gathers a *private copy of
the expert weight matrices for every token*::

    mlp1_weight = self.mlp1_weight[expert_indices_chunk, ...]   # [B, K, D, 2F]
    mlp1_weight = mlp1_weight.float()

At this checkpoint's dimensions (128 experts, top-4, d_model=640,
d_ff=640) that materialises ~419 MB of fp32 weights per 32-token chunk. A
5000-token document needs ~157 chunks per layer across 8 layers, so the block
streams hundreds of gigabytes through memory to do ~30 GFLOP of actual work.
Measured on an M1: ~70 s of the ~90 s total inference time.

The standard fix for MoE on CPU is to invert the loop: group tokens by the
expert they routed to, then run one matmul per expert with the expert's weights
used **in place** (no copy). Each expert's weights are cast to fp32 once per
layer instead of once per token.

Measured at this checkpoint's dimensions (1000 tokens, 6 threads, M1):

    gather + fp32 cast (opf today) .... 1746 ms   (~70 s per document)
    expert loop, cast per expert ......   61 ms   (~2.4 s per document)

...a ~29× speedup for the block, with no additional resident memory (the
weights stay bf16; only one expert's slice is fp32 at a time).

Hardware portability
--------------------
That measurement is **ARM-specific**, and the reason matters: on Apple Silicon
PyTorch has no fast bf16 GEMM, so casting to fp32 wins. A recent Intel Xeon
(AVX-512-BF16, or AMX from Sapphire Rapids on) *does* have native bf16 matmul,
and there the upstream bf16 path can be the faster one. Hard-coding either
choice would make the app slower on half the machines it runs on.

So the strategy is **measured on the host, not assumed**: :func:`install`
runs a sub-second micro-benchmark of both implementations at this checkpoint's
dimensions and keeps the winner. The verdict is cached under the checkpoint
directory keyed by platform + CPU + torch version, so the cost is paid once
per machine rather than once per start.

CUDA is deliberately out of scope — there the upstream Triton kernels are the
fast path, and :mod:`server.inference` skips this module entirely.

Scope and safety
----------------
This module *monkey-patches* ``MLPBlock.forward`` rather than editing the
vendored model, so ``privacy-filter/`` stays byte-identical to upstream and a
future model/package update can't silently half-apply our change.
:func:`install` verifies the attributes it depends on before patching and
refuses to patch when anything looks unfamiliar, so an upstream refactor
degrades to the original (slow but correct) path instead of breaking.

Configuration:
    ``PF_FAST_MOE=auto`` (default) benchmark and pick the winner.
    ``PF_FAST_MOE=1``    force the expert-loop path (skip the benchmark).
    ``PF_FAST_MOE=0``    force the upstream path.
    ``PF_MOE_RECALIBRATE=1`` ignore the cached verdict and measure again.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger("privacy_filter.moe_fast")

_installed = False

# Attributes ``_fast_forward`` reads off the MLPBlock instance. If a future
# opf refactor renames any of them we skip patching rather than crash.
_REQUIRED_ATTRS = (
    "norm", "gate", "experts_per_token", "swiglu_limit", "packed_geglu",
    "world_size", "mlp1_weight", "mlp1_bias", "mlp2_weight", "mlp2_bias",
)


def _fast_forward(self, x):
    """Expert-grouped MoE forward. Mirrors ``MLPBlock.forward`` semantics.

    Equivalent to the upstream non-Triton path: same RMSNorm, same gating
    (top-k → softmax → ``/k``, cancelled by the ``*k`` at the end), same
    SwiGLU (upstream's own function, so clamping and the packed-GeGLU variant
    behave identically), same per-(token, expert) biases applied *before* the
    gate-weighted sum, and the same residual add.
    """
    import torch
    import torch.nn.functional as F
    from opf._model.model import swiglu

    if x.dim() != 3:
        raise ValueError("MLPBlock expects batched 3D tensor input")

    # Distributed sharding splits the expert weights across ranks and needs an
    # all_reduce; that path is out of scope here, so defer to upstream.
    if getattr(self, "world_size", 1) > 1:
        return _original_forward(self, x)

    batch_shape = x.shape[:-1]
    t = self.norm(x).reshape(-1, x.shape[-1])

    g = F.linear(t.float(), self.gate.weight.float(), self.gate.bias.float())
    experts = torch.topk(g, k=self.experts_per_token, dim=-1, sorted=True)
    expert_weights = F.softmax(experts.values, dim=1) / self.experts_per_token
    expert_indices = experts.indices

    n_tokens, d_model = t.shape
    k = self.experts_per_token
    t_f32 = t.float()

    # Flatten the (token, expert) assignments and sort by expert so every
    # expert's rows are contiguous — that's what lets us do one matmul each.
    flat_expert = expert_indices.reshape(-1)
    flat_token = torch.arange(
        n_tokens, device=t.device
    ).repeat_interleave(k)
    flat_weight = expert_weights.reshape(-1)

    order = torch.argsort(flat_expert)
    flat_expert = flat_expert[order]
    flat_token = flat_token[order]
    flat_weight = flat_weight[order]

    uniq, counts = torch.unique_consecutive(flat_expert, return_counts=True)

    out = torch.zeros(n_tokens, d_model, dtype=torch.float32, device=t.device)
    pos = 0
    for expert_id, count in zip(uniq.tolist(), counts.tolist()):
        rows = flat_token[pos:pos + count]
        gate_w = flat_weight[pos:pos + count]
        pos += count

        # One fp32 cast per expert per layer, instead of one per token.
        w1 = self.mlp1_weight[expert_id].float()
        b1 = self.mlp1_bias[expert_id].float()
        w2 = self.mlp2_weight[expert_id].float()
        b2 = self.mlp2_bias[expert_id].float()

        h = t_f32[rows] @ w1 + b1
        h = swiglu(h, limit=self.swiglu_limit, packed=self.packed_geglu)
        o = h @ w2 + b2
        out.index_add_(0, rows, o * gate_w[:, None])

    # Upstream divides the gate weights by k above and multiplies here; keep
    # both so any future change to that convention stays visible.
    out = out * self.experts_per_token
    out = out.to(x.dtype).reshape(*batch_shape, -1)
    return x + out


_original_forward = None


def _host_fingerprint() -> str:
    """Identify the machine well enough to reuse a calibration verdict."""
    import platform
    try:
        import torch
        tv = torch.__version__
    except ImportError:
        tv = "no-torch"
    return "|".join((
        platform.system(), platform.machine(),
        platform.processor() or "?", f"torch{tv}",
    ))


def _calibration_cache_path() -> Path:
    from server.inference import checkpoint_dir
    return checkpoint_dir() / "moe_strategy.json"


def _read_cached_verdict() -> Optional[bool]:
    if os.environ.get("PF_MOE_RECALIBRATE", "").strip() == "1":
        return None
    try:
        data = json.loads(_calibration_cache_path().read_text())
    except (OSError, ValueError):
        return None
    if data.get("host") != _host_fingerprint():
        return None
    value = data.get("use_expert_loop")
    return bool(value) if isinstance(value, bool) else None


def _write_cached_verdict(use_expert_loop: bool, detail: dict) -> None:
    payload = {
        "host": _host_fingerprint(),
        "use_expert_loop": use_expert_loop,
        "measured": detail,
    }
    try:
        path = _calibration_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2))
    except OSError as exc:
        logger.debug("Could not cache MoE calibration: %s", exc)


def _benchmark_strategies(config) -> tuple[bool, dict]:
    """Time both MoE implementations on this host. Returns (use_loop, detail).

    Mirrors the two inner loops rather than calling the model, so it needs no
    checkpoint and stays well under a second. Shapes come from the live
    ``ModelConfig`` so the measurement matches the deployed checkpoint.
    """
    import torch

    e = int(config.num_experts)
    k = int(config.experts_per_token)
    d = int(config.hidden_size)
    f = int(config.intermediate_size)
    n = 256                      # enough tokens to be representative, still fast
    dtype = torch.bfloat16 if "bfloat16" in str(config.param_dtype) else torch.float32

    w1 = torch.randn(e, d, f * 2, dtype=dtype)
    w2 = torch.randn(e, f, d, dtype=dtype)
    x = torch.randn(n, d, dtype=dtype)
    idx = torch.randint(0, e, (n, k))
    gate = torch.rand(n, k)

    def gather_path():
        """Upstream: copy each token's expert weights, cast, batched matmul."""
        batch = int(getattr(config, "torch_ops_batch", 32)) or 32
        out = []
        for s in range(0, n, batch):
            xi, ii, gi = x[s:s + batch], idx[s:s + batch], gate[s:s + batch]
            a = w1[ii, ...].float()
            b = w2[ii, ...].float()
            t = xi.float().unsqueeze(1).expand(-1, k, -1)
            h = torch.einsum("bkd,bkdf->bkf", t, a)
            h = torch.nn.functional.silu(h[..., :f]) * h[..., f:]
            o = torch.einsum("bkf,bkfd->bkd", h, b)
            out.append(torch.einsum("bkd,bk->bd", o, gi))
        return torch.cat(out)

    def loop_path():
        """Ours: group tokens by expert, weights used in place."""
        flat_e = idx.reshape(-1)
        flat_t = torch.arange(n).repeat_interleave(k)
        flat_w = gate.reshape(-1)
        order = torch.argsort(flat_e)
        flat_e, flat_t, flat_w = flat_e[order], flat_t[order], flat_w[order]
        uniq, counts = torch.unique_consecutive(flat_e, return_counts=True)
        out = torch.zeros(n, d, dtype=torch.float32)
        pos = 0
        for expert, cnt in zip(uniq.tolist(), counts.tolist()):
            rows = flat_t[pos:pos + cnt]
            gw = flat_w[pos:pos + cnt]
            pos += cnt
            a = w1[expert].float()
            b = w2[expert].float()
            h = x[rows].float() @ a
            h = torch.nn.functional.silu(h[:, :f]) * h[:, f:]
            out.index_add_(0, rows, (h @ b) * gw[:, None])
        return out

    def best_of(fn, reps=3):
        fn()                                   # warm-up
        return min(_time_once(fn) for _ in range(reps))

    ms_gather = best_of(gather_path)
    ms_loop = best_of(loop_path)
    detail = {"gather_ms": round(ms_gather, 2), "expert_loop_ms": round(ms_loop, 2)}
    return ms_loop < ms_gather, detail


def _time_once(fn) -> float:
    t0 = time.perf_counter()
    fn()
    return (time.perf_counter() - t0) * 1000.0


def install() -> bool:
    """Patch ``MLPBlock.forward`` with the fast path. Returns True if applied.

    Idempotent. Skips (returning False) when disabled via ``PF_FAST_MOE=0``,
    when opf can't be imported, when the block's internals don't match what
    :func:`_fast_forward` expects, or when the on-host benchmark says the
    upstream path is faster (Intel/AMD with native bf16 matmul).
    """
    global _installed, _original_forward
    if _installed:
        return True
    setting = os.environ.get("PF_FAST_MOE", "auto").strip().lower()
    if setting == "0":
        logger.info("Fast MoE disabled via PF_FAST_MOE=0; using upstream path.")
        return False
    try:
        from opf._model.model import MLPBlock
    except ImportError as exc:
        logger.warning("Cannot patch MoE (opf import failed): %s", exc)
        return False

    missing = [a for a in _REQUIRED_ATTRS if not _has_attr(MLPBlock, a)]
    if missing:
        logger.warning(
            "Skipping fast MoE: MLPBlock is missing expected attributes %s "
            "(upstream refactor?). Falling back to the original path.",
            missing,
        )
        return False

    if setting != "1":
        # "auto": let the host decide which implementation is actually faster.
        if not _should_use_expert_loop():
            return False

    _original_forward = MLPBlock.forward
    MLPBlock.forward = _fast_forward
    _installed = True
    logger.info(
        "Fast MoE installed (expert-grouped path). "
        "Set PF_FAST_MOE=0 to disable, PF_MOE_RECALIBRATE=1 to re-measure."
    )
    return True


def _should_use_expert_loop() -> bool:
    """Decide by measurement whether our MoE beats upstream's on this host."""
    cached = _read_cached_verdict()
    if cached is not None:
        logger.info(
            "MoE strategy from cache for this host: %s",
            "expert-loop" if cached else "upstream bf16",
        )
        return cached
    try:
        config = _live_model_config()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not read model config for MoE calibration: %s", exc)
        return True          # fall back to the path we know helps on most CPUs
    try:
        use_loop, detail = _benchmark_strategies(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning("MoE calibration failed (%s); using expert-loop.", exc)
        return True
    logger.info(
        "MoE calibration on this host: upstream=%.1fms expert-loop=%.1fms → %s",
        detail["gather_ms"], detail["expert_loop_ms"],
        "expert-loop" if use_loop else "upstream bf16",
    )
    _write_cached_verdict(use_loop, detail)
    return use_loop


def _live_model_config():
    """Build a ModelConfig from the local checkpoint's config.json.

    Falls back to ``ModelConfig()`` defaults when the checkpoint isn't present
    yet, so calibration still works before the first download completes.
    """
    from opf._model.model import ModelConfig
    from server.inference import checkpoint_dir

    path = checkpoint_dir() / "config.json"
    fields = {f.name for f in dataclasses.fields(ModelConfig)}
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return ModelConfig()
    return ModelConfig(**{k: v for k, v in raw.items() if k in fields})


def _has_attr(cls, name: str) -> bool:
    """True if ``name`` is declared on the class or assigned in ``__init__``.

    Instance attributes (``self.mlp1_weight = ...``) aren't visible on the
    class, so fall back to scanning the ``__init__`` source for the assignment.
    """
    if hasattr(cls, name):
        return True
    try:
        import inspect
        src = inspect.getsource(cls.__init__)
    except (OSError, TypeError):
        return False
    return f"self.{name}" in src


def uninstall() -> None:
    """Restore the upstream forward (used by tests to A/B the two paths)."""
    global _installed
    if not _installed or _original_forward is None:
        return
    from opf._model.model import MLPBlock
    MLPBlock.forward = _original_forward
    _installed = False
