"""End-to-end redaction smoke test.

Requires the OPF checkpoint to be present locally. The test is skipped (never
downloads ~1.5 GB) when no valid checkpoint is found, so it is safe to run in
any environment.
"""

import sys
from pathlib import Path

import pytest

# Make the project root and the opf package importable.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "privacy-filter"))


def _checkpoint_is_available() -> bool:
    model_dir = Path.home() / ".opf" / "privacy_filter"
    if not model_dir.is_dir():
        return False
    if not (model_dir / "config.json").is_file():
        return False
    return any(model_dir.glob("*.safetensors"))


pytestmark = pytest.mark.skipif(
    not _checkpoint_is_available(),
    reason="OPF checkpoint not downloaded; skipping end-to-end redaction test",
)


def test_email_is_detected_and_redacted():
    from opf._api import OPF

    model = OPF(device="cpu")
    result = model.redact("Contact me at jane.doe@example.com please.")

    # The original email must not survive in the redacted output.
    assert "jane.doe@example.com" not in result.redacted_text
    # At least one span should have been detected.
    assert len(result.detected_spans) >= 1
