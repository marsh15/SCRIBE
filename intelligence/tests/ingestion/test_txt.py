from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scribe_ingest import IngestError, canonical_text, parse

TXT = "text/plain"


def test_paragraphs_preserve_text_exactly():
    src = "First line of para one\nsecond line  \n\n\n\nPara two.\r\n\r\n   \nPara three"
    doc = parse(src.encode(), TXT)
    assert [b.text for b in doc.blocks] == ["First line of para one\nsecond line", "Para two.", "Para three"]
    assert all(b.page is None and b.type == "paragraph" for b in doc.blocks)


def test_setext_headings():
    doc = parse(b"Title\n=====\n\nSection\n-------\n\nbody text\n", TXT)
    assert [(b.type, b.text, b.heading_path) for b in doc.blocks] == [
        ("heading", "Title", ["Title"]),
        ("heading", "Section", ["Title", "Section"]),
        ("paragraph", "body text", ["Title", "Section"]),
    ]


def test_caps_and_colon_headings_are_conservative():
    doc = parse(b"INTRODUCTION\n\nSome body text.\n\nSteps:\n\nDo it.\n\nNote: this is not a heading\n\nOK\n\nTHIS IS A SENTENCE.\n", TXT)
    kinds = [(b.type, b.text) for b in doc.blocks]
    assert ("heading", "INTRODUCTION") in kinds
    assert ("heading", "Steps:") in kinds
    assert ("paragraph", "Note: this is not a heading") in kinds
    assert ("paragraph", "OK") in kinds  # too few letters
    assert ("paragraph", "THIS IS A SENTENCE.") in kinds  # trailing period


def test_trailing_colon_without_following_text_is_not_heading():
    doc = parse(b"Body.\n\nDangling:", TXT)
    assert [b.type for b in doc.blocks] == ["paragraph", "paragraph"]


def test_encodings():
    assert parse("﻿héllo".encode("utf-8"), TXT).blocks[0].text == "héllo"
    assert parse(b"caf\xe9", TXT).blocks[0].text == "café"


def test_empty_text():
    for src in [b"", b"\n\n  \n"]:
        with pytest.raises(IngestError) as e:
            parse(src, TXT)
        assert e.value.code == "EMPTY_EXTRACTION"


@settings(max_examples=300, deadline=None)
@given(st.lists(st.sampled_from(["", "", "TITLE", "=====", "-----", "Note:", "word word", "x"]) | st.text(max_size=30), max_size=40))
def test_spans_always_valid(lines):
    try:
        doc = parse("\n".join(lines).encode(), TXT)
    except IngestError as e:
        assert e.code == "EMPTY_EXTRACTION"
        return
    text = canonical_text(doc)
    prev_end = 0
    for i, b in enumerate(doc.blocks):
        assert text[b.char_start : b.char_end] == b.text
        assert b.char_start >= prev_end
        if i:
            assert b.char_start == prev_end + 2
        prev_end = b.char_end
    assert prev_end == len(text)
