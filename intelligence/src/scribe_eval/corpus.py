"""Load plain text from corpus documents for evidence checking."""

from __future__ import annotations

from pathlib import Path

TEXT_SUFFIXES = {".csv", ".md", ".txt"}


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def _docx_text(path: Path) -> str:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    lines: list[str] = []
    # Body children in document order: paragraphs and tables interleaved.
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.append(Paragraph(child, document).text)
        elif tag == "tbl":
            for row in Table(child, document).rows:
                lines.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(lines)


def _pdf_text(path: Path) -> str:
    import pdfplumber

    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def load_document_text(path: str | Path) -> str:
    """Return the text of a corpus document.

    Scanned PDFs have no text layer; if a sibling ``<name>.truth.txt`` exists
    next to a PDF (e.g. ``scan.pdf`` -> ``scan.truth.txt``) its content is the
    ground-truth text and is returned instead of parsing the PDF.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        truth = path.with_name(path.stem + ".truth.txt")
        if truth.is_file():
            return _decode(truth.read_bytes())
        return _pdf_text(path)
    if suffix == ".docx":
        return _docx_text(path)
    if suffix in TEXT_SUFFIXES:
        return _decode(path.read_bytes())
    raise ValueError(f"unsupported document type: {path.name}")
