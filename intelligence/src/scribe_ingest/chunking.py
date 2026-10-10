"""Structural chunker (DESIGN D8, D9, INV-7; profile `structural-v1`).

Input: a `ParsedDocument`. Output: a `ChunkSet` of parent and child chunks that
share ONE `chunk_index` sequence (T08: UNIQUE(generation_id, chunk_index)). A
parent is numbered immediately before its children, so a parent's children are
the contiguous indexes that follow it.

Rules
-----
* Furniture blocks are ignored. Children never cross a heading boundary: a child
  holds blocks of exactly one `heading_path`. One deliberate exception: a heading
  block that has no body of its own and is immediately followed by a *descendant*
  heading ("# A" then "## B") is carried forward into the first child of the
  descendant section instead of becoming a heading-only chunk. (Same carry-forward
  when a heading plus the next block does not fit: the heading rides in the first
  piece of that block's split.)
* Consecutive small blocks of one section are packed into one child up to
  `child_max_tokens` (content = block texts joined by "\\n\\n"). Identical
  paragraphs in different places are never deduplicated: each becomes its own
  chunk with its own spans (`content_hash` may be equal, spans never are).
* A block over the limit is split at sentence boundaries, then by words (and, for
  a single run with no whitespace, by characters). Pieces of the same split block
  overlap by <= `overlap_tokens` (word/sentence aligned); nothing else overlaps.
* Tables are split by ROWS; every piece repeats the header (first line, plus the
  markdown separator line when present - this is exactly `block.meta["header"]`
  for the markdown/PDF parsers). A single oversized row is split by words and
  still repeats the header. Code blocks are split by lines (an oversized line is
  split by words). Neither gets overlap between row/line groups.
* Spans (`Span`) point into BLOCK text: `block.text[char_start:char_end]`. A
  child spanning several blocks has one span per block piece. A table piece has
  a header span and a rows span (merged into one when contiguous). `bbox`/`page`
  are those of the whole block (no per-piece geometry).
* Parent = section-level grouping of children: same top-level heading, up to
  `parent_target_tokens` (sum of child token counts, overlap included). A document
  without headings groups sequential children. Parent content is rebuilt from the
  union of its children's spans, so overlap is not duplicated.

Token limit: every child is <= `child_max_tokens` except when the budget cannot
be met, i.e. a table header (+ carried heading) that alone consumes most of the
budget. Pieces then still make progress (at least one word/row) and may exceed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import cached_property
from typing import Callable, Literal

from scribe_eval.normalize import normalize

from .model import BLOCK_SEPARATOR, Block, ParsedDocument
from .tokens import TOKENIZER_ID, count_tokens

__all__ = ["STRUCTURAL_V1", "Chunk", "ChunkProfile", "ChunkSet", "Span", "chunk_document"]


@dataclass(frozen=True)
class ChunkProfile:
    name: str
    tokenizer_id: str = TOKENIZER_ID
    child_max_tokens: int = 384
    overlap_tokens: int = 48
    parent_target_tokens: int = 1536

    @property
    def config_hash(self) -> str:
        canonical = json.dumps(
            {
                "name": self.name,
                "tokenizer_id": self.tokenizer_id,
                "child_max_tokens": self.child_max_tokens,
                "overlap_tokens": self.overlap_tokens,
                "parent_target_tokens": self.parent_target_tokens,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


STRUCTURAL_V1 = ChunkProfile(name="structural-v1")


@dataclass(frozen=True)
class Span:
    block_index: int
    char_start: int  # offsets into Block.text
    char_end: int
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class Chunk:
    kind: Literal["parent", "child"]
    chunk_index: int
    content: str
    heading_path: list[str]
    token_count: int
    spans: list[Span]
    parent_index: int | None = None  # chunk_index of the parent (children only)

    @property
    def heading_text(self) -> str:
        return " > ".join(self.heading_path)

    @property
    def embed_text(self) -> str:
        """What gets embedded (D9): heading path + newline + content."""
        return f"{self.heading_text}\n{self.content}" if self.heading_path else self.content

    @cached_property
    def content_hash(self) -> str:
        return hashlib.sha256(normalize(self.content).encode()).hexdigest()

    @property
    def page_start(self) -> int | None:
        pages = [s.page for s in self.spans if s.page is not None]
        return min(pages) if pages else None

    @property
    def page_end(self) -> int | None:
        pages = [s.page for s in self.spans if s.page is not None]
        return max(pages) if pages else None


@dataclass
class ChunkSet:
    profile: ChunkProfile
    chunks: list[Chunk] = field(default_factory=list)  # parents and children, by chunk_index

    @property
    def parents(self) -> list[Chunk]:
        return [c for c in self.chunks if c.kind == "parent"]

    @property
    def children(self) -> list[Chunk]:
        return [c for c in self.chunks if c.kind == "child"]


# --------------------------------------------------------------------------
# text splitting helpers
# --------------------------------------------------------------------------

_WORD = re.compile(r"\S+\s*")
_SENT_END = re.compile(r"""[.!?]["')\]”’]*$""")
_SEP_ROW = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")

