"""Plain-text parser.

Paragraphs are split on blank lines and keep their internal line breaks
verbatim (only line endings and trailing spaces are normalised), so quote
checks against the source hold. Headings are detected conservatively:

* a single line underlined with ``===`` or ``---`` (3+ chars), level 1 / 2;
* a standalone ALL-CAPS line (>= 3 letters, <= 80 chars, no final period);
* a standalone short line (<= 8 words, <= 60 chars) ending with ``:`` that is
  followed by more text.
"""

from __future__ import annotations

import re

from .errors import IngestError
from .model import Block, ParsedDocument, assemble

_UNDERLINE = re.compile(r"^(=|-){3,}$")


def decode_text(data: bytes) -> str:
    """UTF-8 (with or without BOM); latin-1 fallback never fails."""
    try:
        s = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        s = data.decode("latin-1")
    return s.replace("\x00", "")


def normalize_lines(s: str) -> list[str]:
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return [ln.rstrip(" \t\f\v") for ln in s.split("\n")]


def _is_caps_heading(t: str) -> bool:
    letters = [c for c in t if c.isalpha()]
    return (
        len(letters) >= 3
        and len(t) <= 80
        and not any(c.islower() for c in letters)
        and not t.endswith(".")
    )


def _is_colon_heading(t: str) -> bool:
    return t.endswith(":") and len(t) <= 60 and len(t.split()) <= 8 and len(t) > 1


def parse_text(data: bytes) -> ParsedDocument:
    lines = normalize_lines(decode_text(data))
    # group into paragraphs (list of lines) split on blank lines
    paras: list[list[str]] = []
    cur: list[str] = []
    for ln in lines:
        if ln.strip() == "":
            if cur:
                paras.append(cur)
                cur = []
        else:
            cur.append(ln)
    if cur:
        paras.append(cur)

    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []
    structural_level = 0

    def push(level: int, title: str) -> None:
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        blocks.append(
            Block(type="heading", text=title, heading_path=[t for _, t in stack])
        )

    for i, p in enumerate(paras):
        has_next = i + 1 < len(paras)
        if len(p) == 2 and _UNDERLINE.match(p[1].strip()) and p[0].strip():
            level = 1 if p[1].strip()[0] == "=" else 2
            structural_level = level
            push(level, p[0].strip())
        elif len(p) == 1 and _UNDERLINE.match(p[0].strip()):
            continue  # stray rule
        elif len(p) == 1 and _is_caps_heading(p[0].strip()):
            structural_level = 1
            push(1, p[0].strip())
        elif len(p) == 1 and has_next and _is_colon_heading(p[0].strip()):
            push(min(structural_level + 1, 6), p[0].strip())
        else:
            blocks.append(
                Block(type="paragraph", text="\n".join(p), heading_path=[t for _, t in stack])
            )
    if not blocks:
        raise IngestError("EMPTY_EXTRACTION", "The file has no readable text.")
    return assemble(blocks)
