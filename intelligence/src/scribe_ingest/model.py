"""Typed parse result.

Canonical text (DESIGN D7)
--------------------------
The canonical text of a document is the `text` of every NON-furniture block,
joined with "\\n\\n". `char_start`/`char_end` are offsets into that string, so
`canonical_text(doc)[b.char_start:b.char_end] == b.text` for every non-furniture
block. This is what spans, quote checks and chunk boundaries refer to.

Furniture blocks (repeated page headers/footers) stay in `blocks` with
`furniture=True` so nothing is silently lost, but they are excluded from the
canonical text and from chunking. Their `char_start == char_end` is the offset
at which they would have appeared (a zero-length span), so the columns are never
null and spans stay monotonic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Iterable, Literal

BlockType = Literal["heading", "paragraph", "list_item", "table", "code"]

PARSER_VERSION = "scribe-ingest/1.0.0"
BLOCK_SEPARATOR = "\n\n"


@dataclass
class Block:
    type: BlockType
    text: str
    index: int = 0
    # Path of headings leading to this block. A heading block's path includes itself.
    heading_path: list[str] = field(default_factory=list)
    page: int | None = None  # 1-based; None when the format has no pages
    # (x0, top, x1, bottom) in PDF points, origin at the TOP-LEFT of the page.
    bbox: tuple[float, float, float, float] | None = None
    page_size: tuple[float, float] | None = None  # (width, height) in points
    ocr: bool = False
    row_number: int | None = None  # CSV rows (T10)
    char_start: int = 0
    char_end: int = 0
    furniture: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    blocks: list[Block]
    page_count: int | None = None
    needs_ocr_pages: list[int] = field(default_factory=list)
    parser_version: str = PARSER_VERSION
    # Source-level flags (T10): e.g. CSV encoding/delimiter, and warnings such as
    # "CSV_ROWS_TRUNCATED:<n>" (DESIGN D21: the source is flagged, not failed).
    meta: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @cached_property
    def text(self) -> str:
        return BLOCK_SEPARATOR.join(b.text for b in self.blocks if not b.furniture)

    @property
    def chunkable_blocks(self) -> list[Block]:
        return [b for b in self.blocks if not b.furniture]


def canonical_text(doc: ParsedDocument) -> str:
    return doc.text


def assemble(
    blocks: Iterable[Block],
    *,
    page_count: int | None = None,
    needs_ocr_pages: Iterable[int] = (),
    parser_version: str = PARSER_VERSION,
) -> ParsedDocument:
    """Assign `index` and canonical-text offsets, then build the document."""
    out: list[Block] = []
    cursor = 0
    seen_real = False
    for i, b in enumerate(blocks):
        b.index = i
        if b.furniture:
            b.char_start = b.char_end = cursor
        else:
            if seen_real:
                cursor += len(BLOCK_SEPARATOR)
            seen_real = True
            b.char_start = cursor
            cursor += len(b.text)
            b.char_end = cursor
        out.append(b)
    return ParsedDocument(
        blocks=out,
        page_count=page_count,
        needs_ocr_pages=sorted(set(needs_ocr_pages)),
        parser_version=parser_version,
    )
