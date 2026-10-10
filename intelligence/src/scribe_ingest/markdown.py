"""Hand-written Markdown block parser (no dependency).

Supported: ATX and setext headings, paragraphs, bullet/numbered list items
(nested, `meta["depth"]`), fenced code blocks (content only, one block each),
pipe tables (raw rows kept, header recoverable from `meta`), YAML front matter
(emitted as a `code` block with `meta["frontmatter"]`). Inline markup is left
as written so quote checks against the source hold. `page` is always None.
heading_path of a heading block includes the heading itself.
"""

from __future__ import annotations

import re

from .errors import IngestError
from .model import Block, ParsedDocument, assemble
from .text import decode_text, normalize_lines

_ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})[ \t]*([^`]*)$")
_HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_LIST = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")
_QUOTE = re.compile(r"^ {0,3}>[ ]?(.*)$")


def _split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def _is_table_start(lines: list[str], i: int) -> bool:
    return (
        "|" in lines[i]
        and i + 1 < len(lines)
        and "|" in lines[i + 1]
        and bool(_TABLE_SEP.match(lines[i + 1]))
        and "-" in lines[i + 1]
    )


def _starts_block(lines: list[str], i: int) -> bool:
    ln = lines[i]
    return bool(
        _ATX.match(ln)
        or _FENCE.match(ln)
        or _HR.match(ln)
        or _LIST.match(ln)
        or _is_table_start(lines, i)
    )


def parse_markdown(data: bytes) -> ParsedDocument:
    lines = normalize_lines(decode_text(data))
    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []
    n = len(lines)
    i = 0

    def path() -> list[str]:
        return [t for _, t in stack]

    def add_heading(level: int, title: str) -> None:
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        blocks.append(Block(type="heading", text=title, heading_path=path()))

    # YAML front matter
    if n and lines[0].strip() == "---":
        for j in range(1, min(n, 60)):
            if lines[j].strip() in ("---", "..."):
                body = "\n".join(lines[1:j]).strip()
                if body:
                    blocks.append(
                        Block(type="code", text=body, meta={"frontmatter": True})
                    )
                i = j + 1
                break

    list_indents: list[int] = []
    while i < n:
        ln = lines[i]
        if ln.strip() == "":
            i += 1
            continue

        m = _FENCE.match(ln)
        if m:
            fence = m.group(2)
            lang = m.group(3).strip()
            j = i + 1
            body: list[str] = []
            while j < n:
                cm = re.match(r"^ {0,3}(" + re.escape(fence[0]) + r"{" + str(len(fence)) + r",})[ \t]*$", lines[j])
                if cm:
                    break
                body.append(lines[j])
                j += 1
            text = "\n".join(body).strip("\n")
            if text.strip():
                blocks.append(
                    Block(type="code", text=text, heading_path=path(), meta={"lang": lang})
                )
            i = j + 1
            list_indents = []
            continue

        m = _ATX.match(ln)
        if m:
            title = (m.group(2) or "").strip()
            if title:
                add_heading(len(m.group(1)), title)
            i += 1
            list_indents = []
            continue

        if _HR.match(ln) and not _LIST.match(ln):
            i += 1
            continue

        if _is_table_start(lines, i):
            header = _split_row(lines[i])
            rows: list[list[str]] = []
            raw = [lines[i].strip(), lines[i + 1].strip()]
            j = i + 2
            while j < n and lines[j].strip() and "|" in lines[j]:
                raw.append(lines[j].strip())
                rows.append(_split_row(lines[j]))
                j += 1
            blocks.append(
                Block(
                    type="table",
                    text="\n".join(raw),
                    heading_path=path(),
                    meta={"header": header, "rows": rows},
                )
            )
            i = j
            list_indents = []
            continue

        m = _LIST.match(ln)
        if m:
            indent = len(m.group(1).expandtabs(4))
            while list_indents and list_indents[-1] > indent:
                list_indents.pop()
            if not list_indents or list_indents[-1] < indent:
                list_indents.append(indent)
            depth = len(list_indents) - 1
            marker = m.group(2)
            item = [m.group(3).strip()]
            j = i + 1
            # lazy/indented continuation lines (no blank line, not a new block)
            while j < n and lines[j].strip() and not _starts_block(lines, j):
                if _SETEXT.match(lines[j]):
                    break
                item.append(lines[j].strip())
                j += 1
            text = "\n".join(item).strip()
            if text:
                blocks.append(
                    Block(
                        type="list_item",
                        text=text,
                        heading_path=path(),
                        meta={
                            "depth": depth,
                            "ordered": marker[0].isdigit(),
                            "marker": marker,
                        },
                    )
                )
            i = j
            continue

        # paragraph (possibly a setext heading)
        list_indents = []
        para: list[str] = []
        j = i
        setext_level = 0
        while j < n and lines[j].strip():
            if para:
                sm = _SETEXT.match(lines[j])
                if sm:
                    setext_level = 1 if sm.group(1)[0] == "=" else 2
                    j += 1
                    break
                if _starts_block(lines, j):
                    break
            qm = _QUOTE.match(lines[j])
            para.append((qm.group(1) if qm else lines[j]).strip())
            j += 1
        text = "\n".join(para).strip()
        if setext_level and text:
            add_heading(setext_level, " ".join(p for p in para if p))
        elif text:
            blocks.append(Block(type="paragraph", text=text, heading_path=path()))
        i = max(j, i + 1)

    if not blocks:
        raise IngestError("EMPTY_EXTRACTION", "The file has no readable text.")
    return assemble(blocks)
