"""Tests for the version comparison logic in app_update."""

import sys
from pathlib import Path

# Make the project root importable when running pytest from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_update import compare_versions


def test_equal_versions():
    assert compare_versions("1.3.2", "1.3.2") == 0


def test_current_older():
    assert compare_versions("1.3.1", "1.3.2") == -1
    assert compare_versions("1.2.9", "1.3.0") == -1
    assert compare_versions("0.9.0", "1.0.0") == -1


def test_current_newer():
    assert compare_versions("1.3.3", "1.3.2") == 1
    assert compare_versions("2.0.0", "1.9.9") == 1


def test_different_length_versions():
    # "1.3" should be treated as older than "1.3.0" (fewer components).
    assert compare_versions("1.3", "1.3.0") == -1
    assert compare_versions("1.3.0", "1.3") == 1


def test_malformed_versions_are_treated_as_equal():
    # Non-numeric input must not raise; it falls back to "equal" (0).
    assert compare_versions("abc", "1.2.3") == 0
    assert compare_versions("1.2.3", "") == 0
