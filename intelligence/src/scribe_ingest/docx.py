"""DOCX parser (python-docx).

* The body is walked in DOCUMENT ORDER: paragraphs and tables are interleaved
  exactly as they appear (we iterate the body's child elements).
* `Heading 1..9` styles build `heading_path` (a heading's path includes itself).
* List paragraphs (List Bullet / List Number styles, or any `w:numPr`) become
  `list_item` blocks.
* Tables become `table` blocks rendered like the PDF ruled-table renderer:
  `| h1 | h2 |` then one `| a | b |` line per row, `meta` has `header` and
  `rows`. Horizontally merged cells (`gridSpan`) and vertically merged
  continuation cells (`vMerge`) keep their column slot but are rendered empty,
  so merged text is never repeated.
* DOCX has no reliable pages: `page` and `bbox` are always None (INV-7).
* Section headers/footers live outside the body and are ignored.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any, Iterator

from .errors import IngestError
from .model import Block, ParsedDocument, assemble

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING = re.compile(r"^heading\s*([1-9])$", re.IGNORECASE)
_LIST_STYLE = re.compile(r"^list\s*(bullet|number|continue)?", re.IGNORECASE)
_WS = re.compile(r"\s+")


def _invalid(msg: str = "This Word document could not be read. It may be corrupt or password-protected.") -> IngestError:
    return IngestError("INVALID_DOCUMENT", msg)


def _para_text(p: Any) -> str:
    parts: list[str] = []
    for node in p.iter():
        if node.tag == _W + "t" and node.text:
            parts.append(node.text)
        elif node.tag == _W + "tab":
            parts.append(" ")
        elif node.tag in (_W + "br", _W + "cr"):
            parts.append(" ")
    return _WS.sub(" ", "".join(parts)).strip()


def _style_names(document: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for s in document.styles:
        try:
            out[s.style_id] = s.name or ""
        except Exception:  # pragma: no cover - defensive
            continue
    return out


def _body_children(parent: Any) -> Iterator[Any]:
    for el in parent.iterchildren():
        if el.tag == _W + "sdt":
            content = el.find(_W + "sdtContent")
            if content is not None:
                yield from _body_children(content)
        else:
            yield el


def _cell_text(tc: Any) -> str:
    paras = [_para_text(p) for p in tc.iter(_W + "p")]
    return " ".join(t for t in paras if t)


def _row_cells(tr: Any) -> list[str]:
    cells: list[str] = []
    for tc in tr.findall(_W + "tc"):
        pr = tc.find(_W + "tcPr")
        span = 1
        vcont = False
        if pr is not None:
            gs = pr.find(_W + "gridSpan")
            if gs is not None:
                try:
                    span = max(1, int(gs.get(_W + "val", "1")))
                except ValueError:
                    span = 1
            vm = pr.find(_W + "vMerge")
            # <w:vMerge/> or val="continue" is a continuation of the cell above.
            vcont = vm is not None and vm.get(_W + "val", "continue") == "continue"
        text = "" if vcont else _cell_text(tc)
        cells.append(text)
        cells.extend([""] * (span - 1))
    return cells


def _render_table(tbl: Any) -> tuple[str, list[str], list[list[str]]] | None:
    rows = [_row_cells(tr) for tr in tbl.findall(_W + "tr")]
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    text = "\n".join("| " + " | ".join(r) + " |" for r in rows)
    return text, rows[0], rows[1:]


def parse_docx(data: bytes) -> ParsedDocument:
    import docx  # python-docx

    if not zipfile.is_zipfile(io.BytesIO(data)):
        # Not a zip: corrupt, or an encrypted (OLE) Office file.
        raise _invalid()
    try:
        document = docx.Document(io.BytesIO(data))
        body = document.element.body
        styles = _style_names(document)
    except Exception as exc:  # any python-docx/opc failure means an unreadable package
        raise _invalid() from exc

    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []

    def path() -> list[str]:
        return [t for _, t in stack]

    for el in _body_children(body):
        if el.tag == _W + "p":
            text = _para_text(el)
            if not text:
                continue
            ppr = el.find(_W + "pPr")
            style_el = ppr.find(_W + "pStyle") if ppr is not None else None
            style = styles.get(style_el.get(_W + "val", ""), "") if style_el is not None else ""
            m = _HEADING.match(style)
            if m:
                level = int(m.group(1))
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, text))
                blocks.append(Block(type="heading", text=text, heading_path=path(), meta={"level": level}))
                continue
            num = ppr.find(_W + "numPr") if ppr is not None else None
            numid = num.find(_W + "numId") if num is not None else None
            has_num = numid is not None and numid.get(_W + "val", "0") != "0"
            is_list = has_num or (
                _LIST_STYLE.match(style) is not None and style.lower() != "list paragraph"
            )
            blocks.append(
                Block(type="list_item" if is_list else "paragraph", text=text, heading_path=path())
            )
        elif el.tag == _W + "tbl":
            rendered = _render_table(el)
            if rendered is None:
                continue
            text, header, rows = rendered
            blocks.append(
                Block(
                    type="table",
                    text=text,
                    heading_path=path(),
                    meta={"header": header, "rows": rows, "source": "docx"},
                )
            )

    if not blocks:
        raise IngestError("EMPTY_EXTRACTION", "No readable text was found in this file.")
    return assemble(blocks, page_count=None)
