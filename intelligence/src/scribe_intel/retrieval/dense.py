"""Dense retrieval over chunk_embeddings_1536 (and the legacy 3072 table), Design B4/D11.

Modes (D11)
* ``exact``: ORDER BY ``(e.embedding <=> q) + 0`` - an expression the HNSW operator class cannot
  serve, so the planner can never choose the index; the scan is a filtered sequential/btree scan
  followed by a top-k sort. No planner GUC is touched, so the join planning stays normal.
* ``ann``: the plain ``ORDER BY e.embedding <=> q`` form that HNSW can serve, inside a
  MATERIALIZED CTE with LIMIT k and a final re-sort (relaxed_order may emit slightly out-of-order
  rows). Sets ``hnsw.ef_search=100`` and ``hnsw.iterative_scan=relaxed_order`` with
  ``set_config(..., true)`` (transaction local) only when pgvector >= 0.8; otherwise it falls back to
  exact and reports the degraded flag ``ann_iterative_scan_unavailable`` (INV-16). Without
  iterative scan an HNSW scan applies the workspace/generation filters AFTER the index returns
  ef_search rows and would silently return fewer than k results, so ann is never used without it.
* ``auto``: exact when the workspace has fewer than 20,000 active child chunks, otherwise ann.

All scope filters are inside the statement before LIMIT (INV-2/3, see queries.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Sequence

import psycopg

from .queries import (CANDIDATE_COLUMNS, SCOPE_JOINS, in_transaction, row_to_candidate, scope_params, scope_where,
                      vec_literal)
from .types import Candidate

DenseMode = Literal["exact", "ann", "auto"]
AUTO_ANN_THRESHOLD = 20_000
EF_SEARCH = 100
DIMS = 1536
LEGACY_DIMS = 3072
LEGACY_THRESHOLD = 0.3
LEGACY_LIMIT = 10
FLAG_ANN_UNAVAILABLE = "ann_iterative_scan_unavailable"


@dataclass
class DenseResult:
    candidates: list[Candidate]
    mode_used: Literal["exact", "ann"]
    degraded: list[str] = field(default_factory=list)


async def pgvector_version(conn: psycopg.AsyncConnection) -> tuple[int, ...] | None:
    cur = await conn.execute("select extversion from pg_extension where extname = 'vector'")
    row = await cur.fetchone()
    if row is None:
        return None
    nums = re.findall(r"\d+", row[0])
    return tuple(int(n) for n in nums[:3]) or None


def supports_iterative_scan(version: tuple[int, ...] | None) -> bool:
    return version is not None and version[:2] >= (0, 8)


async def active_chunk_count(conn: psycopg.AsyncConnection, workspace_id: str, index_profile_id,
                             cap: int = AUTO_ANN_THRESHOLD) -> int:
    """Active child chunks of the workspace for this profile, counted up to `cap` (cheap)."""
    sql = f"""select count(*) from (select 1 from chunks c {SCOPE_JOINS}
              where {scope_where(None)} limit %(cap)s) t"""
    cur = await conn.execute(sql, {**scope_params(workspace_id, index_profile_id, None), "cap": cap})
    return (await cur.fetchone())[0]


def dense_sql(mode: Literal["exact", "ann"], source_filter: bool) -> str:
    where = scope_where([0] if source_filter else None)
    dist = "(e.embedding <=> %(q)s::vector)"
    if mode == "exact":
        return f"""select {CANDIDATE_COLUMNS}, {dist} as dist
from chunk_embeddings_1536 e
  join chunks c on c.id = e.chunk_id
  {SCOPE_JOINS}
  where e.workspace_id = %(w)s and {where}
  order by {dist} + 0, c.id
  limit %(k)s"""
    # ann: the eligibility filter is an uncorrelated `= ANY (ARRAY(subquery))` qual on the embeddings scan itself
    # (an InitPlan, so the planner cannot turn it into a join), so
    # pgvector's iterative scan applies it while walking the graph (a filter living in a join above
    # the index scan would only see ef_search rows). The final join repeats the scope as a guard.
    return f"""with ann as materialized (
  select e.chunk_id, {dist} as dist
  from chunk_embeddings_1536 e
  where e.workspace_id = %(w)s and e.chunk_id = any (array(
    select c.id from chunks c {SCOPE_JOINS} where {where}))
  order by {dist}
  limit %(k)s)
