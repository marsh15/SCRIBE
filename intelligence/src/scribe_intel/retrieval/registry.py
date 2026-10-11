"""Strategy registry for POST /retrieve (T13 registers three single-retriever strategies;
T14 adds the hybrid profiles A-E by dropping a module into `retrieval/strategies/`).

A handler is ``async def handler(ctx: RetrieveContext) -> StrategyResult``. It owns its own
snapshot (``snapshot_tx``), embedding and fusion; the route only validates, calls the handler,
persists the trace and serialises. ``ranked`` is the handler's full output in final order (the route
returns the first ``ctx.limit`` of them and flags those as ``selected`` in the trace).

Registering from T14::

    from scribe_intel.retrieval.registry import register, RetrieveContext, StrategyResult

    @register("hybrid-a")
    async def hybrid_a(ctx: RetrieveContext) -> StrategyResult: ...
"""

from __future__ import annotations

import asyncio
import importlib
import pkgutil
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Sequence

from scribe_ingest.embedding import Embedder

from . import strategies
from .dense import DenseMode
from .packing import PackedContext
from .rerank import Reranker
from .search import RetrievalSnapshot, resolve_index_profile, run_legacy_dense, run_retrievers, snapshot_tx
from .types import Candidate, RankedCandidate

STRUCTURAL_INDEX = "structural-v1"
LEGACY_INDEX = "legacy-v1"
DEFAULT_PROFILE = "dense-exact"
CANDIDATE_POOL = 40  # D: dense/lexical top-k


@dataclass
class RetrieveContext:
    pool: Any  # AsyncConnectionPool (anything with .connection())
    workspace_id: str
    query: str
    source_ids: list[int] | None
    limit: int
    embedder_for: Callable[[int], Embedder]  # dims -> embedder (1536 for v2, 3072 for legacy)
    # rerank model name -> reranker (None = no reranker available); used by profiles that rerank
    reranker_for: Callable[[str], Reranker | None] = lambda model: None


@dataclass
class StrategyResult:
    ranked: list[RankedCandidate]
    stage_ms: dict[str, int] = field(default_factory=dict)
    counts: dict[str, Any] = field(default_factory=dict)
    degraded: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    packed: PackedContext | None = None  # evidence context (hybrid profiles); None for T13 strategies
    profile_name: str | None = None  # retrieval_profiles.name when the strategy is a seeded profile


Handler = Callable[[RetrieveContext], Awaitable[StrategyResult]]
_REGISTRY: dict[str, Handler] = {}
_discovered = False


def register(name: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        if name in _REGISTRY and _REGISTRY[name] is not fn:
            raise ValueError(f"retrieval strategy {name!r} is already registered")
        _REGISTRY[name] = fn
        return fn

    return deco


def discover_strategies() -> None:
    """Import every module of `retrieval/strategies/` once (their @register calls run)."""
    global _discovered
    if _discovered:
        return
    for m in pkgutil.iter_modules(strategies.__path__):
        importlib.import_module(f"{strategies.__name__}.{m.name}")
    _discovered = True


def get_handler(name: str) -> Handler | None:
    discover_strategies()
    return _REGISTRY.get(name)


def profile_names() -> list[str]:
    discover_strategies()
    return sorted(_REGISTRY)


def single_method_ranking(cands: Sequence[Candidate]) -> list[RankedCandidate]:
    """Wrap one retriever's list as RankedCandidates (dense-legacy is stored in the dense columns)."""
    out = []
    for c in cands:
        rc = RankedCandidate(chunk_id=c.chunk_id, candidate=c)
        if c.method == "lexical":
            rc.lexical_rank, rc.lexical_score = c.rank, c.score
        else:
            rc.dense_rank, rc.dense_score = c.rank, c.score
        out.append(rc)
    return out


def _result(snap: RetrievalSnapshot, cands: Sequence[Candidate], extra: dict[str, Any] | None = None) -> StrategyResult:
    extra = dict(extra or {})
    if snap.dense_mode:
        extra["dense_mode"] = snap.dense_mode
    return StrategyResult(single_method_ranking(cands), dict(snap.stage_ms), dict(snap.counts),
                          list(snap.degraded), extra)


def _dense_strategy(mode: DenseMode) -> Handler:
    async def handler(ctx: RetrieveContext) -> StrategyResult:
        embedder = ctx.embedder_for(1536)
        # embedding starts before the snapshot opens and overlaps the connection checkout (B4 step 3)
        embed = asyncio.ensure_future(_first(embedder.embed_queries([ctx.query])))
        try:
            async with snapshot_tx(ctx.pool, ctx.workspace_id) as conn:
                prof = await resolve_index_profile(conn, STRUCTURAL_INDEX)
                snap = await run_retrievers(conn, ctx.workspace_id, ctx.query, query_vec=embed, index_profile_id=prof,
                                            source_ids=ctx.source_ids, dense_k=max(CANDIDATE_POOL, ctx.limit),
                                            lexical_k=0, dense_mode=mode)
        finally:
            _release(embed)
        return _result(snap, snap.dense)

    return handler


def _release(task: "asyncio.Future") -> None:
    """Cancel an unfinished embedding task, or mark a finished one's exception as retrieved."""
    if not task.done():
        task.cancel()
    elif not task.cancelled():
        task.exception()


async def _first(coro: Awaitable[list[list[float]]]) -> list[float]:
    return (await coro)[0]


@register("lexical-v1")
async def lexical_v1(ctx: RetrieveContext) -> StrategyResult:
    async with snapshot_tx(ctx.pool, ctx.workspace_id) as conn:
        prof = await resolve_index_profile(conn, STRUCTURAL_INDEX)
        snap = await run_retrievers(conn, ctx.workspace_id, ctx.query, query_vec=None, index_profile_id=prof,
                                    source_ids=ctx.source_ids, dense_k=0, lexical_k=max(CANDIDATE_POOL, ctx.limit))
    return _result(snap, snap.lexical)


@register("legacy-dense")
async def legacy_dense(ctx: RetrieveContext) -> StrategyResult:
    """Scribe 1.0 behaviour over chunk_embeddings_3072: similarity > 0.3, exact, limit as requested."""
    embedder = ctx.embedder_for(3072)
    embed = asyncio.ensure_future(_first(embedder.embed_queries([ctx.query])))
    try:
        async with snapshot_tx(ctx.pool, ctx.workspace_id) as conn:
            prof = await resolve_index_profile(conn, LEGACY_INDEX)
            snap = await run_legacy_dense(conn, ctx.workspace_id, query_vec=embed, index_profile_id=prof,
                                          source_ids=ctx.source_ids, limit=ctx.limit)
    finally:
        _release(embed)
    return _result(snap, snap.dense, {"threshold": 0.3})


register("dense-exact")(_dense_strategy("exact"))
register("dense-ann")(_dense_strategy("ann"))
