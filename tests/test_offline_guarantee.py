"""The lock on the offline promise for the markitdown add-on.

The Markdown export imports the ``markitdown`` package to render the redacted
document. That package publishes three ways to break the offline guarantee that
matters for a data-protection tool: an LLM client for image captions, the
``SpeechRecognition`` backend that uploads audio to Google, and the Azure
Document Intelligence clients. None of them is hard to re-enable by accident —
a careless ``pip install 'markitdown[all]'`` a year from now is enough.

These tests fail loudly if that happens, and the failure message tells whoever
hits it exactly why the dependency is not wanted.

Adapted from the MarkItDown Local repository (``tests/test_offline_guarantee.py``)
so both projects share the same catch.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("markitdown")

# module -> why it must stay out of the environment
FORBIDDEN_MODULES = {
    "speech_recognition": (
        "markitdown's audio path calls recognize_google, which uploads the "
        "recording to Google. The Anonimizador does not process audio; if this "
        "package is present it means someone installed markitdown[all]."
    ),
    "pydub": (
        "only markitdown's online transcription path needs it; its presence "
        "means the audio-transcription extra got installed."
    ),
    "youtube_transcript_api": (
        "fetching YouTube transcripts is a network call and out of scope."
    ),
    "azure.ai.documentintelligence": (
        "Document Intelligence uploads the document to Azure."
    ),
    "azure.ai.contentunderstanding": (
        "Content Understanding uploads the document to Azure."
    ),
}


@pytest.mark.parametrize("module_name", sorted(FORBIDDEN_MODULES))
def test_network_dependency_is_absent(module_name: str) -> None:
    try:
        importlib.import_module(module_name)
    except ImportError:
        return
    pytest.fail(
        f"{module_name} is installed. {FORBIDDEN_MODULES[module_name]} "
        "Install the selective extras in requirements-server.txt, never "
        "markitdown[all]."
    )


def test_markdown_export_builds_without_an_llm_client() -> None:
    """The image converter uploads images when given a client."""
    from server import markdown_export

    engine = markdown_export._get_engine()
    assert getattr(engine, "_llm_client", None) is None
    assert getattr(engine, "_llm_model", None) is None


def test_markdown_export_disables_plugin_discovery() -> None:
    """Third-party plugins must not add converters behind our back.

    Discovery would let any package in the environment that declares the
    ``markitdown.plugin`` entry point insert a converter that talks to the
    network. We do not register our own plugin from the Anonimizador, so
    discovery buys nothing here anyway.
    """
    from server import markdown_export

    engine = markdown_export._get_engine()
    assert engine._plugins_enabled is False
