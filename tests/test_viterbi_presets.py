"""Regression tests for the Viterbi calibration presets.

opf validates the calibration artifact with ``_validate_exact_keys``: any
extra field at the top level (e.g. a stray ``_comment``) raises at load time.
These tests parse each preset directly through opf's loader so the JSON stays
compatible even if we drift the format later.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "privacy-filter"))

# Loading the calibration artifact only touches ``opf._core.decoding``, which
# still imports torch — skip cleanly if torch isn't installed.
pytest.importorskip("torch")
from opf._core import decoding  # noqa: E402

from server import inference  # noqa: E402


PRESETS = ("conservative", "balanced", "aggressive")


@pytest.mark.parametrize("mode", PRESETS)
def test_preset_file_exists(mode):
    assert inference.calibration_path_for(mode) is not None, mode


@pytest.mark.parametrize("mode", PRESETS)
def test_preset_loads_through_opf_validator(mode):
    """The exact-keys validator in opf must accept every shipped preset.

    Regression: an early version included a ``_comment`` sibling to
    ``operating_points`` and opf raised
    ``ValueError: Calibration artifact must contain exactly ['operating_points'] (extra=['_comment'])``.
    """
    path = inference.calibration_path_for(mode)
    biases = decoding.resolve_viterbi_biases_from_calibration_path(path)
    # Every documented bias key must be present in the resolved map.
    for key in decoding.VITERBI_BIAS_KEYS:
        assert key in biases
        assert isinstance(biases[key], float)
