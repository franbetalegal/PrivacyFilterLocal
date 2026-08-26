"""Machine-readable codes for everything the backend tells the user.

Project convention: the backend is written in English, the interface speaks
Spanish. User-facing prose in the backend would break one rule or the other, so
the backend never emits prose. It emits a code plus parameters, and the frontend
renders the Spanish sentence (see ``frontend/src/messages.ts``).

Two shapes reach the frontend:

- ``warnings``: a list of ``{"code": ..., "params": {...}}`` on a successful
  response, for things the user should know but that did not stop the operation.
- HTTP error ``detail``: a single ``{"code": ..., "params": {...}}``.

Adding a message means adding a constant here AND its Spanish rendering in the
frontend map. A code with no rendering falls back to the raw code in the UI,
which is ugly on purpose — it should be noticed in review.
"""

from __future__ import annotations

# --- warnings (operation succeeded, but the user must be told something) ---

#: Redaction finished but some detected PII text is still present in the output.
#: params: ``count``
LEAK_DETECTED = "leak_detected"

#: The anonymised copy could not be checked for leaks because it has no text
#: layer (it came from a scan), so surviving data inside the image is invisible
#: to the automatic check.
VERIFICATION_UNAVAILABLE_SCANNED = "verification_unavailable_scanned"

#: opf reported that the input did not survive a tokenizer round trip, so
#: span offsets come from the decoded token text rather than the original.
TOKENIZER_DECODE_MISMATCH = "tokenizer_decode_mismatch"


# --- errors ---------------------------------------------------------------

#: params: ``ext``
UNSUPPORTED_FILE_TYPE = "unsupported_file_type"

PDF_UNREADABLE = "pdf_unreadable"
DOCX_UNREADABLE = "docx_unreadable"

#: params: ``ext``
APPLY_REQUIRES_PDF_OR_DOCX = "apply_requires_pdf_or_docx"

#: params: ``error``
INVALID_SPANS_JSON = "invalid_spans_json"

FILE_NOT_FOUND_OR_EXPIRED = "file_not_found_or_expired"

#: params: ``ext``
MARKDOWN_UNSUPPORTED_FORMAT = "markdown_unsupported_format"
MARKDOWN_NO_TEXT = "markdown_no_text"

TERM_NOT_FOUND = "term_not_found"
TERM_EMPTY = "term_empty"

#: params: ``error``
INVALID_REGEX = "invalid_regex"

GOLD_SET_EMPTY = "gold_set_empty"


def message(code: str, **params) -> dict:
    """Build the payload the frontend renders.

    ``params`` values must be JSON-serialisable and must never carry document
    text: a message travels to logs and to the browser, and the document may
    contain personal data. Counts, extensions and error class names are fine.
    """
    payload: dict = {"code": code}
    if params:
        payload["params"] = params
    return payload
