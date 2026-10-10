"""Scribe ingestion: bytes + mime -> ParsedDocument. Pure; no database, no network."""

from __future__ import annotations

from typing import Callable

from .errors import IngestError
from .markdown import parse_markdown
from .model import Block, ParsedDocument, canonical_text
from .pdf import parse_pdf
from .text import parse_text

__all__ = [
    "Block",
    "IngestError",
    "ParsedDocument",
    "canonical_text",
    "parse",
    "register_parser",
]

Parser = Callable[[bytes], ParsedDocument]

_PARSERS: dict[str, Parser] = {}
_ALIASES = {"text/x-markdown": "text/markdown", "application/x-pdf": "application/pdf"}


def _norm_mime(mime: str) -> str:
    m = (mime or "").split(";", 1)[0].strip().lower()
    return _ALIASES.get(m, m)


def register_parser(mime: str, fn: Parser) -> None:
    """Register (or replace) the parser for a mime type. T10 adds DOCX/CSV here."""
    _PARSERS[_norm_mime(mime)] = fn


register_parser("application/pdf", parse_pdf)
register_parser("text/plain", parse_text)
register_parser("text/markdown", parse_markdown)


def parse(data: bytes, mime: str, *, allow_ocr_pending: bool = False) -> ParsedDocument:
    """Parse `data` into typed blocks.

    A document with no readable (non-furniture) blocks raises EMPTY_EXTRACTION,
    except that with `allow_ocr_pending=True` a PDF whose only content is
    image-only pages is returned with empty blocks and `needs_ocr_pages` set.
    """
    fn = _PARSERS.get(_norm_mime(mime))
    if fn is None:
        raise IngestError(
            "UNSUPPORTED_FORMAT",
            f"Files of type '{mime}' are not supported yet.",
        )
    if not data:
        raise IngestError("EMPTY_EXTRACTION", "The file is empty.")
    doc = fn(data)
    if not doc.chunkable_blocks:
        if doc.needs_ocr_pages and allow_ocr_pending:
            return doc
        raise IngestError("EMPTY_EXTRACTION", "No readable text was found in this file.")
    return doc
