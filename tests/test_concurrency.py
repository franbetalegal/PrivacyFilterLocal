"""Tests for the shared CPU-budget policy."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import concurrency


def test_reserves_two_cores_by_default(monkeypatch):
    monkeypatch.delenv("PF_RESERVED_CORES", raising=False)
    monkeypatch.delenv("PF_MAX_WORKERS", raising=False)
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    assert concurrency.worker_count() == 6


def test_never_returns_less_than_one(monkeypatch):
    monkeypatch.delenv("PF_MAX_WORKERS", raising=False)
    monkeypatch.setenv("PF_RESERVED_CORES", "2")
    monkeypatch.setattr("os.cpu_count", lambda: 1)
    assert concurrency.worker_count() == 1
    monkeypatch.setattr("os.cpu_count", lambda: 2)
    assert concurrency.worker_count() == 1


def test_reserved_cores_is_configurable(monkeypatch):
    monkeypatch.delenv("PF_MAX_WORKERS", raising=False)
    monkeypatch.setenv("PF_RESERVED_CORES", "4")
    monkeypatch.setattr("os.cpu_count", lambda: 16)
    assert concurrency.worker_count() == 12


def test_max_workers_caps_budget(monkeypatch):
    monkeypatch.setenv("PF_RESERVED_CORES", "2")
    monkeypatch.setenv("PF_MAX_WORKERS", "3")
    monkeypatch.setattr("os.cpu_count", lambda: 32)
    # 32 - 2 = 30, but capped at 3.
    assert concurrency.worker_count() == 3


def test_max_workers_does_not_raise_budget(monkeypatch):
    monkeypatch.setenv("PF_RESERVED_CORES", "2")
    monkeypatch.setenv("PF_MAX_WORKERS", "100")
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    # cap is higher than the reserved-core budget, so budget wins.
    assert concurrency.worker_count() == 6


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("PF_MAX_WORKERS", raising=False)
    monkeypatch.setenv("PF_RESERVED_CORES", "not-a-number")
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    assert concurrency.worker_count() == 6  # default reserve of 2


def test_reserved_cores_zero_uses_all(monkeypatch):
    monkeypatch.delenv("PF_MAX_WORKERS", raising=False)
    monkeypatch.setenv("PF_RESERVED_CORES", "0")
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    assert concurrency.worker_count() == 8
