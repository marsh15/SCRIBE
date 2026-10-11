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
from typing import Mapping
from uuid import UUID

import psycopg

from ..packing import ParentContent
from ..parents import load_parents
from ..pipeline import run_pipeline
from ..profiles import SEED_PROFILES, RetrievalProfile
from ..registry import (STRUCTURAL_INDEX, Handler, RetrieveContext, StrategyResult, _first, _release, register)
from ..search import resolve_index_profile, run_retrievers, snapshot_tx


class ParentPrefetchFailed(RuntimeError):
    """Raised by the parent loader when the in-snapshot prefetch failed (pipeline degrades)."""


def make_handler(p: RetrievalProfile) -> Handler:
    cfg = p.config

    async def handler(ctx: RetrieveContext) -> StrategyResult:
        embed: asyncio.Future | None = None
        if cfg.dense_top_k is not None:
            # embedding starts before the snapshot opens and overlaps the connection checkout
            embed = asyncio.ensure_future(_first(ctx.embedder_for(1536).embed_queries([ctx.query])))
        prefetched: Mapping[UUID, ParentContent] | None = None
        prefetch_ms = 0
        try:
            async with snapshot_tx(ctx.pool, ctx.workspace_id) as conn:
                prof = await resolve_index_profile(conn, STRUCTURAL_INDEX)
                snap = await run_retrievers(
                    conn, ctx.workspace_id, ctx.query, query_vec=embed, index_profile_id=prof,
                    source_ids=ctx.source_ids, dense_k=cfg.dense_top_k or 0, lexical_k=cfg.lexical_top_k or 0,
                    dense_mode="auto")
                if cfg.expand_parents:
                    t = time.monotonic()
                    pids = list(dict.fromkeys(
                        c.parent_id for c in (*snap.dense, *snap.lexical) if c.parent_id is not None))
                    try:
                        prefetched = await load_parents(conn, ctx.workspace_id, prof, pids)
                    except psycopg.Error:
                        prefetched = None  # loader below raises -> parent_expansion_unavailable
                    prefetch_ms = int((time.monotonic() - t) * 1000)
        finally:
            if embed is not None:
                _release(embed)

        reranker = ctx.reranker_for(cfg.rerank_model) if cfg.rerank and cfg.rerank_model else None

        async def parent_loader(ids: list[UUID]) -> Mapping[UUID, ParentContent]:
            if prefetched is None:
                raise ParentPrefetchFailed("parent prefetch failed inside the snapshot")
            return {i: prefetched[i] for i in ids if i in prefetched}

        res = await run_pipeline(p, ctx.query, {"dense": snap.dense, "lexical": snap.lexical},
                                 reranker=reranker, parent_loader=parent_loader if cfg.expand_parents else None)

        stage_ms = dict(snap.stage_ms)
        if cfg.expand_parents:
            stage_ms["parents_prefetch"] = prefetch_ms
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

    return handler


for _p in SEED_PROFILES:
    register(_p.name)(make_handler(_p))
