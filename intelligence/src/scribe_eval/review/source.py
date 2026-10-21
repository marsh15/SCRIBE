"""Source-document access for the review page: safe corpus paths, page text, page images, quote highlighting."""

from __future__ import annotations

import io
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..corpus import load_document_text
from ..normalize import normalize

CONTEXT_CHARS = 700  # text shown on each side of the quote for non-PDF documents
PREVIEW_CHARS = 1800  # text shown from the top when the quote is not found
RENDER_SCALE = 1.6


def locate(text: str, quote: str) -> tuple[int, int] | None:
    """Return (start, end) offsets in ``text`` of ``quote`` under the D17 normalization, or None.

    Case, Unicode form and whitespace runs are ignored, exactly as ``normalize`` does. The match is found on a per
    character normalized copy of ``text`` carrying an index map back to the original characters.
    """
    nq = normalize(quote)
    if not nq:
        return None
    chars: list[str] = []
    back: list[int] = []
    prev_space = False
    for i, c in enumerate(text):
        if c.isspace() or unicodedata.normalize("NFKC", c).isspace():
            if not prev_space:
                chars.append(" ")
                back.append(i)
            prev_space = True
            continue
        prev_space = False
        n = normalize(c)
        for ch in n:
            chars.append(ch)
            back.append(i)
    flat = "".join(chars)
    pos = flat.find(nq)
    if pos < 0:
        return None
    return back[pos], back[pos + len(nq) - 1] + 1


def contains(text: str, quote: str) -> bool:
    """The canonical D17 contract (used for the found / not found verdict)."""
    nq = normalize(quote)
    return bool(nq) and nq in normalize(text)


@dataclass
class Segment:
    text: str
    hit: bool = False


@dataclass
class SourceView:
    doc: str
    page: int | None
    mode: str  # "pdf" | "scan" | "text" | "missing"
    found: bool
    segments: list[Segment] = field(default_factory=list)
    image_url: str | None = None
    warning: str | None = None
    notes: list[str] = field(default_factory=list)
    needle: str = ""
    page_count: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "doc": self.doc, "page": self.page, "mode": self.mode, "found": self.found,
            "segments": [{"t": s.text, "hit": s.hit} for s in self.segments], "image_url": self.image_url,
            "warning": self.warning, "notes": self.notes, "needle": self.needle, "page_count": self.page_count,
        }


def _segments(text: str, needle: str, *, window: bool) -> tuple[list[Segment], bool]:
    """Split ``text`` around the highlighted needle. ``window`` trims to the context around the hit."""
    span = locate(text, needle) if needle else None
    if span is None:
        found = contains(text, needle) if needle else False
        shown = text if window is False else text[:PREVIEW_CHARS] + ("..." if len(text) > PREVIEW_CHARS else "")
        return [Segment(shown)], found
    a, b = span
    if not window:
        return [Segment(text[:a]), Segment(text[a:b], True), Segment(text[b:])], True
    lo, hi = max(0, a - CONTEXT_CHARS), min(len(text), b + CONTEXT_CHARS)
    return [
        Segment(("..." if lo > 0 else "") + text[lo:a]),
        Segment(text[a:b], True),
        Segment(text[b:hi] + ("..." if hi < len(text) else "")),
    ], True


@lru_cache(maxsize=32)
def _pdf_pages(path: str, mtime_ns: int) -> tuple[str, ...]:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return tuple((p.extract_text() or "") for p in pdf.pages)


@lru_cache(maxsize=64)
def _render_png(path: str, mtime_ns: int, page: int) -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(path)
    try:
        img = pdf[page - 1].render(scale=RENDER_SCALE).to_pil()
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return buf.getvalue()
    finally:
        pdf.close()


