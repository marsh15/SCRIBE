"""T21 (pure): rendering, OCR text hygiene, merging into the parsed document, the Gemini client over a mock transport,
and the scanned corpus fixture against its golden quotes. No database, no network; the OCR client is always a fake."""
from __future__ import annotations

import base64
import json
import math
import struct

import httpx
import pytest
from _helpers import CORPUS, GOLDEN
from _pdfbuild import build_pdf, page_stream
from scribe_eval.normalize import normalize
from scribe_ingest import IngestError, parse
from scribe_ingest.chunking import STRUCTURAL_V1, chunk_document
from scribe_ingest.ocr import (MAX_OCR_PAGES, OCR_NOTICE, FakeOcrClient, GeminiOcrClient, OcrClientRefused, OcrError,
                               apply_ocr, check_ocr_limit, clean_ocr_text, ocr_blocks, ocr_client_from_env,
                               render_page_png)

PDF = "application/pdf"
SCANNED = "facility-safety-notice-scanned.pdf"


def scanned_bytes() -> bytes:
    return (CORPUS / SCANNED).read_bytes()


def truth() -> str:
    return (CORPUS / "facility-safety-notice-scanned.truth.txt").read_text()


def mixed(n_pages: int, scanned: set[int]) -> bytes:
    pages = [page_stream([], image=True) if i in scanned else
             page_stream([(60, 100, 10, f"Plain text layer on page {i}, long enough to need no OCR at all.")])
             for i in range(1, n_pages + 1)]
    return build_pdf(pages, with_image_on=scanned)


def png_size(png: bytes) -> tuple[int, int]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", png[16:24])


# ------------------------------------------------------------------ rendering

def test_render_is_png_at_150_dpi():
    w, h = png_size(render_page_png(mixed(1, {1}), 1))  # MediaBox 595 x 842 pt
    assert (w, h) == (math.ceil(595 * 150 / 72), math.ceil(842 * 150 / 72))  # pdfium rounds up


def test_render_uses_requested_page():
    data = mixed(2, {2})
    assert png_size(render_page_png(data, 1)) == png_size(render_page_png(data, 2))


# ------------------------------------------------------------------ text hygiene and blocks

def test_clean_ocr_text_strips_control_chars_fences_and_caps():
    assert clean_ocr_text("a\x00b\x07c\r\nd") == "abc\nd"
    assert clean_ocr_text("```text\nhello\nworld\n```") == "hello\nworld"
    assert len(clean_ocr_text("x" * 100_000)) == 30_000


def test_ocr_blocks_are_flagged_paragraphs_with_page_and_no_bbox():
    blocks = ocr_blocks(3, "Title line\nsecond line\n\n\nNext paragraph")
    assert [b.text for b in blocks] == ["Title line second line", "Next paragraph"]
    assert all(b.ocr and b.page == 3 and b.bbox is None and b.type == "paragraph" for b in blocks)
    assert ocr_blocks(3, "  \n \n") == []


def test_apply_ocr_mixed_pdf_keeps_page_order_and_offsets():
    doc = parse(mixed(3, {2}), PDF, allow_ocr_pending=True)
    assert doc.needs_ocr_pages == [2]
    merged = apply_ocr(doc, {2: "Scanned words on page two."})
    assert [(b.page, b.ocr) for b in merged.blocks] == [(1, False), (2, True), (3, False)]
    assert [b.index for b in merged.blocks] == [0, 1, 2]
    for b in merged.chunkable_blocks:  # canonical-text offsets are recomputed (INV-7)
        assert merged.text[b.char_start:b.char_end] == b.text
    assert merged.meta["ocr_pages"] == [2] and merged.meta["ocr_empty_pages"] == []


def test_apply_ocr_requires_every_flagged_page():
    doc = parse(mixed(3, {2}), PDF, allow_ocr_pending=True)
    with pytest.raises(ValueError):
        apply_ocr(doc, {})


def test_blank_ocr_everywhere_is_empty_extraction():
    doc = parse(scanned_bytes(), PDF, allow_ocr_pending=True)
    with pytest.raises(IngestError) as e:
        apply_ocr(doc, {1: "   "})
    assert e.value.code == "EMPTY_EXTRACTION"


def test_blank_ocr_page_in_a_mixed_pdf_is_recorded_as_empty():
    doc = parse(mixed(2, {2}), PDF, allow_ocr_pending=True)
    merged = apply_ocr(doc, {2: ""})
    assert merged.meta["ocr_empty_pages"] == [2] and [b.page for b in merged.blocks] == [1]


def test_over_50_pages_is_resource_limit():
    check_ocr_limit(list(range(1, MAX_OCR_PAGES + 1)))
    with pytest.raises(IngestError) as e:
        check_ocr_limit(list(range(1, MAX_OCR_PAGES + 2)))
    assert e.value.code == "RESOURCE_LIMIT" and "50" in e.value.message and "T21" not in e.value.message


# ------------------------------------------------------------------ scanned corpus fixture + golden quotes

