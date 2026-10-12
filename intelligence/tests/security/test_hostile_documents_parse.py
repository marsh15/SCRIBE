"""T27: hostile bytes must parse to inert blocks or fail with a plain permanent error, never an INTERNAL error that is
retried. Injection text is kept verbatim (it is data); NUL bytes, which Postgres text/jsonb cannot store, are removed
at parse time for every format."""
from __future__ import annotations

import json

import pytest
from _adv import ADV, CANARY, DOCS

from scribe_ingest import parse

NUL = b"\x00"


def all_strings(doc) -> list[str]:
    out: list[str] = []
    for b in doc.blocks:
        out.append(b.text)
        out.extend(b.heading_path)
        out.append(json.dumps(b.meta, default=str))
    return out


@pytest.mark.parametrize("d", DOCS, ids=lambda d: d["file"])
def test_fixtures_parse_and_keep_hostile_text_verbatim(d):
    doc = parse((ADV / d["file"]).read_bytes(), d["mime"])
    text = "\n".join(b.text for b in doc.blocks)
    assert CANARY in text
    assert d["inert_text"] in text, "attack text must survive, unchanged, as data"


@pytest.mark.parametrize("mime,data", [
    ("text/csv", b"a,b\n1,2" + NUL + b"3\n"),
    ("text/csv", NUL + b"a,b\n" + NUL + b"1," + NUL + b"2\n"),
    ("text/markdown", b"# T" + NUL + b"itle\n\nbody" + NUL + b" text\n"),
    ("text/plain", b"line" + NUL + b" one\n\nline two\n"),
])
def test_nul_bytes_never_reach_block_text_or_meta(mime, data):
    doc = parse(data, mime)
    assert doc.blocks
    for s in all_strings(doc):
        assert "\x00" not in s and "\\u0000" not in s
