"""OCR for scanned PDF pages (T21; DESIGN D6, D21, INV-7, INV-13).

A page needs OCR when `parse_pdf` finds fewer than 25 non-space characters on it AND an image (`needs_ocr_pages`).
Such a page is rendered with pypdfium2 at 150 DPI and transcribed by Gemini 2.5 Flash through an injectable `OcrClient`.
The transcription becomes ordinary `paragraph` blocks with `ocr=True`, the page number known and `bbox=None` (we do not
know where on the page the text sat; INV-7: nothing is invented).

Untrusted text (INV-13)
-----------------------
The model output is document content. It is never logged and never put into error messages; `clean_ocr_text` strips
control characters (Postgres cannot store NUL) and caps the page. It is data only: nothing here interprets it.

Fakes
-----
`FakeOcrClient` is a test double. Its text is stamped by the caller (or is empty) and `ocr_client_from_env` refuses it
unless the same guard as the fake embedder holds (SCRIBE_ALLOW_FAKE_EMBEDDINGS=1 and a database whose name contains
'test'). Fake results are never published.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import random
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol

import httpx

from .errors import IngestError
from .model import Block, ParsedDocument, assemble

OCR_MODEL = "gemini-2.5-flash"
OCR_DPI = 150
MAX_OCR_PAGES = 50  # DESIGN D21
OCR_PAGES_PER_STEP = 8  # bounded work per leased invocation (each page is one render + one model call)
MAX_OCR_PAGE_CHARS = 30_000
OCR_NOTICE = "OCR text — verify against original"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OCR_PROMPT = (
    "Transcribe the text on this page image as plain text. Keep the reading order. Separate paragraphs with a blank "
    "line. Do not add, summarise, translate or correct anything, and do not describe images; if a part is unreadable "
    "leave it out. If the page has no text, return nothing. Output only the transcription."
)

OcrErrorCode = Literal["RATE_LIMITED", "TRANSIENT_PROVIDER", "INVALID_REQUEST", "AUTH"]


class OcrError(Exception):
    """Provider failure. The message is plain and never contains page text."""

    def __init__(self, code: OcrErrorCode, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code: OcrErrorCode = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class OcrResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class OcrClient(Protocol):
    is_fake: bool
    model: str

    async def transcribe(self, png: bytes, *, page: int) -> OcrResult: ...


# ------------------------------------------------------------------ rendering and text hygiene

def render_page_png(data: bytes, page: int, dpi: int = OCR_DPI) -> bytes:
    """Render 1-based `page` of the PDF `data` to PNG bytes at `dpi` (pypdfium2)."""
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(data)
    try:
        pg = pdf[page - 1]
        try:
            bitmap = pg.render(scale=dpi / 72)
            try:
                buf = io.BytesIO()
                bitmap.to_pil().convert("RGB").save(buf, format="PNG")
                return buf.getvalue()
            finally:
                bitmap.close()
        finally:
            pg.close()
    finally:
        pdf.close()


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FENCE = re.compile(r"\A\s*```[a-zA-Z]*\n(.*?)\n```\s*\Z", re.S)


def clean_ocr_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    text = _CTRL.sub("", text).strip()
    return text[:MAX_OCR_PAGE_CHARS]


def ocr_blocks(page: int, text: str) -> list[Block]:
    """Paragraph blocks (blank-line separated; lines inside a paragraph are joined by a space) for one OCR page."""
    out: list[Block] = []
    for para in re.split(r"\n\s*\n", clean_ocr_text(text)):
        flat = " ".join(line.strip() for line in para.split("\n") if line.strip())
        if flat:
            out.append(Block(type="paragraph", text=flat, page=page, bbox=None, ocr=True))
    return out


def apply_ocr(doc: ParsedDocument, pages: dict[int, str]) -> ParsedDocument:
    """Merge transcribed pages into the parsed document in page order and recompute indexes and canonical-text offsets.
    Every page in `doc.needs_ocr_pages` must be present in `pages` ('' for a blank page). EMPTY_EXTRACTION when the
    merged document still has no readable text."""
    missing = [p for p in doc.needs_ocr_pages if p not in pages]
    if missing:
        raise ValueError(f"OCR pages missing: {missing}")
    new = [b for p in sorted(doc.needs_ocr_pages) for b in ocr_blocks(p, pages[p])]
    # every parsed block has a page (PDF); the sort is stable so reading order inside a page is kept
    merged = sorted([*doc.blocks, *new], key=lambda b: b.page or 0)
    if not any(not b.furniture for b in merged):
        raise IngestError("EMPTY_EXTRACTION", "No readable text was found in this file.")
    out = assemble(merged, page_count=doc.page_count, parser_version=doc.parser_version)
    out.meta = {**doc.meta, "ocr_pages": sorted(doc.needs_ocr_pages),
                "ocr_empty_pages": sorted(p for p in doc.needs_ocr_pages if not ocr_blocks(p, pages[p]))}
    out.warnings = list(doc.warnings)
    return out


# ------------------------------------------------------------------ clients

def _retry_after(resp: httpx.Response) -> float | None:
    header = resp.headers.get("retry-after")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    return None


class GeminiOcrClient:
    """Gemini 2.5 Flash image-to-text over REST. The key comes from GOOGLE_GENERATIVE_AI_API_KEY (or the `api_key`
    argument) and is sent only in the `x-goog-api-key` header; it never appears in URLs, errors or logs."""

    is_fake = False

    def __init__(
        self, *, api_key: str | None = None, model: str = OCR_MODEL,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        max_retries: int = 3, timeout: float = 90.0,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("GOOGLE_GENERATIVE_AI_API_KEY", "")
        self.model = model
        self._transport = transport
        self._sleep = sleep
        self.max_retries = max_retries
        self._timeout = timeout
        self.requests_made = 0

    async def transcribe(self, png: bytes, *, page: int) -> OcrResult:
        if not self._api_key:
            raise OcrError("AUTH", "GOOGLE_GENERATIVE_AI_API_KEY is not set", retryable=False)
        body = {
            "contents": [{"parts": [
                {"text": OCR_PROMPT},
                {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(png).decode("ascii")}},
            ]}],
            "generationConfig": {"temperature": 0, "thinkingConfig": {"thinkingBudget": 0}},
        }
        headers = {"x-goog-api-key": self._api_key, "content-type": "application/json"}
        url = ENDPOINT.format(model=self.model)
        attempt = 0
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            while True:
                self.requests_made += 1
                try:
                    resp = await client.post(url, json=body, headers=headers)
                except httpx.HTTPError as exc:
                    err = OcrError("TRANSIENT_PROVIDER", f"OCR request failed ({type(exc).__name__})", retryable=True)
                    if attempt >= self.max_retries:
                        raise err from None
                else:
                    if resp.status_code == 200:
                        return self._parse(resp, page)
                    err = self._classify(resp)
                    if not err.retryable or attempt >= self.max_retries:
                        raise err
                    hint = _retry_after(resp)
                    await self._sleep(min(hint, 60.0) if hint is not None else self._backoff(attempt))
                    attempt += 1
                    continue
                await self._sleep(self._backoff(attempt))
                attempt += 1

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2.0**attempt, 30.0) * (0.5 + random.random() / 2)

    @staticmethod
    def _classify(resp: httpx.Response) -> OcrError:
        code = resp.status_code  # provider messages are deliberately not echoed
        if code == 429:
            return OcrError("RATE_LIMITED", "OCR provider rate limited (HTTP 429)", retryable=True)
        if code in (401, 403):
            return OcrError("AUTH", f"OCR provider rejected credentials (HTTP {code})", retryable=False)
        if code in (408, 409) or code >= 500:
            return OcrError("TRANSIENT_PROVIDER", f"OCR provider error (HTTP {code})", retryable=True)
        return OcrError("INVALID_REQUEST", f"OCR request rejected (HTTP {code})", retryable=False)

    @staticmethod
    def _parse(resp: httpx.Response, page: int) -> OcrResult:
        try:
            body: dict[str, Any] = resp.json()
        except Exception:  # noqa: BLE001
            raise OcrError("INVALID_REQUEST", "malformed OCR response", retryable=False) from None
        usage = body.get("usageMetadata") or {}
        in_tok, out_tok = int(usage.get("promptTokenCount") or 0), int(usage.get("candidatesTokenCount") or 0)
        cands = body.get("candidates") or []
        if not cands:
            if (body.get("promptFeedback") or {}).get("blockReason"):
                raise OcrError("INVALID_REQUEST", f"the OCR provider declined to read page {page}", retryable=False)
            return OcrResult("", in_tok, out_tok)  # nothing on the page
        parts = (cands[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        return OcrResult(text, in_tok, out_tok)


class FakeOcrClient:
    """DETERMINISTIC TEST DOUBLE. Returns `pages[page]` (default ''), counts calls, can fail on demand.
    Never produces results that may be published."""

    is_fake = True
    model = "fake-ocr"

    def __init__(self, pages: dict[int, str] | None = None, *, default: str = "",
                 fail_on: dict[int, Exception] | None = None,
                 before: Callable[[int], None] | None = None) -> None:
        self.pages = dict(pages or {})
        self.default = default
        self.fail_on = dict(fail_on or {})
        self.before = before  # test hook, e.g. raise to simulate a killed process
        self.calls: list[int] = []

    async def transcribe(self, png: bytes, *, page: int) -> OcrResult:
        if not png.startswith(b"\x89PNG"):
            raise OcrError("INVALID_REQUEST", "fake OCR expects a PNG", retryable=False)
        self.calls.append(page)
        if self.before:
            self.before(page)
        if page in self.fail_on:
            raise self.fail_on[page]
        text = self.pages.get(page, self.default)
        return OcrResult(text, input_tokens=258, output_tokens=len(text) // 4)


class OcrClientRefused(RuntimeError):
    pass


def _database_name(dsn: str) -> str:
    from psycopg.conninfo import conninfo_to_dict

    return str(conninfo_to_dict(dsn).get("dbname") or "") if dsn else ""


def ocr_client_from_env(dsn: str, env: "os._Environ[str] | dict[str, str] | None" = None) -> OcrClient:
    """SCRIBE_OCR=gemini (default) | fake. `fake` needs SCRIBE_ALLOW_FAKE_EMBEDDINGS=1 and a database whose name
    contains 'test'; its output is a stamped placeholder, never real text."""
    env = os.environ if env is None else env
    kind = (env.get("SCRIBE_OCR") or "gemini").lower()
    if kind == "gemini":
        return GeminiOcrClient(api_key=env.get("GOOGLE_GENERATIVE_AI_API_KEY", ""))
    if kind == "fake":
        if env.get("SCRIBE_ALLOW_FAKE_EMBEDDINGS") != "1" or "test" not in _database_name(dsn).lower():
            raise OcrClientRefused(
                "REFUSING to use the fake OCR client: it requires SCRIBE_ALLOW_FAKE_EMBEDDINGS=1 and a database "
                f"whose name contains 'test' (database is {_database_name(dsn)!r}).")
        return FakeOcrClient(default="[FAKE OCR - NOT REAL TEXT] placeholder transcription of a scanned page.")
    raise ValueError(f"SCRIBE_OCR must be 'gemini' or 'fake', got {kind!r}")


def check_ocr_limit(pages: list[int]) -> None:
    if len(pages) > MAX_OCR_PAGES:
        raise IngestError(
            "RESOURCE_LIMIT",
            f"This PDF has {len(pages)} scanned pages that need text recognition. "
            f"The limit is {MAX_OCR_PAGES} scanned pages per file.")