class Corpus:
    """Read access to a corpus directory. Document names are confined to the directory (no traversal)."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory).resolve()
        self._doc_text: dict[tuple[str, int], str] = {}

    def path(self, doc: str) -> Path | None:
        if not doc or "\x00" in doc:
            return None
        p = (self.dir / doc).resolve()
        if p.parent != self.dir or not p.is_file():
            return None
        return p

    def has(self, doc: str) -> bool:
        return self.path(doc) is not None

    def full_text(self, doc: str) -> str | None:
        """Document text as ``load_document_text`` returns it (cached on mtime), None when missing or unreadable."""
        p = self.path(doc)
        if p is None:
            return None
        key = (str(p), p.stat().st_mtime_ns)
        if key not in self._doc_text:
            try:
                self._doc_text[key] = load_document_text(p)
            except Exception:
                return None
        return self._doc_text[key]

    def quote_count(self, doc: str, quote: str) -> int | None:
        text = self.full_text(doc)
        nq = normalize(quote)
        if text is None or not nq:
            return None
        return normalize(text).count(nq)

    def png(self, doc: str, page: int) -> bytes | None:
        p = self.path(doc)
        if p is None or p.suffix.lower() != ".pdf" or page < 1:
            return None
        pages = _pdf_pages(str(p), p.stat().st_mtime_ns)
        if page > len(pages):
            return None
        return _render_png(str(p), p.stat().st_mtime_ns, page)

    def view(self, doc: str, page: int | None, needle: str, *, needle_label: str = "quote") -> SourceView:
        p = self.path(doc)
        if p is None:
            return SourceView(doc, page, "missing", False, warning=f"DOCUMENT NOT FOUND IN CORPUS: {doc}", needle=needle)
        if p.suffix.lower() == ".pdf":
            return self._pdf_view(p, doc, page, needle, needle_label)
        text = self.full_text(doc)
        if text is None:
            return SourceView(doc, page, "missing", False, warning=f"CANNOT READ DOCUMENT: {doc}", needle=needle)
        segs, found = _segments(text, needle, window=True)
        warn = None
        if needle and not found:
            warn = f"{needle_label.upper()} NOT FOUND IN THIS DOCUMENT"
        view = SourceView(doc, page, "text", found, segs, warning=warn, needle=needle)
        if not needle:
            view.notes.append("no quote or value to highlight; showing the start of the document")
        return view

    def _pdf_view(self, p: Path, doc: str, page: int | None, needle: str, needle_label: str) -> SourceView:
        mtime = p.stat().st_mtime_ns
        truth = p.with_name(p.stem + ".truth.txt")
        if truth.is_file():
            # Scanned PDF: no text layer; the sibling truth file is the ground-truth text for the (single) page.
            text = load_document_text(p)
            segs, found = _segments(text, needle, window=False)
            pg = page or 1
            return SourceView(
                doc, pg, "scan", found, segs, image_url=f"/api/page.png?doc={_q(doc)}&page={pg}",
                warning=None if found or not needle else f"{needle_label.upper()} NOT FOUND ON THIS PAGE",
                notes=["scanned PDF: showing the rendered page next to its ground-truth text (.truth.txt)"],
                needle=needle, page_count=1,
            )
        try:
            pages = _pdf_pages(str(p), mtime)
        except Exception as exc:
            return SourceView(doc, page, "missing", False, warning=f"CANNOT READ PDF: {exc}", needle=needle)
        n = len(pages)
        notes: list[str] = []
        where = [i + 1 for i, t in enumerate(pages) if needle and contains(t, needle)]
        pg = page
        if pg is None:
            pg = where[0] if where else 1
            notes.append("evidence has no page number; " + (f"quote found on page {pg}" if where else "quote found on no page, showing page 1"))
        if not 1 <= pg <= n:
            return SourceView(doc, pg, "missing", False, warning=f"PAGE {pg} DOES NOT EXIST (document has {n} pages)",
                              needle=needle, page_count=n)
        segs, found = _segments(pages[pg - 1], needle, window=False)
        warn = None
        if needle and not found:
            warn = f"{needle_label.upper()} NOT FOUND ON THIS PAGE"
            if where:
                notes.append(f"the {needle_label} does appear on page(s) {', '.join(map(str, where))}")
            else:
                notes.append(f"the {needle_label} does not appear on any page of this PDF")
        return SourceView(doc, pg, "pdf", found, segs, image_url=f"/api/page.png?doc={_q(doc)}&page={pg}", warning=warn,
                          notes=notes, needle=needle, page_count=n)


def _q(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")
