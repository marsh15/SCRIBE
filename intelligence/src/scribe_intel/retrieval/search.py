"""Retrieval orchestration inside ONE snapshot (Design B4 steps 2-4, D12).

Snapshot / latency design
-------------------------
The caller starts the query-embedding task BEFORE opening the snapshot, then enters
``snapshot_tx`` (a REPEATABLE READ READ ONLY transaction, workspace context set first, which also
pins the snapshot). Inside, the lexical query runs while the embedding request is still in flight
(`asyncio.gather` of the two), then the dense query runs with the vector. So the snapshot is held for
max(embed, lexical) + dense + hydrate, never for a serial embed+lexical+dense, and dense and lexical
still see exactly the same set of active generations (D12). The embedding call itself touches no
database state. An embedding failure propagates (RETRIEVAL_FAILED, INV-16): there is no silent
lexical-only fallback.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Sequence

import psycopg

from .dense import DenseMode, DenseResult, dense_search, legacy_dense_search
from .lexical import lexical_search
from .types import Candidate, SpanRef

EmbedFuture = Awaitable[Sequence[float]]


@dataclass
class RetrievalSnapshot:
    dense: list[Candidate] = field(default_factory=list)
    lexical: list[Candidate] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    stage_ms: dict[str, int] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    dense_mode: str | None = None


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


async def snapshot_on(conn: psycopg.AsyncConnection, workspace_id: str) -> None:
    """Begin the snapshot transaction on an autocommit connection. The first statement after BEGIN
    (set_config) is what fixes the REPEATABLE READ snapshot."""
    uuid.UUID(str(workspace_id))
    if not conn.autocommit:
        raise RuntimeError("snapshot transactions need an autocommit connection")
    await conn.execute("begin isolation level repeatable read read only")
    try:
        await conn.execute("select set_config('app.workspace_id', %s, true)", (str(workspace_id),))
    except BaseException:
        await _end(conn)
        raise


async def _end(conn: psycopg.AsyncConnection) -> None:
    try:
        await conn.execute("rollback")  # read-only: nothing to commit
    except psycopg.Error:
        pass


@asynccontextmanager
async def snapshot_tx(pool, workspace_id: str) -> AsyncIterator[psycopg.AsyncConnection]:
    """Check out a pooled connection and hold a REPEATABLE READ READ ONLY transaction (D12)."""
    async with pool.connection() as conn:
        await snapshot_on(conn, workspace_id)
        try:
            yield conn
        finally:
            await _end(conn)


async def resolve_index_profile(conn: psycopg.AsyncConnection, name: str) -> uuid.UUID:
    cur = await conn.execute("select id from index_profiles where name = %s", (name,))
    row = await cur.fetchone()
    if row is None:
        raise LookupError(f"index profile {name!r} not found (run migration 0011)")
    return row[0]


async def hydrate_spans(conn: psycopg.AsyncConnection, workspace_id: str, *lists: list[Candidate]) -> None:
    """One batched query for the spans of every candidate (no N+1)."""
    ids = list({c.chunk_id for lst in lists for c in lst})
    if not ids:
        return
    cur = await conn.execute(
        """select s.chunk_id, b.id, b.block_index, coalesce(s.char_start, 0), coalesce(s.char_end, length(b.text)),
                  coalesce(s.page, b.page), coalesce(s.bbox, b.bbox), b.ocr, b.row_number
           from chunk_source_spans s
           join document_blocks b on b.id = s.block_id and b.workspace_id = %(w)s
           where s.chunk_id = any(%(ids)s) and s.workspace_id = %(w)s
           order by s.chunk_id, b.block_index, s.char_start""",
        {"w": str(workspace_id), "ids": ids})
    by_chunk: dict[uuid.UUID, list[SpanRef]] = {}
    for cid, bid, bidx, cs, ce, page, bbox, ocr, row_no in await cur.fetchall():
        by_chunk.setdefault(cid, []).append(SpanRef(
            block_id=bid, block_index=bidx, char_start=cs, char_end=ce, page=page,
            bbox=tuple(float(x) for x in bbox) if bbox else None, ocr=bool(ocr), row_number=row_no))
    for lst in lists:
        for c in lst:
            c.spans = list(by_chunk.get(c.chunk_id, []))


async def _timed(coro_or_fut: EmbedFuture) -> tuple[Sequence[float], int]:
    t = time.monotonic()
    vec = await coro_or_fut
    return vec, _ms(t)


async def run_retrievers(
    conn: psycopg.AsyncConnection, workspace_id: str, query: str, *, query_vec: Sequence[float] | EmbedFuture | None,
    index_profile_id, source_ids: Sequence[int] | None = None, dense_k: int = 40, lexical_k: int = 40,
    dense_mode: DenseMode = "auto",
) -> RetrievalSnapshot:
    """Run the lexical and dense retrievers on `conn`, which must already be inside `snapshot_tx`.

    `query_vec` is a ready vector or an awaitable/Task resolving to one (embedding in flight).
    ``dense_k=0`` / ``lexical_k=0`` skip that retriever (single-method strategies)."""
    t_all = time.monotonic()
    snap = RetrievalSnapshot()
    pending: asyncio.Future | None = None
    vec: Sequence[float] | None = None
    if dense_k > 0:
        if query_vec is None:
            raise ValueError("dense retrieval needs query_vec")
        if isinstance(query_vec, (list, tuple)):
            vec = query_vec
        else:
            pending = asyncio.ensure_future(_timed(query_vec))
    try:
        t = time.monotonic()
        lexical_coro = lexical_search(conn, workspace_id, query, index_profile_id=index_profile_id,
                                      source_ids=source_ids, k=lexical_k)
        if pending is not None:
            # return_exceptions: never leave the lexical query running on `conn` while we unwind.
            lex, emb = await asyncio.gather(lexical_coro, pending, return_exceptions=True)
            for res in (lex, emb):
                if isinstance(res, BaseException):
                    raise res
            snap.lexical = lex
            vec, snap.stage_ms["embed"] = emb
        else:
            snap.lexical = await lexical_coro
        snap.stage_ms["lexical_plus_embed_wait"] = _ms(t)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
    if dense_k > 0:
        t = time.monotonic()
        res: DenseResult = await dense_search(conn, workspace_id, vec, index_profile_id=index_profile_id,
                                              source_ids=source_ids, k=dense_k, mode=dense_mode)
        snap.dense, snap.dense_mode = res.candidates, res.mode_used
        snap.degraded.extend(res.degraded)
        snap.stage_ms["dense"] = _ms(t)
    t = time.monotonic()
    await hydrate_spans(conn, workspace_id, snap.dense, snap.lexical)
    snap.stage_ms["hydrate"] = _ms(t)
    snap.stage_ms["total"] = _ms(t_all)
    snap.counts = {"dense": len(snap.dense), "lexical": len(snap.lexical)}
    return snap


async def run_legacy_dense(
    conn: psycopg.AsyncConnection, workspace_id: str, *, query_vec: Sequence[float] | EmbedFuture,
    index_profile_id, source_ids: Sequence[int] | None = None, threshold: float = 0.3, limit: int = 10,
) -> RetrievalSnapshot:
    t_all = time.monotonic()
    snap = RetrievalSnapshot(dense_mode="exact")
    if isinstance(query_vec, (list, tuple)):
        vec = query_vec
    else:
        vec, snap.stage_ms["embed"] = await _timed(query_vec)
    t = time.monotonic()
    snap.dense = await legacy_dense_search(conn, workspace_id, vec, index_profile_id=index_profile_id,
                                           source_ids=source_ids, threshold=threshold, limit=limit)
    snap.stage_ms["dense"] = _ms(t)
    t = time.monotonic()
    await hydrate_spans(conn, workspace_id, snap.dense)
    snap.stage_ms["hydrate"] = _ms(t)
    snap.stage_ms["total"] = _ms(t_all)
    snap.counts = {"dense": len(snap.dense)}
    return snap
