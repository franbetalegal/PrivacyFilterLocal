"""Optional post-processing: turn the *already-redacted* output into Markdown.

The rest of the app produces a redacted PDF or DOCX preserving the original
format. That is the deliverable for the file. Markdown is an extra output
some users want on top: it is much cheaper in tokens to paste into an LLM for
summarisation, and its explicit structure (headings, lists, tables) helps the
model answer accurately with less context.

The input to this module is ALWAYS the redacted file, never the original, so
no sensitive data ever crosses ``MarkItDown``. The placeholders such as
``[NOMBRE_1]`` and ``[DNI_1]`` are plain text and travel through unchanged.

One caveat drives the design: for PDFs that came in without a text layer, the
Anonimizador redacts by burning black rectangles onto page images, so the
redacted PDF also has no text layer. ``MarkItDown``'s PDF converter uses
``pdfminer``/``pdfplumber`` and would silently return an empty string on such
a file, without OCRing. Two things save this: the Anonimizador has already
OCRed the original and holds the resulting text with substitutions applied
(``inference.RedactionResult.redacted_text``); and OCRing the redacted image
again would just add noise from the black boxes. So for scanned PDFs the
Markdown output is that already-redacted plain text, not the result of a
second pass through markitdown.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger("privacy_filter.markdown_export")

# mammoth (which markitdown uses for DOCX) correctly escapes ``_`` as ``\_`` so
# ``NOMBRE_1`` isn't parsed as partial italics. It is right by the spec, but our
# placeholders are meant to be pasted verbatim into an LLM, and ``[NOMBRE\_1]``
# reads worse than ``[NOMBRE_1]`` to both a human and a model. Undo the escape,
# but only inside placeholder-shaped tokens so we don't accidentally alter
# regular escaped underscores in the user's own content.
_ESCAPED_PLACEHOLDER = re.compile(r"\[([A-ZÁÉÍÓÚÑ]+(?:\\_[A-ZÁÉÍÓÚÑ0-9]+)+)\]")

# Extensions we can hand to markitdown with confidence. Everything else in the
# anonymiser's DOC_EXTS today is .pdf and .docx, but keeping this centralised
# makes it obvious what the module supports and lets it grow with the app.
_MARKITDOWN_EXTS = frozenset({".pdf", ".docx", ".doc", ".pptx", ".xlsx", ".xls"})


_engine = None


def _get_engine():
    """Return a shared MarkItDown instance built with the offline guarantees.

    Two settings carry the guarantee:

    * ``llm_client=None`` so the image converter cannot upload images to a
      remote model for captioning. Redacted documents rarely contain images
      that matter, but the safeguard is free and matches how the same engine
      is configured in MarkItDown Local.
    * ``enable_plugins=False`` so plugin discovery cannot load a third-party
      converter from the environment that talks to the network. If we ever
      register our own local converters here they will be added explicitly.

    Built lazily so importing this module (e.g. at server startup) does not
    pull in magika/onnxruntime unless the feature is actually used.
    """
    global _engine
    if _engine is None:
        from markitdown import MarkItDown

        _engine = MarkItDown(
            enable_builtins=True,
            enable_plugins=False,
            llm_client=None,
        )
    return _engine


def to_markdown(
    redacted_path: str,
    ext: str,
    *,
    fallback_text: Optional[str] = None,
) -> str:
    """Produce Markdown from a redacted PDF/DOCX/etc.

    ``fallback_text`` is the already-redacted plain text the pipeline used for
    detection (i.e. ``RedactionResult.redacted_text``). It is used when the
    file cannot be read by markitdown — the scanned-PDF case described in the
    module docstring, and any other case where markitdown returns empty.
    Callers should always pass it when they have it; the extra memory is a few
    KB of text.
    """
    ext = ext.lower()

    if ext not in _MARKITDOWN_EXTS:
        # A .txt / .md / .json upload never gets here — those don't get a
        # redacted file at all, they are returned as text. This branch is a
        # safety net if a future extension adds a format we haven't wired up.
        return fallback_text or ""

    try:
        stream_info = _stream_info_for(redacted_path, ext)
        with open(redacted_path, "rb") as fh:
            data = fh.read()
        result = _get_engine().convert_stream(io.BytesIO(data), stream_info=stream_info)
        markdown = (result.markdown or "").strip()
    except Exception as exc:  # noqa: BLE001
        # A failure here must not sink the whole redaction: the primary output
        # (PDF/DOCX) is already produced and downloadable. Log, fall back, and
        # move on. The caller decides whether to expose the .md at all.
        logger.warning(
            "markitdown could not convert %s: %s", Path(redacted_path).name, exc,
        )
        markdown = ""

    # Scanned PDFs come out empty because the redacted file has no text layer.
    # Fall back to the plain text the pipeline already extracted-and-redacted;
    # it carries the same placeholders (``[NOMBRE_1]``, etc.) but obviously
    # without the heading/table structure markitdown would have recovered.
    if not markdown:
        return fallback_text or ""
    return _unescape_placeholders(markdown)


def _unescape_placeholders(markdown: str) -> str:
    """Rewrite ``[NOMBRE\\_1]`` back to ``[NOMBRE_1]``.

    Only inside placeholder-shaped tokens, so regular escaped underscores in
    the user's content are left alone.
    """
    return _ESCAPED_PLACEHOLDER.sub(lambda m: "[" + m.group(1).replace("\\_", "_") + "]", markdown)


def _stream_info_for(path: str, ext: str):
    """Give markitdown enough hints to pick the right built-in converter."""
    from markitdown import StreamInfo

    return StreamInfo(filename=Path(path).name, extension=ext)


def clear_engine_cache() -> None:
    """Drop the shared MarkItDown instance.

    Useful in tests that want to change environment variables between runs.
    Not called at request-teardown: the engine holds no per-request state and
    keeping it hot across requests avoids repeatedly importing magika/onnx.
    """
    global _engine
    _engine = None
