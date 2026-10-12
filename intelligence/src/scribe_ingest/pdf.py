"""PDF parser (pdfplumber). Pure: bytes in, ParsedDocument out. No OCR.

Design choices
--------------
* Lines come from pdfplumber (`extract_text_lines`), carrying the median font
  size and whether the font is bold. Coordinates are pdfplumber's: points,
  origin TOP-LEFT (`top`/`bottom` grow downwards). Block bbox is the union of
  its lines.
* Headings: font size >= 1.2x the page median (<= 200 chars), or a numbered
  pattern (`1.`, `2.3`, `3.1.2 Title`, `Chapter 4. Title`) that is short
  (<= 80 chars / 12 words), has no sentence-final punctuation and stands on
  its own (bold, or a one-line paragraph). Font-size headings get levels by
  size rank across the document; numbered headings below a font heading get
  `depth1_level + depth - 1`. A heading block's heading_path includes itself and
  the path persists across pages.
* Paragraphs: consecutive lines of one kind, split when the vertical gap exceeds
  0.7x the font size or the font size changes by > 12%. Never merged across pages.
* Bullets (`•`, `-`, `*` ...) become `list_item` blocks with wrapped lines folded in.
* Tables: pdfplumber ruled tables are rendered as pipe rows, header row first
  (`| h1 | h2 |` newline `| a | b |`), `meta` has `header` and `rows`. Tables drawn
  as monospaced text (consecutive lines starting with `|`) are kept verbatim as
  one table block, separator row included. Both carry a bbox.
* Furniture: a line whose digit-stripped, case-folded text repeats in the top or
  bottom 15% of the page on >= 50% of pages (and >= 3 pages) within a 12 pt
  vertical band. Furniture lines become `furniture=True` blocks, excluded from
  canonical text.
* OCR candidates: pages with < 25 non-space characters AND at least one image go
  to `needs_ocr_pages`; their stray text is dropped so OCR can replace the page.
"""

from __future__ import annotations

import io
import math
import re
import statistics
from dataclasses import dataclass, field
from typing import Any

import pdfplumber

from .errors import IngestError
from .model import Block, ParsedDocument, assemble

MAX_PAGES = 300
OCR_CHAR_THRESHOLD = 25
HEADING_RATIO = 1.2
BAND = 0.15

_NUMBERED = re.compile(
    r"^(?:(?:chapter|section|part|article|appendix)\s+)?(\d+(?:\.\d+)*)(\.?)\s+\S", re.I
)
_BULLET = re.compile(r"^([•▪◦●‣·]|[-*–—])\s+(?=\S)")


@dataclass
class _Line:
    page: int
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    size: float
    bold: bool
    kind: str = "body"  # body | heading | table | furniture | bullet
    para_start: bool = True
    level_hint: int | None = None


@dataclass
class _Page:
    number: int
    width: float
    height: float
    median: float
    items: list[Any] = field(default_factory=list)  # _Line | Block(table)


def _invalid(msg: str) -> IngestError:
    return IngestError("INVALID_DOCUMENT", msg)


def _is_password_error(exc: BaseException | None) -> bool:
    seen = 0
    while exc is not None and seen < 6:
        if "Password" in type(exc).__name__ or "Encrypt" in type(exc).__name__:
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


_ENCRYPTED_MSG = "This PDF is password-protected. Remove the password and upload it again."
_DAMAGED_MSG = "This PDF is damaged or not a valid PDF file."


def _lines_from(page_obj: Any, number: int) -> tuple[list[_Line], float]:
    raw = page_obj.extract_text_lines(return_chars=True, strip=True)
    all_sizes: list[float] = []
    lines: list[_Line] = []
    for r in raw:
        chars = [c for c in r["chars"] if c["text"].strip()]
        text = r["text"].strip()
        if not chars or not text:
            continue
        sizes = [round(c["size"], 2) for c in chars]
        all_sizes.extend(sizes)
        size = statistics.median(sizes)
        bold = sum("bold" in c["fontname"].lower() for c in chars) > len(chars) / 2
        lines.append(
            _Line(number, text, r["x0"], r["top"], r["x1"], r["bottom"], size, bold)
        )
    median = statistics.median(all_sizes) if all_sizes else 0.0
    return lines, median


def _render_table(rows: list[list[str | None]]) -> tuple[str, list[str], list[list[str]]]:
    clean = [[(c or "").replace("\n", " ").strip() for c in row] for row in rows]
    text = "\n".join("| " + " | ".join(r) + " |" for r in clean)
    return text, clean[0], clean[1:]


def _ruled_tables(page_obj: Any) -> list[Any]:
    try:
        found = page_obj.find_tables()
    except Exception:
        return []
    out = []
    for t in found:
        try:
            rows = t.extract()
        except Exception:
            continue
        if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
            out.append((t, rows))
    return out


