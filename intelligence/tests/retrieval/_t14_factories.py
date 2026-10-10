"""Synthetic candidate builders for the DB-free T14 tests."""

from __future__ import annotations

import hashlib
import uuid

from scribe_intel.retrieval.types import Candidate, SpanRef

SV = uuid.UUID(int=1)
GEN = uuid.UUID(int=2)


def cid(n: int) -> uuid.UUID:
    return uuid.UUID(int=0x1000 + n)


def span(n: int = 0, start: int = 0, end: int = 10) -> SpanRef:
    return SpanRef(block_id=uuid.UUID(int=0x5000 + n), block_index=n, char_start=start, char_end=end, page=1, bbox=None)


def cand(n: int, rank: int, method: str = "dense", *, content: str | None = None, parent: int | None = None,
         doc: str = "doc.pdf", score: float = 0.5, heading: str | None = None, source_id: int = 1,
         spans: list | None = None) -> Candidate:
    content = f"text of chunk {n}" if content is None else content
    return Candidate(
        chunk_id=cid(n), parent_id=None if parent is None else uuid.UUID(int=0x9000 + parent), source_id=source_id,
        source_version_id=SV, generation_id=GEN, doc_name=doc, content=content, heading_text=heading,
        content_hash=hashlib.sha256(content.encode()).hexdigest(), token_count=None, method=method,  # type: ignore[arg-type]
        rank=rank, score=score, spans=[span(n)] if spans is None else spans,
    )


def pid(n: int) -> uuid.UUID:
    return uuid.UUID(int=0x9000 + n)
