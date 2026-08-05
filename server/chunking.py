"""Split a long document into overlapping chunks for opf inference.

opf runs a bidirectional transformer with **banded attention** (band size 128,
effective window 257 tokens per position). That means the label the model
assigns to a token only depends on its ~128-token neighbourhood — long-range
dependencies do not affect it. So splitting the input into overlapping chunks
and running inference chunk-by-chunk yields the *same* spans as one big pass,
as long as the overlap between chunks is comfortably bigger than the band.

Chunking is the practical answer to CPU inference time on long documents:
opf's per-forward cost grows with N (sequence length) and, at ~5000 tokens on
a laptop-class CPU, one pass takes 60-100 seconds. Splitting into ~1000-token
chunks with ~150-token overlap keeps every chunk in the fast regime and lets
their combined time stay well below the monolithic pass.

Boundary handling:
    - Chunks are cut on whitespace when possible so we never split a token
      mid-word (the tokenizer would otherwise re-tokenize the two halves
      differently).
    - Overlap is generous enough (default 500 chars ≈ 150-200 tokens) that
      the model sees the same context around every token as it would in the
      single-pass version, so spans near a boundary get the same label.
    - After merging, duplicate spans (same start/end/label) from the overlap
      region are deduplicated.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """One chunk of the original text, with its absolute offset."""
    text: str
    offset: int  # index in the original text where this chunk starts


def split_with_overlap(
    text: str,
    chunk_chars: int,
    overlap_chars: int,
) -> list[Chunk]:
    """Split ``text`` into overlapping chunks that snap to whitespace.

    Each chunk is at most ``chunk_chars`` long; consecutive chunks overlap by
    at least ``overlap_chars`` (approximately — snapping to whitespace may
    shift the boundary by a few characters). Documents shorter than
    ``chunk_chars`` return a single chunk covering the whole text.
    """
    if chunk_chars <= 0 or overlap_chars < 0 or overlap_chars >= chunk_chars:
        raise ValueError(
            f"chunk_chars={chunk_chars} overlap_chars={overlap_chars}: "
            "require 0 <= overlap < chunk"
        )
    n = len(text)
    if n == 0:
        return []
    if n <= chunk_chars:
        return [Chunk(text=text, offset=0)]

    step = chunk_chars - overlap_chars
    # How far back we're willing to snap the end to hit whitespace.
    snap_window = min(overlap_chars, 200)

    chunks: list[Chunk] = []
    start = 0
    while start < n:
        end = min(start + chunk_chars, n)
        if end < n:
            # Prefer to cut on whitespace within [end - snap_window, end].
            back = text.rfind(" ", max(start + step, end - snap_window), end)
            if back != -1 and back > start + step:
                end = back
        chunks.append(Chunk(text=text[start:end], offset=start))
        if end >= n:
            break
        # Advance by (chunk - overlap), snapping forward to whitespace if
        # close by so we don't start mid-word.
        next_start = end - overlap_chars
        if next_start < 0:
            next_start = 0
        fwd = text.find(" ", next_start)
        if fwd != -1 and fwd - next_start < 100:
            next_start = fwd + 1
        # Avoid infinite loops if we somehow can't advance.
        if next_start <= start:
            next_start = start + 1
        start = next_start
    return chunks
