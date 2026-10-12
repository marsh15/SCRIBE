"""Faithful Python port of the legacy-v1 chunker (`lib/chunking.ts`).

`@langchain/textsplitters` RecursiveCharacterTextSplitter(chunkSize=4000,
chunkOverlap=400, separators=["\\n\\n", "\\n", " ", ""], keepSeparator=true) plus
the metadata pass (chunkIndex, charOffset via indexOf with an advancing search
offset, estimatedPage, positionPercent, section). Used by eval/backfill to
reproduce old chunks.

Parity with JavaScript semantics (all verified against fixtures produced by the
real TS chunker, tests/ingestion/test_legacy_chunker.py):

* Lengths and offsets are UTF-16 code units, like JS `String.length`/`indexOf`.
  Internally the text is held as a Python str whose characters are UTF-16 code
  units (astral characters become surrogate pairs), so slicing, `""`-splitting
  (which can cut a surrogate pair in half, as JS does) and offsets all match.
  Public inputs/outputs are normal strings; `char_offset`/`char_length` are in
  UTF-16 code units. A chunk cut through a surrogate pair is returned with a lone
  surrogate, as in JS.
* `String.prototype.trim` strips the JS whitespace + line-terminator set, not
  Python's (`\\x1c-\\x1f`, `\\x85` are NOT trimmed; `\\ufeff` IS).
* `Math.round` rounds half up (Python's `round` is banker's).

Known divergences: none found. (`console.warn` for oversized chunks is dropped.)
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

CHUNK_SIZE = 4000
CHUNK_OVERLAP = 400
SEPARATORS = ("\n\n", "\n", " ", "")

# ECMAScript WhiteSpace + LineTerminator
_JS_WS = frozenset(
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def _to_utf16(s: str) -> str:
    """Re-encode so each Python char is one UTF-16 code unit."""
    if s.isascii():
        return s
    raw = s.encode("utf-16-le", "surrogatepass")
    units = struct.unpack(f"<{len(raw) // 2}H", raw)
    return "".join(map(chr, units))


def _from_utf16(s: str) -> str:
    if s.isascii():
        return s
    raw = struct.pack(f"<{len(s)}H", *map(ord, s))
    return raw.decode("utf-16-le", "surrogatepass")


def _js_trim(s: str) -> str:
    a, b = 0, len(s)
    while a < b and s[a] in _JS_WS:
        a += 1
    while b > a and s[b - 1] in _JS_WS:
        b -= 1
    return s[a:b]


def _split_on_separator(text: str, sep: str) -> list[str]:
    """`splitOnSeparator` with keepSeparator=true: split before every separator."""
    if sep == "":
        return list(text)
    cuts = []
    i = text.find(sep)
    while i != -1:
        if i > 0:
            cuts.append(i)
        i = text.find(sep, i + 1)  # overlapping matches count, as with a lookahead
    parts = []
    prev = 0
    for c in cuts:
        parts.append(text[prev:c])
        prev = c
    parts.append(text[prev:])
    return [p for p in parts if p != ""]


def _join_docs(docs: list[str], separator: str) -> str | None:
    text = _js_trim(separator.join(docs))
    return None if text == "" else text


def _merge_splits(splits: list[str], separator: str) -> list[str]:
    docs: list[str] = []
    current: list[str] = []
    total = 0
    for d in splits:
        n = len(d)
        if total + n + len(current) * len(separator) > CHUNK_SIZE:
            if current:
                doc = _join_docs(current, separator)
                if doc is not None:
                    docs.append(doc)
                while total > CHUNK_OVERLAP or (
                    total + n + len(current) * len(separator) > CHUNK_SIZE and total > 0
                ):
                    total -= len(current[0])
                    current.pop(0)
        current.append(d)
        total += n
    doc = _join_docs(current, separator)
    if doc is not None:
        docs.append(doc)
    return docs


def _split_text(text: str, separators: tuple[str, ...]) -> list[str]:
    final: list[str] = []
    separator = separators[-1]
    new_separators: tuple[str, ...] | None = None
    for i, s in enumerate(separators):
        if s == "":
            separator = s
            break
        if s in text:
            separator = s
            new_separators = separators[i + 1 :]
            break
    splits = _split_on_separator(text, separator)
    good: list[str] = []
    for s in splits:
        if len(s) < CHUNK_SIZE:
            good.append(s)
        else:
            if good:
                final.extend(_merge_splits(good, ""))
                good = []
            if new_separators is None:
                final.append(s)
            else:
                final.extend(_split_text(s, new_separators))
    if good:
        final.extend(_merge_splits(good, ""))
    return final


@dataclass(frozen=True)
class LegacyChunk:
    content: str
    chunk_index: int
    total_chunks: int
    file_name: str | None
    estimated_page: int | None
    total_pages: int | None
    position_percent: int
    section: int
    char_offset: int | None  # UTF-16 code units, None when not found
    char_length: int  # UTF-16 code units


def _js_round(x: float) -> int:
    return math.floor(x + 0.5)


def chunk_content(
    content: str, *, file_name: str | None = None, num_pages: int | None = None
) -> list[LegacyChunk]:
    """Port of `chunkContent(content, {fileName, numPages})`."""
    u = _to_utf16(content)
    trimmed = _js_trim(u)
    raw_chunks = _split_text(trimmed, SEPARATORS)
    total_length = len(trimmed)
    search_offset = 0
    out: list[LegacyChunk] = []
    for index, chunk in enumerate(raw_chunks):
        start = u.find(chunk, search_offset)
        if start >= 0:
            search_offset = start + 1
        position_percent = _js_round(start / total_length * 100) if start >= 0 else 0
        estimated_page = None
        if num_pages and start >= 0:
            estimated_page = max(1, math.ceil(start / total_length * num_pages))
        before = u[:start] if start >= 0 else ""
        out.append(
            LegacyChunk(
                content=_from_utf16(chunk),
                chunk_index=index,
                total_chunks=len(raw_chunks),
                file_name=file_name,
                estimated_page=estimated_page,
                total_pages=num_pages,
                position_percent=position_percent,
                section=before.count("\n\n") + 1,
                char_offset=start if start >= 0 else None,
                char_length=len(chunk),
            )
        )
    return out
