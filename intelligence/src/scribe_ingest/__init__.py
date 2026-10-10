"""Scribe ingestion: bytes + mime -> ParsedDocument. Pure; no database, no network."""

from __future__ import annotations

import inspect
from typing import Callable

from .csv_rows import parse_csv
from .docx import parse_docx
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
register_parser("application/vnd.openxmlformats-officedocument.wordprocessingml.document", parse_docx)
register_parser("text/csv", parse_csv)
register_parser("application/csv", parse_csv)


def parse(data: bytes, mime: str, *, allow_ocr_pending: bool = False, name: str | None = None
) -> ParsedDocument:
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
    # `name` (file name) is forwarded only to parsers that accept it (CSV).
    if name and "name" in inspect.signature(fn).parameters:
        doc = fn(data, name=name)  # type: ignore[call-arg]
    else:
        doc = fn(data)
    if not doc.chunkable_blocks:
        if doc.needs_ocr_pages and allow_ocr_pending:
            return doc
        raise IngestError("EMPTY_EXTRACTION", "No readable text was found in this file.")
    return doc