def _inside(obj: dict, boxes: list[tuple[float, float, float, float]]) -> bool:
    cx = (obj["x0"] + obj["x1"]) / 2
    cy = (obj["top"] + obj["bottom"]) / 2
    return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "", text.casefold())).strip()


def _mark_furniture(pages: list[_Page]) -> None:
    n = len(pages)
    if n < 3:
        return
    groups: dict[str, list[_Line]] = {}
    for p in pages:
        for it in p.items:
            if not isinstance(it, _Line):
                continue
            if it.top < p.height * BAND or it.bottom > p.height * (1 - BAND):
                k = _key(it.text)
                if k:
                    groups.setdefault(k, []).append(it)
    need = max(3, math.ceil(n * 0.5))
    for lines in groups.values():
        if len({ln.page for ln in lines}) < need:
            continue
        # same band: all within 12pt vertically, measured from the top or the bottom edge
        by_page = {p.number: p for p in pages}
        offs = [
            ln.top if ln.top < by_page[ln.page].height / 2 else by_page[ln.page].height - ln.bottom
            for ln in lines
        ]
        if max(offs) - min(offs) <= 12:
            for ln in lines:
                ln.kind = "furniture"


def _heading_numbering(text: str) -> tuple[int, str] | None:
    m = _NUMBERED.match(text)
    if not m:
        return None
    if "." not in m.group(1) and not m.group(2) and not text.lower().startswith(
        ("chapter", "section", "part", "article", "appendix")
    ):
        return None  # "30 days of ..." is not a heading
    return m.group(1).count(".") + 1, m.group(1)


def _short_title(text: str) -> bool:
    return (
        len(text) <= 80
        and len(text.split()) <= 12
        and not re.search(r"[.!?:;,]$", text)
    )


def _classify(page: _Page) -> None:
    """Set kind / para_start for body lines of one page, in reading order."""
    prev: _Line | None = None
    seq = [it for it in page.items if isinstance(it, _Line)]
    for idx, ln in enumerate(seq):
        if ln.kind == "furniture":
            prev = None
            continue
        gap_break = prev is None or (
            ln.top - prev.bottom > 0.7 * ln.size
            or abs(ln.size - prev.size) > 0.12 * max(ln.size, prev.size)
        )
        ln.para_start = gap_break
        prev = ln
    # second pass needs next-line paragraph starts for the "stands alone" test
    for idx, ln in enumerate(seq):
        if ln.kind == "furniture":
            continue
        text = ln.text
        next_starts = True
        for nxt in seq[idx + 1 :]:
            if nxt.kind == "furniture":
                continue
            next_starts = nxt.top - ln.bottom > 0.7 * nxt.size or abs(
                nxt.size - ln.size
            ) > 0.12 * max(nxt.size, ln.size)
            break
        if text.startswith("|") and text.count("|") >= 2:
            ln.kind = "table"
        elif page.median and ln.size >= HEADING_RATIO * page.median and len(text) <= 200:
            ln.kind = "heading"
            ln.level_hint = None
        else:
            num = _heading_numbering(text)
            if (
                num
                and ln.para_start
                and _short_title(text)
                and (ln.bold or next_starts)
            ):
                ln.kind = "heading"
            elif _BULLET.match(text):
                ln.kind = "bullet"
                ln.para_start = True
    # kind transitions always start a new block
    prev = None
    for ln in seq:
        if ln.kind == "furniture":
            prev = None
            continue
        if prev is not None and prev.kind != ln.kind and (
            ln.kind in ("heading", "table") or prev.kind in ("heading", "table")
        ):
            ln.para_start = True
        prev = ln


def _bbox(lines: list[_Line]) -> tuple[float, float, float, float]:
    return (
        round(min(ln.x0 for ln in lines), 2),
        round(min(ln.top for ln in lines), 2),
        round(max(ln.x1 for ln in lines), 2),
        round(max(ln.bottom for ln in lines), 2),
    )


def _split_pipe(line: str) -> list[str]:
    s = line.strip().strip("|")
    return [c.strip() for c in s.split("|")]


def _is_sep(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells)


def parse_pdf(data: bytes) -> ParsedDocument:
    if b"%PDF" not in data[:1024]:
        raise _invalid("This file is not a valid PDF.")
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            n_pages = len(pdf.pages)
            if n_pages > MAX_PAGES:
                raise IngestError(
                    "RESOURCE_LIMIT",
                    f"This PDF has {n_pages} pages. The limit is {MAX_PAGES} pages per file.",
                )
            return _parse_pages(pdf, n_pages)
    except IngestError:
        raise
    except Exception as exc:
        raise _invalid(_ENCRYPTED_MSG if _is_password_error(exc) else _DAMAGED_MSG) from exc


