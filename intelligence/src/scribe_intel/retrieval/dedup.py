"""Context de-duplication (DESIGN B4 step 7).

POLICY (two separate notions; do not confuse them):
  * INDEXING keeps identical paragraphs in different places as distinct chunks (own spans,
    own citations). Nothing here changes the index.
  * For the CONTEXT SENT TO THE MODEL, the same `content_hash` twice in one result set is
    redundant tokens. We keep the BEST-RANKED occurrence (input order = current rank order) and
    record the rest on it: `duplicates` (chunk ids) and `also_in` (doc name, source, spans) so a
    citation can still list every location. The kept item's own `spans` stay its own (a span
    belongs to the text it locates); other locations live in `also_in`.
  * When parents will be expanded (`merge_parents=True`), at most ONE child per `parent_id`
    survives: later siblings are merged into the best-ranked sibling (`merged_children`), and
    `merged_spans` unions all sibling spans (sorted, de-duplicated). If the parent turns out
    not to fit/load, packing.expand_parents un-merges them again so no evidence is silently lost.
Hash duplicates are resolved BEFORE parent merging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from .types import RankedCandidate, SpanRef


@dataclass(frozen=True)
class Provenance:
    chunk_id: UUID
    doc_name: str
    source_id: int
    source_version_id: UUID
    spans: tuple[SpanRef, ...]


@dataclass
class DedupedItem:
    ranked: RankedCandidate
    duplicates: list[UUID] = field(default_factory=list)  # same content_hash, elsewhere
    also_in: list[Provenance] = field(default_factory=list)
    merged_children: list[RankedCandidate] = field(default_factory=list)  # same-parent siblings
    merged_spans: list[SpanRef] = field(default_factory=list)  # own + sibling spans

    @property
    def chunk_id(self) -> UUID:
        return self.ranked.chunk_id


def _union_spans(*groups: list[SpanRef]) -> list[SpanRef]:
    seen: dict[SpanRef, None] = {}
    for g in groups:
        for s in g:
            seen.setdefault(s, None)
    return sorted(seen, key=lambda s: (s.block_index, s.char_start, s.char_end, str(s.block_id)))


def dedup(ranked: list[RankedCandidate], *, merge_parents: bool = True) -> list[DedupedItem]:
    # 1. exact content_hash duplicates
    kept: list[DedupedItem] = []
    by_hash: dict[str, DedupedItem] = {}
    seen_ids: set[UUID] = set()
    for rc in ranked:
        if rc.chunk_id in seen_ids:
            continue
        seen_ids.add(rc.chunk_id)
        c = rc.candidate
        first = by_hash.get(c.content_hash)
        if first is None:
            item = DedupedItem(ranked=rc)
            by_hash[c.content_hash] = item
            kept.append(item)
        else:
            first.duplicates.append(rc.chunk_id)
            first.also_in.append(Provenance(rc.chunk_id, c.doc_name, c.source_id, c.source_version_id, tuple(c.spans)))
    for it in kept:
        it.merged_spans = _union_spans(it.ranked.candidate.spans)
    if not merge_parents:
        return kept

    # 2. one child per parent
    out: list[DedupedItem] = []
    by_parent: dict[UUID, DedupedItem] = {}
    for it in kept:
        pid = it.ranked.candidate.parent_id
        head = by_parent.get(pid) if pid is not None else None
        if head is None:
            if pid is not None:
                by_parent[pid] = it
            out.append(it)
        else:
            head.merged_children.append(it.ranked)
            head.merged_spans = _union_spans(head.merged_spans, it.merged_spans)
            head.duplicates.extend(it.duplicates)
            head.also_in.extend(it.also_in)
    return out
