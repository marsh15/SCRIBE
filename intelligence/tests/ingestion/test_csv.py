"""CSV parser: the six plan edge cases plus golden-v1 evidence matching.

CSV golden quotes are RAW CSV LINES, but a row block is rendered as
`Table: <name> · Row <n> · <col>: <val> · ...`, so a raw line is never a
substring of the rendered text. `row_matches_quote` therefore accepts a quote
when the raw line's non-empty cell values appear, in order, in ONE row block's
normalized text (delimiter taken from the document meta, quoting handled by
csv). The check is strict: every cell must be present and in order. Separately
`meta["raw"]` is asserted to equal the quote exactly.
"""

from __future__ import annotations

import csv
import io
import json

import pytest
from _helpers import CORPUS, GOLDEN
from scribe_eval.normalize import normalize
from scribe_ingest import IngestError, parse

MIME = "text/csv"


def row_matches_quote(block, quote: str, delimiter: str) -> bool:
    cells = next(csv.reader(io.StringIO(quote), delimiter=delimiter))
    hay = normalize(block.text)
    at = 0
    for cell in cells:
        c = normalize(cell)
        if not c:
            continue
        i = hay.find(c, at)
        if i < 0:
            return False
        at = i + len(c)
    return True


def _parse(name: str):
    return parse((CORPUS / name).read_bytes(), MIME, name=name)


def _entries():
    seen, out = set(), []
    for line in GOLDEN.read_text().splitlines():
        case = json.loads(line)
        for ev in case["evidence"]:
            if ev["doc"].endswith(".csv") and (ev["doc"], ev["quote"]) not in seen:
                seen.add((ev["doc"], ev["quote"]))
                out.append(pytest.param(ev["doc"], ev["quote"], id=f"{case['id']}-{ev['doc']}"))
    return out


ENTRIES = _entries()


def test_entries_collected():
    assert len(ENTRIES) >= 8


@pytest.mark.parametrize("doc,quote", ENTRIES)
def test_golden_quote_matches_one_row(doc, quote):
    parsed = _parse(doc)
    delim = parsed.meta["delimiter"]
    hits = [b for b in parsed.blocks if row_matches_quote(b, quote, delim)]
    assert hits, f"no row block matches {quote!r}"
    assert any(b.meta["raw"] == quote for b in hits), "meta['raw'] must be the original line"
    assert all(b.page is None and b.bbox is None and b.type == "table" for b in hits)


def test_rendered_text_does_not_contain_raw_line():
    """Documents the D17 hazard: raw-line quotes are NOT substrings of row text."""
    parsed = _parse("benefits-table.csv")
    q = "BEN-DEN-01,Dental,Employees after probation,50,600,90"
    assert not any(normalize(q) in normalize(b.text) for b in parsed.blocks)


def test_corpus_header_and_row_text():
    p = _parse("benefits-table.csv")
    assert p.meta["header"][0] == "benefit_code" and not p.meta["generated_header"]
    b = next(b for b in p.blocks if b.meta["raw"].startswith("BEN-HLT-01"))
    assert b.text == (
        "Table: benefits-table.csv · Row 1 · benefit_code: BEN-HLT-01 · benefit: Health insurance (basic)"
        " · eligibility: All employees · employer_pays_pct: 85 · waiting_period_days: 0"
    )  # empty annual_cap_eur omitted
    assert b.row_number == 1 and b.meta["line"] == 2


def test_bom_semicolon_decimal_commas():
    p = _parse("price-list-2026.csv")
    assert p.meta["delimiter"] == ";" and p.meta["encoding"] == "utf-8-sig"
    assert p.meta["header"][0] == "sku"  # BOM stripped
    b = next(b for b in p.blocks if b.meta["raw"].startswith("NW-PAL-120"))
    assert "price_eur: 14,90" in b.text


def test_no_header_and_ragged_rows():
    p = _parse("office-locations.csv")
    assert p.meta["generated_header"] is True and p.meta["ragged"] is True
    assert p.meta["header"][:3] == ["column_1", "column_2", "column_3"]
    assert len(p.meta["header"]) == 8  # widest row
    first = p.blocks[0]
    assert first.row_number == 1 and first.text.startswith("Table: office-locations.csv · Row 1 · column_1: HQ")
    lyon = next(b for b in p.blocks if b.meta["raw"].startswith("D-LYS"))
    assert "column_8: 6 loading doors" in lyon.text and not lyon.meta["ragged"]  # full width
    milan = next(b for b in p.blocks if b.meta["raw"].startswith("D-MIL"))
    assert milan.meta["ragged"] and milan.text.endswith("column_5: Giulia Ferrara")


def test_latin1_fallback():
    p = parse("id,city\n1,Z\xfcrich\n".encode("latin-1"), MIME)
    assert p.meta["encoding"] == "latin-1"
    assert "city: Z\xfcrich" in p.blocks[0].text


def test_quoted_newline_and_default_name():
    p = parse(b'id,note\n1,"line one\nline two"\n2,plain\n', MIME)
    assert len(p.blocks) == 2
    assert p.blocks[0].text == "Table: table · Row 1 · id: 1 · note: line one line two"
    assert p.blocks[0].meta["raw"] == '1,"line one\nline two"'
    assert p.blocks[1].meta["line"] == 4


def test_leading_bom_and_crlf():
    p = parse(b"\xef\xbb\xbfa,b\r\n1,2\r\n3,4\r\n", MIME)
    assert p.meta["header"] == ["a", "b"] and len(p.blocks) == 2
    assert p.blocks[1].meta["raw"] == "3,4"


def test_tab_and_pipe_delimiters():
    assert parse(b"a\tb\n1\t2\n", MIME).meta["delimiter"] == "\t"
    assert parse(b"a|b\n1|2\n", MIME).meta["delimiter"] == "|"


def test_rows_longer_than_header_get_generated_names():
    p = parse(b"a,b\n1,2,3\n4,5\n", MIME)
    assert p.meta["ragged"] and "column_3: 3" in p.blocks[0].text


def test_truncation_at_5000_rows():
    body = "id,v\n" + "".join(f"{i},x{i}\n" for i in range(1, 5002))
    p = parse(body.encode(), MIME)
    assert len(p.blocks) == 5000
    assert p.blocks[-1].row_number == 5000
    assert p.meta["truncated"] is True and p.meta["truncated_rows"] == 1
    assert p.warnings == ["CSV_ROWS_TRUNCATED:1"]
    exact = parse(("id,v\n" + "".join(f"{i},x\n" for i in range(5000))).encode(), MIME)
    assert not exact.meta["truncated"] and exact.warnings == []


def test_empty_and_header_only():
    for data in (b"", b"\xef\xbb\xbf", b"\n\n  \n", b"a,b,c\n"):
        if data == b"a,b,c\n":
            # a single row is data with a generated header, not an error
            assert parse(data, MIME).meta["generated_header"] is True
            continue
        with pytest.raises(IngestError) as e:
            parse(data, MIME)
        assert e.value.code == "EMPTY_EXTRACTION"


def test_other_mimes_unaffected_by_name():
    assert parse(b"hello world\n", "text/plain", name="x.txt").blocks


def test_canonical_offsets():
    p = parse(b"a,b\n1,2\n3,4\n", MIME)
    for b in p.blocks:
        assert p.text[b.char_start : b.char_end] == b.text
