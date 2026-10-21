"""Hybrid retrieval profiles A-E (T14): one `/retrieve` strategy per seeded `retrieval_profiles` row.

Flow per request (DESIGN B4):
  1. start the query embedding (only if the profile uses the dense retriever) BEFORE the snapshot;
  2. inside ONE `snapshot_tx`: dense + lexical retrievers, then (profiles that expand parents) a
     prefetch of the parents of every candidate, so children and parents come from the same
     REPEATABLE READ snapshot;
  3. after the snapshot closed (no transaction is held across the network rerank call):
     fusion -> rerank -> dedup -> parent expansion -> pack, via the pure `run_pipeline`.

Degradations never fail the request: no/failed reranker -> `rerank_unavailable` (fused order);
parent prefetch failure -> `parent_expansion_unavailable` (children packed as-is).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Mapping
from uuid import UUID

import psycopg

from ..packing import ParentContent
from ..parents import load_parents
from ..pipeline import run_pipeline
from ..profiles import ALL_PROFILES, RetrievalProfile
from ..registry import (STRUCTURAL_INDEX, Handler, RetrieveContext, StrategyResult, _first, _release, register)
from ..search import RetrievalSnapshot, resolve_index_profile, run_retrievers, snapshot_tx


class ParentPrefetchFailed(RuntimeError):
    """Raised by the parent loader when the in-snapshot prefetch failed (pipeline degrades)."""


@dataclass
class InSnapshot:
    """What one profile read inside the snapshot: retrieved lists plus the prefetched parents."""

    snap: RetrievalSnapshot
    prefetched: Mapping[UUID, ParentContent] | None
    prefetch_ms: int


async def read_in_snapshot(conn, workspace_id: str, query: str, p: RetrievalProfile, prof, *,
                           source_ids: list[int] | None, query_vec) -> InSnapshot:
    """Dense + lexical retrieval (and the parent prefetch for profiles that expand parents) for ONE profile on a
    connection that is already inside `snapshot_tx`. `query_vec` is a vector, an awaitable resolving to one, or
    None (lexical-only profile)."""
    cfg = p.config
    snap = await run_retrievers(
        conn, workspace_id, query, query_vec=query_vec if cfg.dense_top_k is not None else None, index_profile_id=prof,
        source_ids=source_ids, dense_k=cfg.dense_top_k or 0, lexical_k=cfg.lexical_top_k or 0, dense_mode="auto",
        lexical_mode=cfg.lexical_mode)
    prefetched: Mapping[UUID, ParentContent] | None = None
    prefetch_ms = 0
    if cfg.expand_parents:
        t = time.monotonic()
        pids = list(dict.fromkeys(c.parent_id for c in (*snap.dense, *snap.lexical) if c.parent_id is not None))
        try:
            prefetched = await load_parents(conn, workspace_id, prof, pids)
        except psycopg.Error:
            prefetched = None  # loader below raises -> parent_expansion_unavailable
        prefetch_ms = int((time.monotonic() - t) * 1000)
    return InSnapshot(snap, prefetched, prefetch_ms)


async def finish_profile(p: RetrievalProfile, query: str, read: InSnapshot, reranker) -> StrategyResult:
    """Everything after the snapshot closed: fusion -> rerank -> dedup -> expansion -> pack."""
    cfg = p.config
    snap, prefetched = read.snap, read.prefetched

    async def parent_loader(ids: list[UUID]) -> Mapping[UUID, ParentContent]:
        if prefetched is None:
            raise ParentPrefetchFailed("parent prefetch failed inside the snapshot")
        return {i: prefetched[i] for i in ids if i in prefetched}

    res = await run_pipeline(p, query, {"dense": snap.dense, "lexical": snap.lexical},
                             reranker=reranker, parent_loader=parent_loader if cfg.expand_parents else None)

    stage_ms = dict(snap.stage_ms)
    if cfg.expand_parents:
        stage_ms["parents_prefetch"] = read.prefetch_ms
    stage_ms.update({k: int(v) for k, v in res.stage_ms.items()})
    degraded = list(snap.degraded)
    degraded.extend(d for d in res.degraded if d not in degraded)
    extra: dict = {
        "profile_config_hash": p.config_hash,
        "top_rerank_score": res.answerability_inputs.get("top_rerank_score"),
        "reranker": getattr(reranker, "model", None),
        "reranker_is_fake": bool(getattr(reranker, "is_fake", False)),
        "budget_tokens": res.packed.budget_tokens,
        "used_tokens": res.packed.used_tokens,
    }
    extra.update(res.extra)  # rerank_error (provider error code) when the rerank degraded
    if snap.dense_mode:
        extra["dense_mode"] = snap.dense_mode
    return StrategyResult(ranked=res.ranked, stage_ms=stage_ms, counts={**snap.counts, **res.counts},
                          degraded=degraded, extra=extra, packed=res.packed, profile_name=p.name)


def make_handler(p: RetrievalProfile) -> Handler:
    cfg = p.config

    async def handler(ctx: RetrieveContext) -> StrategyResult:
        embed: asyncio.Future | None = None
        if cfg.dense_top_k is not None:
            # embedding starts before the snapshot opens and overlaps the connection checkout
            embed = asyncio.ensure_future(_first(ctx.embedder_for(1536).embed_queries([ctx.query])))
        try:
            async with snapshot_tx(ctx.pool, ctx.workspace_id) as conn:
                prof = await resolve_index_profile(conn, STRUCTURAL_INDEX)
                read = await read_in_snapshot(conn, ctx.workspace_id, ctx.query, p, prof,
                                              source_ids=ctx.source_ids, query_vec=embed)
        finally:
            if embed is not None:
                _release(embed)

        reranker = ctx.reranker_for(cfg.rerank_model) if cfg.rerank and cfg.rerank_model else None
        return await finish_profile(p, ctx.query, read, reranker)

    return handler


for _p in ALL_PROFILES:
    register(_p.name)(make_handler(_p))
