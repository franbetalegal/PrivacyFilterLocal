"""Tests for the inference-device auto-detection helper."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import device


def test_env_override_wins(monkeypatch):
    monkeypatch.setenv("PF_DEVICE", "cpu")
    assert device.detect_device() == "cpu"


def test_invalid_env_falls_back_to_auto(monkeypatch, caplog):
    monkeypatch.setenv("PF_DEVICE", "wat")
    with caplog.at_level("WARNING", logger="privacy_filter.device"):
        got = device.detect_device()
    # ``got`` depends on the host: it's cpu on CI, mps on M1, cuda where
    # available. The invariant is that we don't return "wat" and we warn.
    assert got in {"cpu", "cuda", "mps"}
    assert any("PF_DEVICE" in r.message for r in caplog.records)


def test_describe_cpu():
    assert device.describe("cpu") == "cpu"


def test_describe_mps_mentions_apple():
    assert "Apple" in device.describe("mps")


def test_describe_cuda_mentions_cuda(monkeypatch):
    # Even if the host doesn't have CUDA, describe should return a string
    # containing "cuda" (falling back to just the name when torch can't
    # introspect the device).
    label = device.describe("cuda")
    assert "cuda" in label.lower()


def test_mps_is_never_auto_selected(monkeypatch):
    """MPS benchmarks slower than the optimised CPU path, so auto-detect must
    never pick it — only an explicit PF_DEVICE=mps should."""
    monkeypatch.delenv("PF_DEVICE", raising=False)
    assert device.detect_device() in {"cpu", "cuda"}


def test_mps_can_be_requested_explicitly(monkeypatch):
    monkeypatch.setenv("PF_DEVICE", "mps")
    assert device.detect_device() == "mps"


def test_mps_available_is_reported_separately():
    """mps_available() is informational (shown in the UI), not a selector."""
    assert isinstance(device.mps_available(), bool)


def test_detect_returns_cpu_when_no_torch(monkeypatch):
    """If torch is missing, we must safely return cpu instead of crashing."""
    monkeypatch.delenv("PF_DEVICE", raising=False)
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) \
        else __builtins__.__import__

    def fake_import(name, *args, **kwargs):
        if name == "torch":
            raise ImportError("simulated missing torch")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert device.detect_device() == "cpu"
