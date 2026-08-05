"""Tests for the text-chunking utility used by opf on long inputs."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.chunking import Chunk, split_with_overlap


def test_short_text_returns_single_chunk():
    text = "short"
    chunks = split_with_overlap(text, chunk_chars=100, overlap_chars=20)
    assert chunks == [Chunk(text="short", offset=0)]


def test_empty_text_returns_no_chunks():
    assert split_with_overlap("", chunk_chars=100, overlap_chars=20) == []


def test_chunks_cover_the_whole_text_with_overlap():
    text = " ".join(f"word{i}" for i in range(500))   # ~3.5k chars
    chunks = split_with_overlap(text, chunk_chars=1000, overlap_chars=200)
    assert len(chunks) >= 4
    # Every offset+text must match the original.
    for c in chunks:
        assert text[c.offset:c.offset + len(c.text)] == c.text
    # First chunk starts at 0; last chunk ends at len(text).
    assert chunks[0].offset == 0
    last = chunks[-1]
    assert last.offset + len(last.text) == len(text)


def test_consecutive_chunks_actually_overlap():
    text = " ".join(f"word{i:04d}" for i in range(500))
    chunks = split_with_overlap(text, chunk_chars=1000, overlap_chars=300)
    for a, b in zip(chunks, chunks[1:]):
        a_end = a.offset + len(a.text)
        b_start = b.offset
        # The overlap is at least 100 chars — snapping to whitespace can
        # shrink it a bit, but never below this.
        assert a_end - b_start >= 100, (a.offset, a_end, b.offset)


def test_chunks_snap_to_whitespace_boundaries():
    """A chunk cut must never split a word in the middle."""
    text = " ".join("palabra" for _ in range(300))
    chunks = split_with_overlap(text, chunk_chars=500, overlap_chars=100)
    for c in chunks:
        # No chunk starts or ends inside a "palabra" token.
        if c.offset > 0:
            assert text[c.offset - 1] == " " or text[c.offset] == " ", c
        end = c.offset + len(c.text)
        if end < len(text):
            assert c.text.endswith(" ") or text[end] == " ", c


def test_invalid_config_raises():
    with pytest.raises(ValueError):
        split_with_overlap("x" * 100, chunk_chars=100, overlap_chars=100)
    with pytest.raises(ValueError):
        split_with_overlap("x" * 100, chunk_chars=100, overlap_chars=200)
    with pytest.raises(ValueError):
        split_with_overlap("x" * 100, chunk_chars=0, overlap_chars=0)
