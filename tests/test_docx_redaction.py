"""Tests for the run-aware DOCX paragraph redaction helper.

These exercise ``_replace_text_in_paragraph``, the delicate logic that splits
python-docx runs at match boundaries so the placeholder replaces the PII while
the surrounding formatting is preserved.
"""

import sys
from pathlib import Path

import pytest

# Make the project root importable when running pytest from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

docx = pytest.importorskip("docx")

# Importing app_local pulls in gradio; skip the whole module if it is absent.
app_local = pytest.importorskip("app_local")
_replace_text_in_paragraph = app_local._replace_text_in_paragraph


def _paragraph_with_runs(run_texts):
    """Return a fresh paragraph whose runs hold ``run_texts`` in order."""
    document = docx.Document()
    paragraph = document.add_paragraph()
    for text in run_texts:
        paragraph.add_run(text)
    return paragraph


def test_replace_within_single_run():
    para = _paragraph_with_runs(["Hello John"])
    _replace_text_in_paragraph(para, "John", "[NAME]")
    assert para.text == "Hello [NAME]"


def test_replace_spanning_multiple_runs():
    # "John" is split across the run boundary ("Jo" + "hn").
    para = _paragraph_with_runs(["Hel", "lo Jo", "hn!"])
    assert para.text == "Hello John!"
    _replace_text_in_paragraph(para, "John", "[NAME]")
    assert para.text == "Hello [NAME]!"


def test_no_match_leaves_text_unchanged():
    para = _paragraph_with_runs(["Hello John"])
    _replace_text_in_paragraph(para, "Zzz", "[NAME]")
    assert para.text == "Hello John"


def test_replace_at_paragraph_start():
    para = _paragraph_with_runs(["John went home"])
    _replace_text_in_paragraph(para, "John", "[NAME]")
    assert para.text == "[NAME] went home"
