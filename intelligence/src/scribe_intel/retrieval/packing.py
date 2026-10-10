"""Parent expansion and context packing (DESIGN B4 step 8).

expand_parents policy
  * parent text replaces a child's text only if parent_tokens <= budget * max_parent_fraction
    (default 0.5, so a single parent can never crowd out the whole context) AND the running total
    of earlier entries plus this parent still fits in the budget.
  * The CHILD's spans are kept for citation (a parent has no span of its own); when siblings were
    merged by dedup their spans are unioned in. If a parent is missing, too large, or does not
    fit, the child text is kept and merged siblings are re-emitted as their own entries right
    after it (no evidence silently lost).
  * Child text is passed through verbatim, so a table chunk's repeated header row (T11) is
    never altered.

pack policy: greedy in the given (rerank) order; STOP AT THE FIRST ITEM THAT WOULD EXCEED THE
BUDGET (no skip-and-fill-smaller: it would reorder evidence by size and hide the rank cut-off).
Everything from that item on is reported in `dropped` with a reason. `used_tokens <= budget`
always holds; a first item larger than the budget yields an empty context.

Tokens of an item = count_tokens(text) + count_tokens(heading_text) (heading is shown with it).
`PackedItem` (lead-owned) has no provenance fields, so `PackedEntry` wraps it with the dedup
information and `PackedContext` carries entries + budget accounting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping, Sequence
from uuid import UUID

from scribe_ingest.tokens import count_tokens as default_count_tokens

from .dedup import DedupedItem, Provenance
from .types import PackedItem, RankedCandidate, SpanRef

DEFAULT_BUDGET = 6000


@dataclass(frozen=True)
class ParentContent:
    content: str
    heading_text: str | None = None


ParentLoader = Callable[[list[UUID]], Awaitable[Mapping[UUID, "str | ParentContent"]]]


@dataclass
class PackedEntry:
    item: PackedItem
    duplicates: list[UUID] = field(default_factory=list)
    also_in: list[Provenance] = field(default_factory=list)
    merged_chunk_ids: list[UUID] = field(default_factory=list)


@dataclass(frozen=True)
class Dropped:
    chunk_id: UUID
    reason: str  # "exceeds_budget" (first overflow) | "after_budget_stop"
    token_count: int


@dataclass
class PackedContext:
    entries: list[PackedEntry]
    used_tokens: int
    budget_tokens: int
    dropped: list[Dropped] = field(default_factory=list)

    @property
    def items(self) -> list[PackedItem]:
        return [e.item for e in self.entries]


def item_tokens(text: str, heading: str | None, count_tokens: Callable[[str], int]) -> int:
    return count_tokens(text) + (count_tokens(heading) if heading else 0)


def _entry(rc: RankedCandidate, spans: list[SpanRef], count_tokens: Callable[[str], int], *,
           text: str | None = None, heading: str | None = None, parent: bool = False) -> PackedEntry:
    c = rc.candidate
    t = c.content if text is None else text
    h = c.heading_text if heading is None else heading
    return PackedEntry(
        PackedItem(
            chunk_id=c.chunk_id, parent_id=c.parent_id, text=t, heading_text=h, doc_name=c.doc_name,
            source_id=c.source_id, source_version_id=c.source_version_id, spans=list(spans),
            token_count=item_tokens(t, h, count_tokens), rerank_score=rc.rerank_score,
            expanded_from_parent=parent,
        )
    )


def to_entries(items: Sequence[DedupedItem], count_tokens: Callable[[str], int] = default_count_tokens) -> list[PackedEntry]:
    """No parent expansion: children as-is (merged siblings, if any, re-emitted as own entries)."""
    out: list[PackedEntry] = []
    for d in items:
        e = _entry(d.ranked, d.ranked.candidate.spans, count_tokens)
        e.duplicates, e.also_in = list(d.duplicates), list(d.also_in)
        out.append(e)
        out.extend(_entry(m, m.candidate.spans, count_tokens) for m in d.merged_children)
    return out


async def expand_parents(
    items: Sequence[DedupedItem],
    parent_loader: ParentLoader,
    *,
    budget_tokens: int = DEFAULT_BUDGET,
    count_tokens: Callable[[str], int] = default_count_tokens,
    max_parent_fraction: float = 0.5,
) -> list[PackedEntry]:
    pids = list(dict.fromkeys(d.ranked.candidate.parent_id for d in items if d.ranked.candidate.parent_id is not None))
    parents = dict(await parent_loader(pids)) if pids else {}
    out: list[PackedEntry] = []
    used = 0
    for d in items:
        c = d.ranked.candidate
        raw = parents.get(c.parent_id) if c.parent_id is not None else None
        entry: PackedEntry | None = None
        if raw is not None:
            pc = raw if isinstance(raw, ParentContent) else ParentContent(raw)
            heading = pc.heading_text if pc.heading_text is not None else c.heading_text
            cost = item_tokens(pc.content, heading, count_tokens)
            if cost <= budget_tokens * max_parent_fraction and used + cost <= budget_tokens:
                entry = _entry(d.ranked, d.merged_spans or c.spans, count_tokens, text=pc.content,
                               heading=heading, parent=True)
                entry.merged_chunk_ids = [m.chunk_id for m in d.merged_children]
                d_merged: list[RankedCandidate] = []
        if entry is None:
            entry = _entry(d.ranked, c.spans, count_tokens)
            d_merged = list(d.merged_children)
        entry.duplicates, entry.also_in = list(d.duplicates), list(d.also_in)
        out.append(entry)
        used += entry.item.token_count
        for m in d_merged:
            me = _entry(m, m.candidate.spans, count_tokens)
            out.append(me)
            used += me.item.token_count
    return out


def pack(
    entries: Sequence[PackedEntry | PackedItem],
    budget_tokens: int = DEFAULT_BUDGET,
    count_tokens: Callable[[str], int] = default_count_tokens,
) -> PackedContext:
    kept: list[PackedEntry] = []
    dropped: list[Dropped] = []
    used = 0
    stopped = False
    for e in entries:
        if isinstance(e, PackedItem):
            e = PackedEntry(e)
        tokens = item_tokens(e.item.text, e.item.heading_text, count_tokens)
        e.item.token_count = tokens
        if stopped:
            dropped.append(Dropped(e.item.chunk_id, "after_budget_stop", tokens))
        elif used + tokens > budget_tokens:
            stopped = True
            dropped.append(Dropped(e.item.chunk_id, "exceeds_budget", tokens))
        else:
            kept.append(e)
            used += tokens
    return PackedContext(entries=kept, used_tokens=used, budget_tokens=budget_tokens, dropped=dropped)
