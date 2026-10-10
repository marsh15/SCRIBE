"""Parity of the Python legacy chunker with the real lib/chunking.ts.

Fixtures in fixtures/legacy_chunks/*.json are produced by
`npx tsx scripts/dump-legacy-chunks.ts` (the TS chunker run on each input).
Every chunk text and every metadata field must match exactly. No divergences are
known; offsets are UTF-16 code units (same as JS).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from _helpers import REPO
from scribe_ingest.legacy_chunker import chunk_content

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "legacy_chunks").glob("*.json"))


def test_fixtures_present():
    assert len(FIXTURES) >= 25


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_parity_with_ts_chunker(path):
    case = json.loads(path.read_text(encoding="utf-8"))
    content = (
        (REPO / case["source"]).read_text(encoding="utf-8")
        if "source" in case
        else case["content"]
    )
    opts = case["options"]
    got = chunk_content(content, file_name=opts.get("fileName"), num_pages=opts.get("numPages"))
    want = case["chunks"]
    assert len(got) == len(want)
    for g, w in zip(got, want, strict=True):
        m = w["metadata"]
        assert g.content == w["content"]
        assert g.chunk_index == m["chunkIndex"]
        assert g.total_chunks == m["totalChunks"]
        assert g.file_name == m.get("fileName")
        assert g.estimated_page == m.get("estimatedPage")
        assert g.total_pages == m.get("totalPages")
        assert g.position_percent == m["positionPercent"]
        assert g.section == m["section"]
        assert g.char_offset == m.get("charOffset")
        assert g.char_length == m["charLength"]


def test_chunking_is_not_vacuous():
    multi = [
        p for p in FIXTURES if len(json.loads(p.read_text(encoding="utf-8"))["chunks"]) >= 5
    ]
    assert len(multi) >= 5