# A leaf piece: (start, end) offsets into the block text.
Range = tuple[int, int]


def _units(text: str, lo: int, hi: int, unit_budget: int) -> tuple[list[int], list[bool]]:
    """Word units of text[lo:hi]: unit start offsets (+ final end) and sentence-start flags."""
    starts: list[int] = []
    sent: list[bool] = []
    prev_word = ""
    prev_ws = ""
    for m in _WORD.finditer(text, lo, hi):
        word = m.group().rstrip()
        # runs with no whitespace that could exceed the budget are cut by characters
        if len(word) * 4 > unit_budget and count_tokens(word) > unit_budget:
            step = max(1, unit_budget // 4)
            for k in range(0, len(word), step):
                sent.append(False if k else _is_sentence_start(prev_word, prev_ws, word, bool(starts)))
                starts.append(m.start() + k)
        else:
            sent.append(_is_sentence_start(prev_word, prev_ws, word, bool(starts)))
            starts.append(m.start())
        prev_word = word
        prev_ws = m.group()[len(word):]
    starts.append(hi)
    return starts, sent


def _is_sentence_start(prev_word: str, prev_ws: str, word: str, has_prev: bool) -> bool:
    if not has_prev:
        return False
    if "\n" in prev_ws:
        return True
    if _SENT_END.search(prev_word) and not word[:1].islower():
        return True
    return False


def _split_ranges(
    text: str,
    lo: int,
    hi: int,
    *,
    max_tokens: int,
    overlap: int,
    prefix_for: Callable[[int], str],
) -> list[Range]:
    """Split text[lo:hi] into sentence/word aligned pieces.

    Piece k is counted together with `prefix_for(k)`; consecutive pieces overlap
    by <= `overlap` tokens. Returned ranges are stripped of surrounding whitespace.
    """
    while lo < hi and text[lo].isspace():
        lo += 1
    while hi > lo and text[hi - 1].isspace():
        hi -= 1
    if lo >= hi:
        return []
    max_prefix = max(count_tokens(prefix_for(0)), count_tokens(prefix_for(1)))
    budget = max(max_tokens - max_prefix, max_tokens // 4, 8)
    b, sent = _units(text, lo, hi, budget)
    n = len(b) - 1  # number of units

    def piece_text(i: int, j: int) -> str:
        return text[b[i] : b[j]].rstrip()

    def tokens(i: int, j: int, k: int) -> int:
        return count_tokens(prefix_for(k) + piece_text(i, j))

    out: list[Range] = []
    i = 0
    k = 0
    while i < n:
        # largest j with tokens(i, j) <= max_tokens (at least i + 1)
        if tokens(i, n, k) <= max_tokens:
            j = n
        else:
            lo_j, hi_j = i + 1, n  # invariant: lo_j fits (or is forced), hi_j does not
            if tokens(i, lo_j, k) > max_tokens:
                hi_j = lo_j
            while hi_j - lo_j > 1:
                mid = (lo_j + hi_j) // 2
                if tokens(i, mid, k) <= max_tokens:
                    lo_j = mid
                else:
                    hi_j = mid
            j = lo_j
            # prefer to end on a sentence boundary when that keeps >= half the piece
            full = tokens(i, j, k)
            for cand in range(j, i, -1):
                if sent[cand] and cand < n and tokens(i, cand, k) * 2 >= full:
                    j = cand
                    break
        out.append((b[i], b[i] + len(piece_text(i, j))))
        if j >= n:
            break
        # next piece starts inside this one by <= overlap tokens
        nxt = j
        if overlap > 0:
            cand = j - 1
            while cand > i and count_tokens(piece_text(cand, j)) <= overlap:
                nxt = cand
                cand -= 1
            sent_nxt = next(
                (c for c in range(nxt, j) if sent[c] and c > i), None
            )
            if sent_nxt is not None and count_tokens(piece_text(sent_nxt, j)) * 2 >= count_tokens(
                piece_text(nxt, j)
            ):
                nxt = sent_nxt
        k += 1
        while nxt < j and tokens(nxt, j + 1, k) > max_tokens:
            nxt += 1
        i = nxt
    return out


# --------------------------------------------------------------------------
# chunk assembly
# --------------------------------------------------------------------------

# Segment of a piece: (block-relative start, end, separator placed before it).
_Seg = tuple[int, int, str]


@dataclass
class _Piece:
    """Content under construction: segments of (block, start, end, sep_before)."""

    path: tuple[str, ...]
    segs: list[tuple[Block, int, int, str]] = field(default_factory=list)
    only_headings: bool = True

    @property
    def text(self) -> str:
        return "".join(sep + b.text[s:e] for b, s, e, sep in self.segs)

    def add(self, block: Block, s: int, e: int, sep: str | None = None) -> None:
        if sep is None:
            sep = BLOCK_SEPARATOR if self.segs else ""
        if self.segs:
            pb, ps, pe, psep = self.segs[-1]
            # merge contiguous ranges of the same block when the gap is exactly the separator
            if pb is block and block.text[pe:s] == sep:
                self.segs[-1] = (pb, ps, e, psep)
                return
        self.segs.append((block, s, e, sep))
        if block.type != "heading":
            self.only_headings = False


def _spans(segs: list[tuple[Block, int, int, str]]) -> list[Span]:
    return [Span(b.index, s, e, b.page, b.bbox) for b, s, e, _ in segs]


class _Builder:
    def __init__(self, profile: ChunkProfile) -> None:
        self.p = profile
        self.pieces: list[_Piece] = []  # closed children, in order

    # -- block handling -------------------------------------------------
    def run(self, doc: ParsedDocument) -> None:
        cur: _Piece | None = None
        for blk in doc.chunkable_blocks:
            if not blk.text.strip():
                continue
            key = tuple(blk.heading_path)
            if cur is not None and cur.path != key:
                if (
                    cur.segs
                    and cur.only_headings
                    and key[: len(cur.path)] == cur.path
                    and len(key) > len(cur.path)
                ):
                    cur.path = key  # carry the bare heading into its first descendant
                else:
                    if cur.segs:
                        self.pieces.append(cur)
                    cur = None
            if cur is None:
                cur = _Piece(path=key)
            cur = self._add_block(cur, blk)
        if cur is not None and cur.segs:
            self.pieces.append(cur)

    def _add_block(self, cur: _Piece, blk: Block) -> _Piece:
        max_t = self.p.child_max_tokens
        full = len(blk.text)
        if not cur.segs:
            if count_tokens(blk.text) <= max_t:
                cur.add(blk, 0, full)
                return cur
        else:
            probe = cur.text + BLOCK_SEPARATOR + blk.text
            if count_tokens(probe) <= max_t:
                cur.add(blk, 0, full)
                return cur
        # does not fit with `cur`
        if cur.segs and not cur.only_headings:
            self.pieces.append(cur)
            cur = _Piece(path=cur.path)
            if count_tokens(blk.text) <= max_t:
                cur.add(blk, 0, full)
                return cur
            lead = ""
        elif cur.segs:  # bare heading(s)
            if count_tokens(blk.text) <= max_t:
                # the block fits alone: the heading stays a tiny chunk of its own
                self.pieces.append(cur)
                cur = _Piece(path=cur.path)
                cur.add(blk, 0, full)
                return cur
            # otherwise it rides along in the first piece of the split
            lead = cur.text + BLOCK_SEPARATOR
        else:
            lead = ""
        heading_segs = list(cur.segs)
        path = cur.path
        pieces = self._split_block(blk, lead)
        for idx, segs in enumerate(pieces):
            pc = _Piece(path=path)
            if idx == 0:
                for hb, hs, he, hsep in heading_segs:
                    pc.segs.append((hb, hs, he, hsep))
                    pc.only_headings = pc.only_headings and hb.type == "heading"
            for s, e, sep in segs:
                if idx == 0 and pc.segs and sep == "":
                    sep = BLOCK_SEPARATOR
                pc.add(blk, s, e, sep)
            self.pieces.append(pc)
        return _Piece(path=path)

    # -- splitting oversized blocks ---------------------------------------
    def _split_block(self, blk: Block, lead: str) -> list[list[_Seg]]:
        if blk.type == "table":
            r = self._split_table(blk, lead)
            if r:
                return r
        elif blk.type == "code":
            return self._split_code(blk, lead)
        ranges = _split_ranges(
            blk.text,
            0,
            len(blk.text),
            max_tokens=self.p.child_max_tokens,
            overlap=self.p.overlap_tokens,
            prefix_for=lambda k: lead if k == 0 else "",
        )
        return [[(s, e, "")] for s, e in ranges]

    def _split_table(self, blk: Block, lead: str) -> list[list[_Seg]] | None:
        text = blk.text
        lines: list[tuple[int, int]] = []
        pos = 0
        for ln in text.split("\n"):
            lines.append((pos, pos + len(ln)))
            pos += len(ln) + 1
        lines = [(s, e) for s, e in lines if text[s:e].strip()]
        if len(lines) < 2:
            return None
        nh = 2 if len(lines) > 2 and _SEP_ROW.match(text[lines[1][0] : lines[1][1]]) and "-" in text[lines[1][0] : lines[1][1]] else 1
        header = lines[:nh]
        rows = lines[nh:]
        h_s, h_e = header[0][0], header[-1][1]
        head_text = text[h_s:h_e]
        max_t = self.p.child_max_tokens

        def prefix(first: bool) -> str:
            return (lead if first else "") + head_text + "\n"

        def segs_for(r_s: int, r_e: int) -> list[_Seg]:
            return [(h_s, h_e, ""), (r_s, r_e, "\n")]

        out: list[list[_Seg]] = []
        group: list[tuple[int, int]] = []

        def flush() -> None:
            if group:
                out.append(segs_for(group[0][0], group[-1][1]))
                group.clear()

        for rs, re_ in rows:
            first = not out
            cand_end = re_
            start = group[0][0] if group else rs
            if count_tokens(prefix(first) + text[start:cand_end]) <= max_t:
                group.append((rs, re_))
                continue
            flush()
            first = not out
            if count_tokens(prefix(first) + text[rs:re_]) <= max_t:
                group.append((rs, re_))
                continue
            # a single oversized row: split by words, header repeated in each piece
            for fs, fe in _split_ranges(
                text,
                rs,
                re_,
                max_tokens=max_t,
                overlap=self.p.overlap_tokens,
                prefix_for=lambda k, f=not out: (lead if (f and k == 0) else "") + head_text + "\n",
            ):
                out.append(segs_for(fs, fe))
        flush()
        return out

    def _split_code(self, blk: Block, lead: str) -> list[list[_Seg]]:
        text = blk.text
        max_t = self.p.child_max_tokens
        lines: list[tuple[int, int]] = []
        pos = 0
        for ln in text.split("\n"):
            lines.append((pos, pos + len(ln)))
            pos += len(ln) + 1
        lines = [(s, e) for s, e in lines if text[s:e].strip()]
        out: list[list[_Seg]] = []
        group: list[tuple[int, int]] = []

        def pre() -> str:
            return lead if not out else ""

        def flush() -> None:
            if group:
                out.append([(group[0][0], group[-1][1], "")])
                group.clear()

        for ls, le in lines:
            start = group[0][0] if group else ls
            if count_tokens(pre() + text[start:le]) <= max_t:
                group.append((ls, le))
                continue
            flush()
            if count_tokens(pre() + text[ls:le]) <= max_t:
                group.append((ls, le))
                continue
            for fs, fe in _split_ranges(
                text,
                ls,
                le,
                max_tokens=max_t,
                overlap=self.p.overlap_tokens,
                prefix_for=lambda k, f=not out: lead if (f and k == 0) else "",
            ):
                out.append([(fs, fe, "")])
        flush()
        return out


def _merge_union(children: list[Chunk], blocks: dict[int, Block]) -> list[tuple[Block, int, int]]:
    per_block: dict[int, list[tuple[int, int]]] = {}
    for c in children:
        for sp in c.spans:
            per_block.setdefault(sp.block_index, []).append((sp.char_start, sp.char_end))
    out: list[tuple[Block, int, int]] = []
    for bi in sorted(per_block):
        blk = blocks[bi]
        merged: list[list[int]] = []
        for s, e in sorted(per_block[bi]):
            if merged and (s <= merged[-1][1] or not blk.text[merged[-1][1] : s].strip()):
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        out.extend((blk, s, e) for s, e in merged)
    return out


def chunk_document(doc: ParsedDocument, profile: ChunkProfile = STRUCTURAL_V1) -> ChunkSet:
    builder = _Builder(profile)
    builder.run(doc)

    blocks = {b.index: b for b in doc.blocks}
    # child candidates (indexes assigned below)
    kids: list[Chunk] = []
    for pc in builder.pieces:
        content = pc.text
        kids.append(
            Chunk(
                kind="child",
                chunk_index=-1,
                content=content,
                heading_path=list(pc.path),
                token_count=count_tokens(content),
                spans=_spans(pc.segs),
            )
        )

    # group children into parents
    groups: list[list[Chunk]] = []
    cur: list[Chunk] = []
    cur_tokens = 0
    cur_top: str | None = None
    for c in kids:
        top = c.heading_path[0] if c.heading_path else None
        if cur and (top != cur_top or cur_tokens + c.token_count > profile.parent_target_tokens):
            groups.append(cur)
            cur, cur_tokens = [], 0
        if not cur:
            cur_top = top
        cur.append(c)
        cur_tokens += c.token_count
    if cur:
        groups.append(cur)

    result = ChunkSet(profile=profile)
    idx = 0
    for g in groups:
        merged = _merge_union(g, blocks)
        content = BLOCK_SEPARATOR.join(b.text[s:e] for b, s, e in merged)
        common: list[str] = []
        for parts in zip(*(c.heading_path for c in g)):
            if len(set(parts)) != 1:
                break
            common.append(parts[0])
        parent = Chunk(
            kind="parent",
            chunk_index=idx,
            content=content,
            heading_path=common,
            token_count=count_tokens(content),
            spans=[Span(b.index, s, e, b.page, b.bbox) for b, s, e in merged],
        )
        result.chunks.append(parent)
        idx += 1
        for c in g:
            c.chunk_index = idx
            c.parent_index = parent.chunk_index
            idx += 1
            result.chunks.append(c)
    return result