def _parse_pages(pdf: Any, n_pages: int) -> ParsedDocument:
    pages: list[_Page] = []
    needs_ocr: list[int] = []
    for pno, page_obj in enumerate(pdf.pages, start=1):
        width, height = float(page_obj.width), float(page_obj.height)
        n_chars = sum(1 for c in page_obj.chars if c["text"].strip())
        pg = _Page(pno, width, height, 0.0)
        pages.append(pg)
        if n_chars < OCR_CHAR_THRESHOLD and len(page_obj.images) >= 1:
            needs_ocr.append(pno)
            continue
        tables = _ruled_tables(page_obj)
        boxes = [tuple(t.bbox) for t, _ in tables]
        flt = page_obj.filter(lambda o, b=boxes: o.get("object_type") != "char" or not _inside(o, b)) if boxes else page_obj
        lines, pg.median = _lines_from(flt, pno)
        pg.items.extend(lines)
        for t, rows in tables:
            text, header, body = _render_table(rows)
            if not text.strip(" |\n"):
                continue
            x0, top, x1, bottom = t.bbox
            blk = Block(
                type="table",
                text=text,
                page=pno,
                bbox=(round(x0, 2), round(top, 2), round(x1, 2), round(bottom, 2)),
                page_size=(width, height),
                meta={"header": header, "rows": body, "source": "ruled"},
            )
            blk.meta["_top"] = top
            pg.items.append(blk)
        pg.items.sort(key=lambda it: it.top if isinstance(it, _Line) else it.meta["_top"])

    _mark_furniture(pages)
    for pg in pages:
        _classify(pg)

    # document-wide font heading ranks
    sizes = sorted(
        {
            round(it.size * 2) / 2
            for pg in pages
            for it in pg.items
            if isinstance(it, _Line) and it.kind == "heading"
        },
        reverse=True,
    )
    rank = {s: i + 1 for i, s in enumerate(sizes)}

    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []
    depth1_level = 0

    def path() -> list[str]:
        return [t for _, t in stack]

    def add_heading(level: int, lines: list[_Line], pg: _Page) -> None:
        title = " ".join(ln.text for ln in lines)
        level = max(1, min(level, 6))
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        blocks.append(
            Block(
                type="heading",
                text=title,
                heading_path=path(),
                page=pg.number,
                bbox=_bbox(lines),
                page_size=(pg.width, pg.height),
                meta={"level": level},
            )
        )

    for pg in pages:
        ps = (pg.width, pg.height)
        group: list[_Line] = []
        group_kind = ""

        def flush() -> None:
            nonlocal group, group_kind, depth1_level
            if not group:
                return
            kind = group_kind
            if kind == "heading":
                first = group[0]
                num = _heading_numbering(first.text)
                size_key = round(first.size * 2) / 2
                is_font = pg.median and first.size >= HEADING_RATIO * pg.median
                if is_font:
                    level = rank.get(size_key, 1)
                    if num and num[0] == 1:
                        depth1_level = level
                elif num:
                    if depth1_level:
                        level = depth1_level + num[0] - 1
                    else:
                        level = (stack[-1][0] + 1) if stack else 1
                else:
                    level = (stack[-1][0] + 1) if stack else 1
                add_heading(level, group, pg)
            elif kind == "table":
                text = "\n".join(ln.text for ln in group)
                rows = [_split_pipe(ln.text) for ln in group]
                rows = [r for r in rows if not _is_sep(r)]
                blocks.append(
                    Block(
                        type="table",
                        text=text,
                        heading_path=path(),
                        page=pg.number,
                        bbox=_bbox(group),
                        page_size=ps,
                        meta={
                            "header": rows[0] if rows else [],
                            "rows": rows[1:],
                            "source": "text",
                        },
                    )
                )
            else:
                btype = "list_item" if kind == "bullet" else "paragraph"
                text = " ".join(ln.text for ln in group)
                if kind == "bullet":
                    text = _BULLET.sub("", text, count=1)
                blocks.append(
                    Block(
                        type=btype,  # type: ignore[arg-type]
                        text=text,
                        heading_path=path(),
                        page=pg.number,
                        bbox=_bbox(group),
                        page_size=ps,
                        furniture=(kind == "furniture"),
                    )
                )
            group = []
            group_kind = ""

        for it in pg.items:
            if isinstance(it, Block):
                flush()
                it.meta.pop("_top", None)
                it.heading_path = path()
                blocks.append(it)
                continue
            kind = it.kind
            if kind == "bullet" or kind == "body" and group_kind == "bullet" and not it.para_start:
                eff = "bullet"
            else:
                eff = kind
            continues = bool(group) and eff == group_kind and (
                group_kind == "table" or not it.para_start
            )
            if kind == "bullet":
                continues = False
            if not continues:
                flush()
                group_kind = eff
            group.append(it)
        flush()

    # furniture blocks must not carry list/paragraph confusion: ensure type paragraph
    for b in blocks:
        if b.furniture:
            b.type = "paragraph"

    if not any(not b.furniture for b in blocks) and not needs_ocr:
        raise IngestError(
            "EMPTY_EXTRACTION", "No readable text was found in this PDF."
        )
    return assemble(blocks, page_count=n_pages, needs_ocr_pages=needs_ocr)
