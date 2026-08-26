"""Parity and speed tests for the expert-grouped MoE fast path.

The important guarantee is *parity*: patching the MoE must not change which
spans opf detects. These tests load the real checkpoint and compare the two
implementations span-for-span, so they're skipped when the model isn't present.
"""
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "privacy-filter"))

from server import moe_fast


def _checkpoint_available() -> bool:
    # Ask inference for the directory instead of rebuilding the path: it honours
    # OPF_CHECKPOINT, which the portable build sets. A hardcoded ~/.opf path
    # disagrees with the loader on such a machine, so these tests would skip
    # while the model is available, or run while it is not.
    from server.inference import checkpoint_dir

    d = checkpoint_dir()
    return (
        d.is_dir()
        and (d / "config.json").is_file()
        and any(d.glob("*.safetensors"))
    )


# --- Unit tests that don't need the checkpoint ----------------------------


def test_install_is_idempotent():
    pytest.importorskip("torch")
    try:
        first = moe_fast.install()
        second = moe_fast.install()
        assert first == second
    finally:
        moe_fast.uninstall()


def test_disabled_via_env(monkeypatch):
    pytest.importorskip("torch")
    monkeypatch.setenv("PF_FAST_MOE", "0")
    moe_fast.uninstall()
    assert moe_fast.install() is False


def test_has_attr_detects_init_assignments():
    """The guard must see attributes assigned in __init__, not just on the class."""
    pytest.importorskip("torch")
    model = pytest.importorskip("opf._model.model")
    for attr in ("mlp1_weight", "mlp2_bias", "swiglu_limit"):
        assert moe_fast._has_attr(model.MLPBlock, attr), attr
    assert not moe_fast._has_attr(model.MLPBlock, "definitely_not_an_attr")


def test_install_skips_when_attrs_missing(monkeypatch):
    """An upstream refactor must degrade to the slow path, not crash."""
    pytest.importorskip("torch")
    moe_fast.uninstall()
    monkeypatch.setattr(moe_fast, "_REQUIRED_ATTRS", ("nonexistent_attribute",))
    assert moe_fast.install() is False


# --- Parity against the real model ----------------------------------------

pytestmark_model = pytest.mark.skipif(
    not _checkpoint_available(),
    reason="OPF checkpoint not downloaded; skipping MoE parity test",
)

PARITY_TEXT = (
    "En Madrid, a 15 de marzo de 2024, comparece Juan García Pérez, "
    "con DNI 12345678Z y correo juan.garcia@example.com, en nombre de "
    "ACME Consultores S.L. El pago se abonará en la cuenta "
    "ES9121000418450200051332. Teléfono de contacto +34 612 345 678."
)


@pytestmark_model
def test_fast_moe_produces_identical_spans():
    """The fast path must detect exactly the same spans as upstream."""
    from opf._api import OPF

    moe_fast.uninstall()
    model = OPF(device="cpu")

    baseline = model.redact(PARITY_TEXT)
    baseline_spans = [
        (s.label, s.start, s.end, s.text) for s in baseline.detected_spans
    ]

    assert moe_fast.install() is True
    try:
        fast = model.redact(PARITY_TEXT)
        fast_spans = [
            (s.label, s.start, s.end, s.text) for s in fast.detected_spans
        ]
    finally:
        moe_fast.uninstall()

    assert fast_spans == baseline_spans, (
        f"Fast MoE changed detections.\n"
        f"upstream: {baseline_spans}\n"
        f"fast:     {fast_spans}"
    )