select {CANDIDATE_COLUMNS}, ann.dist
from ann
  join chunks c on c.id = ann.chunk_id
  {SCOPE_JOINS}
  where {where}
  order by ann.dist, c.id"""


async def dense_search(
    conn: psycopg.AsyncConnection, workspace_id: str, query_vec: Sequence[float], *, index_profile_id,
    source_ids: Sequence[int] | None = None, k: int = 40, mode: DenseMode = "auto",
) -> DenseResult:
    if len(query_vec) != DIMS:
        raise ValueError(f"query vector must have {DIMS} dimensions, got {len(query_vec)}")
    if source_ids is not None and len(source_ids) == 0:
        return DenseResult([], "exact")
    degraded: list[str] = []
    async with in_transaction(conn):
        effective: Literal["exact", "ann"]
        if mode == "exact":
            effective = "exact"
        else:
            if mode == "auto":
                n = await active_chunk_count(conn, workspace_id, index_profile_id)
                wanted = "exact" if n < AUTO_ANN_THRESHOLD else "ann"
            else:
                wanted = "ann"
            effective = wanted
            if wanted == "ann" and not supports_iterative_scan(await pgvector_version(conn)):
                effective = "exact"
                degraded.append(FLAG_ANN_UNAVAILABLE)
        if effective == "ann":
            await conn.execute("select set_config('hnsw.ef_search', %s, true), "
                               "set_config('hnsw.iterative_scan', 'relaxed_order', true)", (str(EF_SEARCH),))
        params = {**scope_params(workspace_id, index_profile_id, source_ids), "q": vec_literal(query_vec), "k": k}
        cur = await conn.execute(dense_sql(effective, source_ids is not None), params)
        rows = await cur.fetchall()
    cands = [row_to_candidate(r, "dense", i, 1.0 - float(r[10])) for i, r in enumerate(rows, 1)]
    return DenseResult(cands, effective, degraded)


async def legacy_dense_search(
    conn: psycopg.AsyncConnection, workspace_id: str, query_vec: Sequence[float], *, index_profile_id,
    source_ids: Sequence[int] | None = None, threshold: float = LEGACY_THRESHOLD, limit: int = LEGACY_LIMIT,
) -> list[Candidate]:
    """Scribe 1.0 semantics (lib/search.ts): similarity = 1 - cosine distance, keep similarity >
    threshold, order by similarity desc, limit N. Exact only (no ANN index exists for 3072 dims).
    Legacy chunks are flat (no parents), so there is no children-only filter. Same scope filters as
    the v2 path (workspace, ACTIVE generation, not deleted)."""
    if len(query_vec) != LEGACY_DIMS:
        raise ValueError(f"legacy query vector must have {LEGACY_DIMS} dimensions, got {len(query_vec)}")
    if source_ids is not None and len(source_ids) == 0:
        return []
    sim = "(1 - (e.embedding <=> %(q)s::vector))"
    sql = f"""select {CANDIDATE_COLUMNS}, {sim} as sim
from chunk_embeddings_3072 e
  join chunks c on c.id = e.chunk_id
  {SCOPE_JOINS}
  where e.workspace_id = %(w)s and {scope_where(source_ids, children_only=False)} and {sim} > %(t)s
  order by {sim} desc, c.id
  limit %(k)s"""
    params = {**scope_params(workspace_id, index_profile_id, source_ids), "q": vec_literal(query_vec),
              "t": threshold, "k": limit}
    async with in_transaction(conn):
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [row_to_candidate(r, "dense-legacy", i, float(r[10])) for i, r in enumerate(rows, 1)]
