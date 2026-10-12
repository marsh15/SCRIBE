"""Pure retrieval pipeline: fusion -> rerank -> dedup -> parent expansion -> pack (B4 steps 5-8).

No DB, no network: retrieved lists, reranker, parent loader and tokenizer are injected. The
caller (T13's /retrieve route) supplies the retrieved lists and persists the trace.
Degradations (INV-16): `rerank_unavailable` (reranker error or none supplied for a rerank
profile; fused order used) and `parent_expansion_unavailable` (loader raised; children packed).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from scribe_ingest.tokens import count_tokens as default_count_tokens

from .dedup import DedupedItem, dedup
from .fusion import rrf_fuse
from .packing import PackedContext, ParentLoader, expand_parents, pack, to_entries
from .profiles import RetrievalProfile
from .rerank import DEGRADED_RERANK, Reranker, rerank_candidates
from .types import Candidate, RankedCandidate

DEGRADED_PARENTS = "parent_expansion_unavailable"


@dataclass
class PipelineResult:
    packed: PackedContext
    ranked: list[RankedCandidate]
    stage_ms: dict[str, float]
    counts: dict[str, int]
    degraded: list[str] = field(default_factory=list)
    answerability_inputs: dict[str, float | None] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)  # e.g. rerank_error (provider error code)


async def run_pipeline(
    profile: RetrievalProfile,
    query: str,
    retrieved: dict[str, list[Candidate]],
    *,
    reranker: Reranker | None,
    parent_loader: ParentLoader | None,
    count_tokens: Callable[[str], int] = default_count_tokens,
) -> PipelineResult:
    cfg = profile.config
    stage_ms: dict[str, float] = {}
    counts: dict[str, int] = {}
    degraded: list[str] = []
    extra: dict[str, Any] = {}

    def lap(name: str, t0: float) -> None:
        stage_ms[name] = round((time.perf_counter() - t0) * 1000, 3)

    t = time.perf_counter()
    lists: dict[str, list[Candidate]] = {}
    for method, top_k in (("dense", cfg.dense_top_k), ("lexical", cfg.lexical_top_k)):
        if top_k is not None:
            lists[method] = sorted(retrieved.get(method, []), key=lambda c: (c.rank, str(c.chunk_id)))[:top_k]
            counts[method] = len(lists[method])
    ranked = rrf_fuse(lists, k=cfg.rrf_k)
    counts["fused"] = len(ranked)
    lap("fusion", t)

    top_score: float | None = None
    if cfg.rerank:
        t = time.perf_counter()
        if reranker is None:
            degraded.append(DEGRADED_RERANK)
        else:
            ranked, deg = await rerank_candidates(
                reranker, query, ranked, cfg.rerank_top_n, count_tokens=count_tokens, error_info=extra
            )
            degraded.extend(deg)
            reranked = [r for r in ranked if r.rerank_rank is not None]
            counts["reranked"] = len(reranked)
            if reranked:
                top_score = max(r.rerank_score for r in reranked if r.rerank_score is not None)
        lap("rerank", t)
        # only the reranked head is context-eligible; the un-reranked tail is a trace-only fallback
        eligible = [r for r in ranked if r.rerank_rank is not None] or ranked[: cfg.rerank_top_n]
    else:
        eligible = ranked

    t = time.perf_counter()
    if cfg.dedup:
        items = dedup(eligible, merge_parents=cfg.expand_parents)
    else:
        items = [DedupedItem(ranked=r, merged_spans=list(r.candidate.spans)) for r in eligible]
    counts["after_dedup"] = len(items)
    lap("dedup", t)

    t = time.perf_counter()
    if cfg.expand_parents and parent_loader is not None:
        try:
            entries = await expand_parents(items, parent_loader, budget_tokens=cfg.budget_tokens, count_tokens=count_tokens)
        except Exception:  # noqa: BLE001 - loader is DB-backed; degrade, never fail retrieval
            degraded.append(DEGRADED_PARENTS)
            entries = to_entries(items, count_tokens)
    else:
        entries = to_entries(items, count_tokens)
    lap("expand", t)

    t = time.perf_counter()
    packed = pack(entries, cfg.budget_tokens, count_tokens)
    lap("pack", t)
    counts["packed"] = len(packed.entries)
    counts["dropped"] = len(packed.dropped)

    in_context = {e.item.chunk_id for e in packed.entries}
    for e in packed.entries:
        in_context.update(e.merged_chunk_ids)
        in_context.update(e.duplicates)  # hash duplicates are represented by the kept chunk's text
    for r in ranked:
        r.selected = r.chunk_id in in_context

    return PipelineResult(
        packed=packed, ranked=ranked, stage_ms=stage_ms, counts=counts, degraded=degraded,
        answerability_inputs={"top_rerank_score": top_score}, extra=extra,
    )