@pytestmark_model
def test_fast_moe_is_faster():
    """Sanity check that the patch actually speeds inference up."""
    from opf._api import OPF

    # A longer text so the difference is unambiguous.
    text = PARITY_TEXT * 6

    moe_fast.uninstall()
    model = OPF(device="cpu")
    model.redact(text[:200])          # warm-up (lazy weight materialisation)

    t0 = time.time()
    model.redact(text)
    slow = time.time() - t0

    assert moe_fast.install() is True
    try:
        t0 = time.time()
        model.redact(text)
        fast = time.time() - t0
    finally:
        moe_fast.uninstall()

    # Expect a large win; assert a conservative 2× so the test isn't flaky on
    # loaded CI machines.
    assert fast * 2 < slow, f"expected >=2x speedup, got slow={slow:.2f}s fast={fast:.2f}s"


# --- Hardware portability --------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("CI") == "true",
    reason=(
        "Runs the real wall-clock micro-benchmark (_benchmark_strategies), "
        "not a mock — 'sub-second' on real hardware but slow/unpredictable "
        "on a shared CI runner (it hung ~44s and got the job canceled before "
        "this guard was added). The mechanism it exercises is hardware-"
        "independent and already covered locally; the two tests below only "
        "read/write the cache and don't run the benchmark itself."
    ),
)
def test_calibration_picks_the_faster_strategy_and_caches_it(monkeypatch, tmp_path):
    """The choice must be measured per host, not hard-coded for ARM.

    An Intel Xeon with AVX-512-BF16/AMX can run the upstream bf16 path faster
    than our fp32 expert loop, so install() benchmarks both and keeps the
    winner. This asserts the mechanism, not a particular winner.
    """
    pytest.importorskip("torch")
    monkeypatch.setattr(moe_fast, "_calibration_cache_path",
                        lambda: tmp_path / "moe_strategy.json")
    monkeypatch.delenv("PF_FAST_MOE", raising=False)
    monkeypatch.delenv("PF_MOE_RECALIBRATE", raising=False)

    config = moe_fast._live_model_config()
    use_loop, detail = moe_fast._benchmark_strategies(config)
    assert {"gather_ms", "expert_loop_ms"} <= set(detail)
    assert detail["gather_ms"] > 0 and detail["expert_loop_ms"] > 0
    # The verdict must follow the measurement, whichever way it goes.
    assert use_loop == (detail["expert_loop_ms"] < detail["gather_ms"])

    moe_fast._write_cached_verdict(use_loop, detail)
    assert moe_fast._read_cached_verdict() == use_loop


def test_cached_verdict_is_ignored_on_a_different_host(monkeypatch, tmp_path):
    """A cache written on another CPU must not be trusted."""
    pytest.importorskip("torch")
    import json
    cache = tmp_path / "moe_strategy.json"
    cache.write_text(json.dumps({"host": "SomeOtherBox|x86_64|xeon|torch9",
                                 "use_expert_loop": False}))
    monkeypatch.setattr(moe_fast, "_calibration_cache_path", lambda: cache)
    monkeypatch.delenv("PF_MOE_RECALIBRATE", raising=False)
    assert moe_fast._read_cached_verdict() is None


def test_recalibrate_env_bypasses_the_cache(monkeypatch, tmp_path):
    pytest.importorskip("torch")
    import json
    cache = tmp_path / "moe_strategy.json"
    cache.write_text(json.dumps({"host": moe_fast._host_fingerprint(),
                                 "use_expert_loop": True}))
    monkeypatch.setattr(moe_fast, "_calibration_cache_path", lambda: cache)
    monkeypatch.setenv("PF_MOE_RECALIBRATE", "1")
    assert moe_fast._read_cached_verdict() is None


def test_force_flags_skip_calibration(monkeypatch):
    pytest.importorskip("torch")
    moe_fast.uninstall()
    monkeypatch.setenv("PF_FAST_MOE", "0")
    assert moe_fast.install() is False


def test_live_model_config_survives_a_missing_checkpoint(monkeypatch, tmp_path):
    """Calibration must work before the first model download completes."""
    pytest.importorskip("torch")
    monkeypatch.setattr("server.inference.checkpoint_dir", lambda: tmp_path)
    config = moe_fast._live_model_config()
    assert config.num_experts > 0 and config.hidden_size > 0