def test_scanned_fixture_golden_quotes_pass_after_fake_ocr():
    doc = parse(scanned_bytes(), PDF, allow_ocr_pending=True)
    assert doc.needs_ocr_pages == [1] and doc.blocks == []
    merged = apply_ocr(doc, {1: truth()})
    quotes = []
    for line in GOLDEN.read_text().splitlines():
        for ev in json.loads(line)["evidence"]:
            if ev["doc"] == SCANNED:
                quotes.append((ev["quote"], ev["page"]))
    assert quotes, "the scanned fixture must have golden evidence"
    for quote, page in quotes:
        hits = [b for b in merged.blocks if normalize(quote) in normalize(b.text)]
        assert hits, f"quote not in any single OCR block: {quote!r}"
        assert all(b.ocr and b.bbox is None for b in hits) and page in {b.page for b in hits}
        # and the quote survives chunking into one child chunk
        chunks = chunk_document(merged, STRUCTURAL_V1)
        assert any(normalize(quote) in normalize(c.content) for c in chunks.children), quote


# ------------------------------------------------------------------ fake client

async def test_fake_ocr_requires_png_and_counts_calls():
    fake = FakeOcrClient({1: "one"})
    res = await fake.transcribe(render_page_png(mixed(1, {1}), 1), page=1)
    assert res.text == "one" and fake.calls == [1] and fake.is_fake
    with pytest.raises(OcrError):
        await fake.transcribe(b"not a png", page=1)


def test_fake_client_from_env_is_refused_outside_test_databases():
    env = {"SCRIBE_OCR": "fake"}
    with pytest.raises(OcrClientRefused):
        ocr_client_from_env("postgresql://u:p@h/scribe", env)
    with pytest.raises(OcrClientRefused):
        ocr_client_from_env("postgresql://u:p@h/scribe_test", env)  # missing allow flag
    ok = ocr_client_from_env("postgresql://u:p@h/scribe_test", {**env, "SCRIBE_ALLOW_FAKE_EMBEDDINGS": "1"})
    assert ok.is_fake and "FAKE OCR" in ok.default  # type: ignore[attr-defined]
    assert not ocr_client_from_env("postgresql://u:p@h/scribe", {"GOOGLE_GENERATIVE_AI_API_KEY": "k"}).is_fake


# ------------------------------------------------------------------ Gemini client (mock transport, no network)

def _ok(text: str, ptok=300, ctok=40) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}],
                                     "usageMetadata": {"promptTokenCount": ptok, "candidatesTokenCount": ctok}})


async def _no_sleep(_s: float) -> None:
    return None


async def test_gemini_client_request_shape_and_result():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers.get("x-goog-api-key"), json.loads(req.content)
        return _ok("Hello page")

    c = GeminiOcrClient(api_key="test-key-not-real", transport=httpx.MockTransport(handler), sleep=_no_sleep)
    res = await c.transcribe(b"\x89PNG-bytes", page=2)
    assert (res.text, res.input_tokens, res.output_tokens) == ("Hello page", 300, 40)
    assert "gemini-2.5-flash:generateContent" in seen["url"] and "key=" not in seen["url"]
    assert seen["key"] == "test-key-not-real"
    parts = seen["body"]["contents"][0]["parts"]
    assert "plain text" in parts[0]["text"] and "reading order" in parts[0]["text"]
    assert base64.b64decode(parts[1]["inline_data"]["data"]) == b"\x89PNG-bytes"
    assert seen["body"]["generationConfig"]["temperature"] == 0


async def test_gemini_client_retries_429_then_succeeds_and_classifies_errors():
    calls = {"n": 0}

    def flaky(_req):
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "0"}) if calls["n"] < 3 else _ok("done")

    c = GeminiOcrClient(api_key="k", transport=httpx.MockTransport(flaky), sleep=_no_sleep)
    assert (await c.transcribe(b"x", page=1)).text == "done" and calls["n"] == 3

    for status, code, retryable in ((403, "AUTH", False), (400, "INVALID_REQUEST", False), (503, "TRANSIENT_PROVIDER", True)):
        c = GeminiOcrClient(api_key="k", transport=httpx.MockTransport(lambda _r, s=status: httpx.Response(s, text="secret page text")),
                            sleep=_no_sleep, max_retries=1)
        with pytest.raises(OcrError) as e:
            await c.transcribe(b"x", page=1)
        assert (e.value.code, e.value.retryable) == (code, retryable)
        assert "secret page text" not in e.value.message  # provider bodies are never echoed


async def test_gemini_client_without_key_is_auth_error_and_makes_no_request():
    c = GeminiOcrClient(api_key="", transport=httpx.MockTransport(lambda _r: pytest.fail("no request expected")))
    with pytest.raises(OcrError) as e:
        await c.transcribe(b"x", page=1)
    assert e.value.code == "AUTH" and not e.value.retryable


async def test_gemini_client_empty_and_blocked_responses():
    empty = GeminiOcrClient(api_key="k", transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={})))
    assert (await empty.transcribe(b"x", page=1)).text == ""
    blocked = GeminiOcrClient(api_key="k", transport=httpx.MockTransport(
        lambda _r: httpx.Response(200, json={"promptFeedback": {"blockReason": "OTHER"}})))
    with pytest.raises(OcrError) as e:
        await blocked.transcribe(b"x", page=4)
    assert e.value.code == "INVALID_REQUEST" and "page 4" in e.value.message


def test_ocr_notice_text():
    assert OCR_NOTICE == "OCR text — verify against original"
