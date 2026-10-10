from __future__ import annotations

import pytest
from _pdfbuild import build_pdf, page_stream
from _helpers import corpus_dir, parse_corpus  # noqa: F401
from scribe_eval.normalize import normalize
from scribe_ingest import IngestError, canonical_text, parse

PDF = "application/pdf"


def test_leave_policy_headings_and_paths(parse_corpus):
    doc = parse_corpus("hr-leave-policy-2026.pdf")
    heads = [b for b in doc.blocks if b.type == "heading"]
    assert [h.text for h in heads][:3] == [
        "Northwind Logistics Leave Policy 2026",
        "1. Purpose and scope",
        "2. Annual leave",
    ]
    assert "2.6 Peak-season blackout" in [h.text for h in heads]
    assert len(heads) == 1 + 6 + 1  # title, six sections, one sub-heading
    blackout = next(b for b in doc.blocks if b.text.startswith("Between 1 and 24 December"))
    assert blackout.heading_path == [
        "Northwind Logistics Leave Policy 2026",
        "2. Annual leave",
        "2.6 Peak-season blackout",
    ]
    # paragraphs are not headings, even though they start with a clause number
    clause = next(b for b in doc.blocks if b.text.startswith("2.1 Full-time"))
    assert clause.type == "paragraph" and clause.page == 2
    # heading path persists across a page break: 3.1 sits under section 3 on page 3
    sick = next(b for b in doc.blocks if b.text.startswith("3.1 Employees must inform"))
    assert sick.heading_path[-1] == "3. Sick leave" and sick.page == 3


def test_wrapped_title_is_one_heading(parse_corpus):
    doc = parse_corpus("vendor-sla.pdf")
    first = next(b for b in doc.blocks if b.type == "heading")
    assert first.text == "Service Level Agreement: Helios Telematics GmbH"


def test_furniture_on_four_page_pdf(parse_corpus):
    doc = parse_corpus("hr-leave-policy-2026.pdf")
    assert doc.page_count == 4
    furniture = [b for b in doc.blocks if b.furniture]
    assert len(furniture) == 8  # header + footer on each page
    header = "HR Leave Policy 2026 (Version 4.0)"
    assert sum(header in b.text for b in furniture) == 4
    assert sum("Confidential - Internal use only" in b.text for b in furniture) == 4
    assert {b.page for b in furniture} == {1, 2, 3, 4}
    body = [b for b in doc.blocks if not b.furniture]
    assert not any(header in b.text or "Confidential" in b.text for b in body)
    text = canonical_text(doc)
    assert header not in text and "Confidential" not in text
    # the title (same words as the header, different text) is NOT furniture
    assert any(b.type == "heading" and not b.furniture for b in doc.blocks)


def test_two_page_documents_keep_their_headers():
    pages = [
        page_stream([(60, 30, 8, "Repeated header"), (60, 100, 10, "Body text on page one, nothing special.")]),
        page_stream([(60, 30, 8, "Repeated header"), (60, 100, 10, "Body text on page two, nothing special.")]),
    ]
    doc = parse(build_pdf(pages), PDF)
    assert not any(b.furniture for b in doc.blocks)  # min 3 pages


def test_repeated_body_text_in_the_middle_is_not_furniture():
    pages = [
        page_stream([(60, 400, 10, "Same line in the middle of the page."), (60, 300, 10, f"Unique body {chr(97 + i)} here.")])
        for i in range(4)
    ]
    doc = parse(build_pdf(pages), PDF)
    assert not any(b.furniture for b in doc.blocks)


def test_text_table_keeps_header_and_rows_intact(parse_corpus):
    doc = parse_corpus("q3-operations-report.pdf")
    tables = [b for b in doc.blocks if b.type == "table"]
    assert [t.page for t in tables] == [2, 3, 3]
    hub = tables[0]
    lines = hub.text.split("\n")
    assert lines[0].startswith("| Hub |") and "Damage rate %" in lines[0]
    assert any(ln.startswith("| Milan |") for ln in lines)
    assert hub.meta["header"] == ["Hub", "Parcels (000)", "On-time delivery %", "Damage rate %"]
    assert ["Milan", "1,688", "93.7", "0.52"] in hub.meta["rows"]
    assert len(hub.meta["rows"]) == 8
    assert hub.bbox is not None and hub.heading_path[-1] == "2. Hub performance"
    # a heading that follows the table on the same page does not get absorbed
    fin = tables[2]
    assert fin.heading_path[-1] == "4. Financial summary"


def test_ruled_table_is_rendered_header_first():
    lines = [(100, 200, 400, 200), (100, 220, 400, 220), (100, 240, 400, 240),
             (100, 200, 100, 240), (250, 200, 250, 240), (400, 200, 400, 240)]
    texts = [(60, 100, 10, "Intro paragraph before the table, long enough."),
             (105, 215, 10, "Name"), (255, 215, 10, "Qty"), (105, 235, 10, "Bolt"), (255, 235, 10, "12")]
    doc = parse(build_pdf([page_stream(texts, lines)]), PDF)
    table = next(b for b in doc.blocks if b.type == "table")
    assert table.text == "| Name | Qty |\n| Bolt | 12 |"
    assert table.meta["header"] == ["Name", "Qty"] and table.meta["rows"] == [["Bolt", "12"]]
    assert table.bbox == (100.0, 200.0, 400.0, 240.0)
    # the cell text is not duplicated as paragraphs
    assert sum("Bolt" in b.text for b in doc.blocks) == 1


