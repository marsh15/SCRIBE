"""Shared retrieval types (Design B2/B4). Owned by the lead: T13 and T14 import, never redefine.

A `Candidate` is one child chunk surfaced by one retriever (dense or lexical). The fusion/rerank
stages return `RankedCandidate`s that keep every per-stage rank/score so the trace table
(`retrieval_candidates`) and the inspector can show what each stage did (INV-10: scores from
different rankers are never added; only ranks are fused).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

Method = Literal["dense", "lexical", "dense-legacy"]


@dataclass(frozen=True)
class SpanRef:
    """Where a chunk's text came from (chunk_source_spans joined to document_blocks)."""

    block_id: UUID
    block_index: int
    char_start: int
    char_end: int
    page: int | None
    bbox: tuple[float, float, float, float] | None
    ocr: bool = False
    row_number: int | None = None


@dataclass
class Candidate:
    chunk_id: UUID
    parent_id: UUID | None
    source_id: int
    source_version_id: UUID
    generation_id: UUID
    doc_name: str
    content: str
    heading_text: str | None
    content_hash: str
    token_count: int | None
    method: Method
    rank: int  # 1-based within the retriever's own list
    score: float  # retriever-native score; never comparable across methods
    spans: list[SpanRef] = field(default_factory=list)


@dataclass
class RankedCandidate:
    chunk_id: UUID
    candidate: Candidate  # best representative (carries content/spans/provenance)
    dense_rank: int | None = None
    dense_score: float | None = None
    lexical_rank: int | None = None
    lexical_score: float | None = None
    fusion_rank: int | None = None
    fusion_score: float | None = None  # RRF value (derived from ranks only)
    rerank_rank: int | None = None
    rerank_score: float | None = None
    selected: bool = False


@dataclass
class PackedItem:
    """One entry of the evidence context handed to the model (before E-labels, T25)."""

    chunk_id: UUID
    parent_id: UUID | None
    text: str  # child content or expanded parent content
    heading_text: str | None
    doc_name: str
    source_id: int
    source_version_id: UUID
    spans: list[SpanRef]
    token_count: int
    rerank_score: float | None
    expanded_from_parent: bool = False
