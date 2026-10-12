from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from scribe_ingest import IngestError, canonical_text, parse

MD = "text/markdown"


def md(s: str):
    return parse(s.encode(), MD)


def test_atx_and_setext_headings_with_paths():
    doc = md("# Top\n\nintro\n\nSub\n---\n\nbody\n\n### Deep ###\n\ndeep body\n\n## Next\n\nn\n")
    got = [(b.type, b.text, b.heading_path) for b in doc.blocks]
    assert got == [
        ("heading", "Top", ["Top"]),
        ("paragraph", "intro", ["Top"]),
        ("heading", "Sub", ["Top", "Sub"]),
        ("paragraph", "body", ["Top", "Sub"]),
        ("heading", "Deep", ["Top", "Sub", "Deep"]),
        ("paragraph", "deep body", ["Top", "Sub", "Deep"]),
        ("heading", "Next", ["Top", "Next"]),
        ("paragraph", "n", ["Top", "Next"]),
    ]
    assert all(b.page is None and b.bbox is None for b in doc.blocks)


def test_lists_nested():
    doc = md("# L\n\n- a\n  - b\n    continued\n- c\n\n1. one\n2. two\n")
    items = [(b.text, b.meta["depth"], b.meta["ordered"]) for b in doc.blocks if b.type == "list_item"]
    assert items == [("a", 0, False), ("b\ncontinued", 1, False), ("c", 0, False), ("one", 0, True), ("two", 0, True)]
    assert all(b.heading_path == ["L"] for b in doc.blocks)


def test_fenced_code_is_one_block_and_keeps_heading_path():
    doc = md("# API\n\n```json\n{\n  \"a\": 1,\n\n  \"b\": \"# not a heading\"\n}\n```\n\nafter\n")
    code = [b for b in doc.blocks if b.type == "code"]
    assert len(code) == 1
    assert code[0].text == '{\n  "a": 1,\n\n  "b": "# not a heading"\n}'
    assert code[0].heading_path == ["API"] and code[0].meta["lang"] == "json"
    assert [b.text for b in doc.blocks if b.type == "heading"] == ["API"]


def test_unclosed_fence_runs_to_eof():
    doc = md("```\nline1\nline2")
    assert [b.type for b in doc.blocks] == ["code"] and doc.blocks[0].text == "line1\nline2"


def test_pipe_table_keeps_header_and_rows():
    doc = md("## T\n\n| Code | Status |\n|---|---|\n| ERR_A | 403 |\n| ERR_B | 404 |\n\nafter")
    t = next(b for b in doc.blocks if b.type == "table")
    assert t.text.split("\n")[0] == "| Code | Status |"
    assert "| ERR_B | 404 |" in t.text
    assert t.meta["header"] == ["Code", "Status"]
    assert t.meta["rows"] == [["ERR_A", "403"], ["ERR_B", "404"]]
    assert doc.blocks[-1].text == "after"


def test_hr_and_front_matter():
    doc = md("---\ntitle: X\n---\n\n# H\n\ntext\n\n---\n\nmore")
    assert doc.blocks[0].type == "code" and doc.blocks[0].meta["frontmatter"]
    assert [b.text for b in doc.blocks[1:]] == ["H", "text", "more"]


def test_inline_markup_preserved():
    doc = md("Send `Authorization` header, **bold** and [link](http://x).")
    assert doc.blocks[0].text == "Send `Authorization` header, **bold** and [link](http://x)."


def test_crlf_bom_and_latin1():
    assert md("﻿# T\r\n\r\nline  \r\n").text == "T\n\nline"
    doc = parse("caf\xe9 au lait".encode("latin-1"), MD)
    assert doc.blocks[0].text == "café au lait"


def test_mime_parameters_and_alias():
    assert parse(b"# x", "text/markdown; charset=utf-8").blocks[0].text == "x"
    assert parse(b"# x", "text/x-markdown").blocks[0].text == "x"


def test_empty_markdown():
    for src in ["", "   \n\n", "---\n", "```\n```"]:
        with pytest.raises(IngestError) as e:
            parse(src.encode(), MD)
        assert e.value.code == "EMPTY_EXTRACTION"


_FRAGMENTS = [
    "# Title", "## Sub", "text line", "- item", "  - nested", "1. num", "```", "```py",
    "| a | b |", "|---|---|", "| 1 | 2 |", "---", "===", "", "", "> quote", "plain words here",
    "café 中文", "\t- tabbed", "#", "~~~", "***",
]


@settings(max_examples=300, deadline=None)
@given(st.lists(st.sampled_from(_FRAGMENTS) | st.text(max_size=30), max_size=40))
def test_spans_always_valid(lines):
    data = "\n".join(lines).encode()
    try:
        doc = parse(data, MD)
    except IngestError as e:
        assert e.code == "EMPTY_EXTRACTION"
        return
    text = canonical_text(doc)
    prev_end = 0
    for i, b in enumerate(doc.blocks):
        assert b.index == i
        assert b.text and b.text.strip("\n") == b.text
        if b.type != "code":
            assert b.text.strip() == b.text
        assert text[b.char_start : b.char_end] == b.text
        assert b.char_start >= prev_end  # monotonic, no overlap
        if i:
            assert b.char_start == prev_end + 2
        prev_end = b.char_end
    assert prev_end == len(text)
