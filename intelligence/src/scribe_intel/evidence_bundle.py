"""Evidence bundles and the answerability gate (T25; DESIGN B2, B4 steps 9-10, D13, D14, INV-9, INV-13, INV-16).

After packing, every packed context entry becomes one `evidence_items` row labelled `E<n>`. The caller (the chat
turn) passes `next_label`, the first number to use, so labels are unique across all searches of one turn; the
response returns the labels assigned and the following free number.

An item records the CITED CHILD span (the child chunk's own text), never the expanded parent text, its hash, the
page range reported by the child's source spans (`None` when no span has a page: pages are never estimated, INV-7)
and the heading path of its first block. Reads of stored quotes go through `evidence.read_evidence_*` only (INV-8).

Answerability gate (D13). State is one of ANSWERABLE | INSUFFICIENT_EVIDENCE | RETRIEVAL_FAILED:
  * zero packed entries                          -> INSUFFICIENT_EVIDENCE (`no_sources` if the workspace has no
                                                    active source at all, else `no_results`)
  * reranker top score present, score <  tau     -> INSUFFICIENT_EVIDENCE (`below_tau`)
  * reranker top score present, score >= tau     -> ANSWERABLE (`ok`)
  * NO reranker score (degraded rerank or a profile without rerank), entries present
                                                 -> ANSWERABLE, `gate_degraded=True` (`no_rerank_score`): the gate
                                                    cannot judge relevance without a reranker score, so it fails
                                                    open and says so (INV-16). It never uses cosine similarity.
  * RETRIEVAL_FAILED is produced by the route when retrieval itself failed (HTTP 503); it is never mixed up with
    INSUFFICIENT_EVIDENCE.
`tau` and `tau_calibrated` come from the profile's `GateConfig`; until T17 calibrates it the placeholder is
uncalibrated and is reported as such (`tau_calibrated=false`).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

import psycopg

from .retrieval.packing import PackedContext
from .retrieval.profiles import GateConfig
from .retrieval.types import RankedCandidate

ANSWERABLE = "ANSWERABLE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
RETRIEVAL_FAILED = "RETRIEVAL_FAILED"
STATES = (ANSWERABLE, INSUFFICIENT_EVIDENCE, RETRIEVAL_FAILED)

LABEL_PREFIX = "E"
MAX_LABEL_START = 100_000


@dataclass(frozen=True)
class Answerability:
    state: str
    reason: str  # ok | below_tau | no_results | no_sources | no_rerank_score | retrieval_failed
    top_rerank_score: float | None
    tau: float
    tau_calibrated: bool
    gate_degraded: bool = False

    def as_json(self) -> dict[str, Any]:
        return {"state": self.state, "reason": self.reason, "top_rerank_score": self.top_rerank_score,
                "tau": self.tau, "tau_calibrated": self.tau_calibrated, "gate_degraded": self.gate_degraded}


def evaluate_gate(gate: GateConfig, *, entries: int, top_rerank_score: float | None,
                  no_sources: bool = False) -> Answerability:
    base = {"top_rerank_score": top_rerank_score, "tau": gate.tau, "tau_calibrated": gate.tau_calibrated}
    if entries == 0:
        return Answerability(INSUFFICIENT_EVIDENCE, "no_sources" if no_sources else "no_results", **base)
    if top_rerank_score is None:
        return Answerability(ANSWERABLE, "no_rerank_score", gate_degraded=True, **base)
    if top_rerank_score < gate.tau:
        return Answerability(INSUFFICIENT_EVIDENCE, "below_tau", **base)
    return Answerability(ANSWERABLE, "ok", **base)


def retrieval_failed(gate: GateConfig) -> Answerability:
    return Answerability(RETRIEVAL_FAILED, "retrieval_failed", None, gate.tau, gate.tau_calibrated)


@dataclass
class ItemDraft:
    label: str
    chunk_id: uuid.UUID
    source_id: int
    source_name: str
    source_version_id: uuid.UUID
    quote: str
    quote_hash: str
    page_start: int | None
    page_end: int | None
    heading_path: list[str] | None = None
    id: uuid.UUID | None = None  # set once persisted


@dataclass
class EvidenceOutcome:
    bundle_id: uuid.UUID
    answerability: Answerability
    items: list[ItemDraft] = field(default_factory=list)
    next_label: int = 1


def quote_hash(quote: str) -> str:
    return hashlib.sha256(quote.encode("utf-8")).hexdigest()


def label_for(n: int) -> str:
    return f"{LABEL_PREFIX}{n}"


def plan_items(packed: PackedContext | None, ranked: Sequence[RankedCandidate], start: int) -> list[ItemDraft]:
    """One draft per packed entry, labelled E<start>, E<start+1>, ... in context order (pure)."""
    if packed is None:
        return []
    by_chunk = {rc.chunk_id: rc.candidate for rc in ranked}
    drafts: list[ItemDraft] = []
    for entry in packed.entries:
        cand = by_chunk.get(entry.item.chunk_id)
        if cand is None:  # cannot happen (packed entries come from ranked); never invent a quote
            continue
        pages = [s.page for s in cand.spans if s.page is not None]
        drafts.append(ItemDraft(
            label=label_for(start + len(drafts)), chunk_id=cand.chunk_id, source_id=cand.source_id,
            source_name=cand.doc_name, source_version_id=cand.source_version_id, quote=cand.content,
            quote_hash=quote_hash(cand.content), page_start=min(pages) if pages else None,
            page_end=max(pages) if pages else None))
    return drafts


_HEADINGS = """
select distinct on (s.chunk_id) s.chunk_id, b.heading_path
from chunk_source_spans s join document_blocks b on b.id = s.block_id and b.workspace_id = %(w)s
where s.workspace_id = %(w)s and s.chunk_id = any(%(c)s)
order by s.chunk_id, b.block_index
"""


async def _heading_paths(conn: psycopg.AsyncConnection, wid: str, chunk_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    if not chunk_ids:
        return {}
    cur = await conn.execute(_HEADINGS, {"w": wid, "c": chunk_ids})
    return {cid: list(hp) for cid, hp in await cur.fetchall() if hp}


async def _workspace_has_sources(conn: psycopg.AsyncConnection, wid: str) -> bool:
    cur = await conn.execute("select exists (select 1 from active_indexes where workspace_id = %s)", (wid,))
    return bool((await cur.fetchone())[0])


async def persist_bundle(conn: psycopg.AsyncConnection, wid: str, trace_id: uuid.UUID, gate: GateConfig,
                         drafts: list[ItemDraft], top_rerank_score: float | None, start: int) -> EvidenceOutcome:
    """Gate + bundle + items in the caller's transaction (the same one that wrote the trace), so a bundle never
    exists without its trace and its items. `conn` already has app.workspace_id set."""
    no_sources = not drafts and not await _workspace_has_sources(conn, wid)
    answer = evaluate_gate(gate, entries=len(drafts), top_rerank_score=top_rerank_score, no_sources=no_sources)
    cur = await conn.execute(
        "insert into evidence_bundles(trace_id, workspace_id, answerability, top_rerank_score) "
        "values (%s,%s,%s,%s) returning id", (trace_id, wid, answer.state, top_rerank_score))
    bundle_id = (await cur.fetchone())[0]
    headings = await _heading_paths(conn, wid, [d.chunk_id for d in drafts])
    for d in drafts:
        d.heading_path = headings.get(d.chunk_id)
        cur = await conn.execute(
            "insert into evidence_items(bundle_id, workspace_id, label, chunk_id, source_version_id, quote, "
            "quote_hash, page_start, page_end, heading_path) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
            (bundle_id, wid, d.label, d.chunk_id, d.source_version_id, d.quote, d.quote_hash, d.page_start,
             d.page_end, d.heading_path))
        d.id = (await cur.fetchone())[0]
    return EvidenceOutcome(bundle_id, answer, drafts, start + len(drafts))


def item_json(d: ItemDraft) -> dict[str, Any]:
    """What the caller needs to cite and render an item. The quote is not returned: it is read back through
    `evidence.read_evidence_items` / POST /evidence/open (INV-8)."""
    return {"id": str(d.id), "label": d.label, "chunk_id": str(d.chunk_id), "source_id": d.source_id,
            "source_name": d.source_name, "source_version_id": str(d.source_version_id),
            "page_start": d.page_start, "page_end": d.page_end, "heading_path": d.heading_path or []}

