"""Every golden-v1 evidence quote on a pdf/md/txt corpus file must land in ONE
non-furniture block, and for PDFs that block must be on the labelled page."""

from __future__ import annotations

import json
from collections import defaultdict

import pytest
from _helpers import GOLDEN, corpus_dir, parse_corpus  # noqa: F401
from scribe_eval.normalize import normalize

TEXT_DOCS = {".pdf", ".md", ".txt"}


def _entries():
    seen = set()
    out = []
    for line in GOLDEN.read_text().splitlines():
        case = json.loads(line)
        for ev in case["evidence"]:
            if "." + ev["doc"].rsplit(".", 1)[1] not in TEXT_DOCS:
                continue
            if ev["doc"] == "facility-safety-notice-scanned.pdf":
                continue  # scanned: no text layer, OCR is T21
            key = (ev["doc"], ev["quote"], ev["page"])
            if key in seen:
                continue
            seen.add(key)
            out.append(pytest.param(case["id"], *key, id=f"{case['id']}-{ev['doc']}"))
    return out


ENTRIES = _entries()


def test_entries_collected():
    assert len(ENTRIES) >= 70


@pytest.mark.parametrize("case_id,doc,quote,page", ENTRIES)
def test_quote_lands_in_one_block(parse_corpus, case_id, doc, quote, page):
    parsed = parse_corpus(doc)
    q = normalize(quote)
    hits = [b for b in parsed.blocks if not b.furniture and q in normalize(b.text)]
    assert hits, f"{case_id}: quote not found in any single non-furniture block of {doc}: {quote!r}"
    if doc.endswith(".pdf"):
        pages = sorted({b.page for b in hits})
        assert page in pages, (
            f"{case_id}: quote {quote!r} found on page(s) {pages} but labelled page {page}"
        )


def test_no_body_quote_is_furniture(parse_corpus):
    """A quote must never be matched only by furniture."""
    bad = defaultdict(list)
    for p in ENTRIES:
        case_id, doc, quote, page = p.values
        parsed = parse_corpus(doc)
        q = normalize(quote)
        if any(b.furniture and q in normalize(b.text) for b in parsed.blocks):
            bad[doc].append(quote)
    assert not bad, dict(bad)
