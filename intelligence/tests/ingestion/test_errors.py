from __future__ import annotations

import os

import httpx
import pytest
from _helpers import corpus_dir  # noqa: F401
from _pdfbuild import build_pdf, page_stream
from scribe_ingest import IngestError, ParsedDocument, parse, register_parser
from scribe_ingest.blob import BlobDownloadError, download_private_blob
from scribe_ingest.model import Block, assemble

PDF = "application/pdf"


def code_of(data: bytes, mime: str = PDF) -> str:
    with pytest.raises(IngestError) as e:
        parse(data, mime)
    assert e.value.message and e.value.message[0].isupper()
    return e.value.code


def test_random_bytes_are_invalid():
    assert code_of(os.urandom(2048)) == "INVALID_DOCUMENT"
    assert code_of(b"not a pdf at all") == "INVALID_DOCUMENT"


def test_truncated_pdf_is_invalid(corpus_dir):
    data = (corpus_dir / "hr-leave-policy-2026.pdf").read_bytes()
    assert code_of(data[: len(data) // 3]) == "INVALID_DOCUMENT"
    assert code_of(b"%PDF-1.4\n" + os.urandom(500)) == "INVALID_DOCUMENT"


def test_encrypted_pdf_is_invalid_with_plain_message():
    data = build_pdf([page_stream([(60, 100, 10, "Secret body text goes here.")])], user_password="hunter2")
    with pytest.raises(IngestError) as e:
        parse(data, PDF)
    assert e.value.code == "INVALID_DOCUMENT"
    assert "password" in e.value.message.lower()


def test_unsupported_format():
    for mime in ["image/png", "application/zip", ""]:
        assert code_of(b"data", mime) == "UNSUPPORTED_FORMAT"
    # DOCX/CSV are supported since T10 (bad bytes are INVALID_DOCUMENT, not UNSUPPORTED_FORMAT)
    assert code_of(b"PK", "application/vnd.openxmlformats-officedocument.wordprocessingml.document") == "INVALID_DOCUMENT"
    assert code_of(b"\n\n", "text/csv") == "EMPTY_EXTRACTION"


def test_empty_inputs():
    assert code_of(b"", "text/plain") == "EMPTY_EXTRACTION"
    assert code_of(build_pdf([page_stream([])])) == "EMPTY_EXTRACTION"


def test_register_parser_extension_point():
    def fake(data: bytes) -> ParsedDocument:
        return assemble([Block(type="paragraph", text=data.decode())])

    register_parser("text/x-fake", fake)
    try:
        assert parse(b"hello", "text/x-fake").text == "hello"
    finally:
        from scribe_ingest import _PARSERS

        _PARSERS.pop("text/x-fake")


# ---- blob download ----------------------------------------------------------


def test_blob_download_sends_bearer_and_returns_body():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["method"] = request.method
        return httpx.Response(200, content=b"abc")

    out = download_private_blob("https://blob.example/x", "tok-123", transport=httpx.MockTransport(handler))
    assert out == b"abc" and seen == {"auth": "Bearer tok-123", "method": "GET"}


@pytest.mark.parametrize("status,retryable", [(404, False), (403, False), (429, True), (503, True)])
def test_blob_http_errors(status, retryable):
    t = httpx.MockTransport(lambda r: httpx.Response(status))
    with pytest.raises(BlobDownloadError) as e:
        download_private_blob("https://blob.example/x", "secret-token", transport=t)
    assert e.value.status == status and e.value.retryable is retryable
    assert "secret-token" not in str(e.value)


def test_blob_size_cap_by_header_and_by_stream():
    big = httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 100))
    with pytest.raises(BlobDownloadError):
        download_private_blob("https://b/x", "t", max_bytes=10, transport=big)

    def chunked(request):
        def gen():
            for _ in range(5):
                yield b"y" * 10

        return httpx.Response(200, content=gen())

    with pytest.raises(BlobDownloadError):
        download_private_blob("https://b/x", "t", max_bytes=25, transport=httpx.MockTransport(chunked))
    assert download_private_blob("https://b/x", "t", max_bytes=50, transport=httpx.MockTransport(chunked)) == b"y" * 50


def test_blob_timeout_and_network_errors_are_retryable():
    def boom(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(BlobDownloadError) as e:
        download_private_blob("https://b/x", "t", transport=httpx.MockTransport(boom))
    assert e.value.retryable

    def refuse(request):
        raise httpx.ConnectError("no", request=request)

    with pytest.raises(BlobDownloadError) as e:
        download_private_blob("https://b/x", "t", transport=httpx.MockTransport(refuse))
    assert e.value.retryable