def test_bullets_become_list_items():
    texts = [
        (60, 100, 10, "Packing checklist"),
        (60, 120, 10, "• First item that wraps"),
        (72, 132, 10, "onto a second line"),
        (60, 144, 10, "• Second item"),
    ]
    doc = parse(build_pdf([page_stream(texts)]), PDF)
    items = [b for b in doc.blocks if b.type == "list_item"]
    assert [i.text for i in items] == ["First item that wraps onto a second line", "Second item"]


def test_font_size_heading_and_numbered_levels():
    texts = [
        (60, 60, 20, "Big Title"),
        (60, 100, 14, "Section"),
        (60, 130, 10, "Body text under the section heading, a sentence."),
        (60, 170, 10, "3.1.2 Deep numbered heading"),
        (60, 190, 10, "Body text under the numbered heading, a sentence."),
    ]
    # the numbered heading is a stand-alone short line, so it counts
    doc = parse(build_pdf([page_stream(texts)]), PDF)
    heads = [(b.text, len(b.heading_path)) for b in doc.blocks if b.type == "heading"]
    assert heads[0] == ("Big Title", 1)
    assert heads[1] == ("Section", 2)
    assert heads[2][0] == "3.1.2 Deep numbered heading" and heads[2][1] == 3


def test_scanned_pdf_needs_ocr(corpus_dir):
    data = (corpus_dir / "facility-safety-notice-scanned.pdf").read_bytes()
    doc = parse(data, PDF, allow_ocr_pending=True)
    assert doc.needs_ocr_pages == [1]
    assert doc.blocks == [] and doc.page_count == 1
    with pytest.raises(IngestError) as e:
        parse(data, PDF)  # default: allow_ocr_pending=False
    assert e.value.code == "EMPTY_EXTRACTION"


def test_mixed_pdf_flags_only_the_image_page():
    pages = [
        page_stream([(60, 100, 10, "A normal text page with plenty of characters.")]),
        page_stream([], image=True),
        page_stream([(60, 100, 10, "Another normal text page with enough text.")]),
    ]
    doc = parse(build_pdf(pages, with_image_on={2}), PDF)
    assert doc.needs_ocr_pages == [2]
    assert [b.page for b in doc.blocks] == [1, 3]


def test_blank_page_without_image_is_not_ocr_candidate():
    pages = [page_stream([(60, 100, 10, "Some text that is long enough here.")]), page_stream([])]
    doc = parse(build_pdf(pages), PDF)
    assert doc.needs_ocr_pages == []


def test_blank_pdf_is_empty_extraction():
    with pytest.raises(IngestError) as e:
        parse(build_pdf([page_stream([])]), PDF)
    assert e.value.code == "EMPTY_EXTRACTION"


def test_bbox_is_top_left_origin_and_inside_page(parse_corpus):
    for name in ["hr-leave-policy-2026.pdf", "q3-operations-report.pdf", "onboarding-handbook.pdf"]:
        doc = parse_corpus(name)
        for b in doc.blocks:
            assert b.bbox is not None and b.page_size is not None
            x0, top, x1, bottom = b.bbox
            w, h = b.page_size
            assert 0 <= x0 < x1 <= w and 0 <= top < bottom <= h, (name, b.index, b.bbox)
    doc = parse_corpus("hr-leave-policy-2026.pdf")
    # top-left origin: the header is near the TOP (small y), the footer near the bottom
    header = next(b for b in doc.blocks if b.furniture and b.page == 1 and "Version 4.0" in b.text)
    footer = next(b for b in doc.blocks if b.furniture and b.page == 1 and "Confidential" in b.text)
    assert header.bbox[1] < 60 and footer.bbox[1] > 780
    title = next(b for b in doc.blocks if b.type == "heading")
    body = next(b for b in doc.blocks if b.text.startswith("1.1 "))
    assert title.bbox[1] < body.bbox[1]


def test_bbox_matches_drawn_position():
    doc = parse(build_pdf([page_stream([(100, 200, 10, "Placed at x=100, 200pt from the top.")])]), PDF)
    x0, top, x1, bottom = doc.blocks[0].bbox
    assert x0 == pytest.approx(100, abs=1) and 190 < top < 205 and bottom < 215


def test_spans_are_exact(parse_corpus):
    for name in ["hr-leave-policy-2026.pdf", "q3-operations-report.pdf", "onboarding-handbook.pdf",
                 "vendor-sla.pdf", "security-incident-response.pdf", "expense-policy.pdf"]:
        doc = parse_corpus(name)
        text = canonical_text(doc)
        prev_end = -1
        for b in doc.blocks:
            if b.furniture:
                assert b.char_start == b.char_end
                continue
            assert text[b.char_start : b.char_end] == b.text
            assert b.char_start >= prev_end + (2 if prev_end >= 0 else 1)
            prev_end = b.char_end
        assert prev_end == len(text)
        assert [b.index for b in doc.blocks] == list(range(len(doc.blocks)))


def test_pages_are_one_based_and_exact(parse_corpus):
    doc = parse_corpus("onboarding-handbook.pdf")
    assert doc.page_count == 11
    contact = next(b for b in doc.blocks if b.type == "table")
    assert contact.page == 11
    ch6 = next(b for b in doc.blocks if b.type == "heading" and "Chapter 6" in b.text)
    assert ch6.page == 6
    assert normalize("Finance and expenses | Finance Helpdesk | 4120") in normalize(contact.text)


def test_page_limit_is_resource_limit():
    pages = [page_stream([(60, 300, 10, f"Page {i} text content.")]) for i in range(301)]
    with pytest.raises(IngestError) as e:
        parse(build_pdf(pages), PDF)
    assert e.value.code == "RESOURCE_LIMIT" and "300" in e.value.message


def test_300_pages_is_allowed():
    pages = [page_stream([(60, 300, 10, f"Page {i} text content here.")]) for i in range(300)]
    assert parse(build_pdf(pages), PDF).page_count == 300
