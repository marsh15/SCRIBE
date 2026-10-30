"""Read one workspace-scoped retrieval trace without returning document text."""
from __future__ import annotations

import uuid

import psycopg
from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from psycopg.rows import dict_row
from scribe_ingest.indexer import _begin

from .auth import Principal, require_scope
from .db import get_pool

router = APIRouter()
MAX_CANDIDATES = 100


class TraceBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    trace_id: uuid.UUID


@router.post("/retrieval/trace")
async def read_trace(body: TraceBody, principal: Principal = Depends(require_scope("retrieval_trace_read"))):
    wid = principal.wid
    try:
        uuid.UUID(wid)
    except ValueError:
        return JSONResponse(status_code=403, content={"error_code": "INVALID_WORKSPACE", "message": "invalid workspace", "retryable": False})
    try:
        pool = await get_pool()
        async with pool.connection() as conn:
            async with conn.transaction():
                await _begin(conn, wid)
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("""
                        select t.id, t.query, coalesce(p.name, t.counts->>'profile') as profile,
                               t.config_hash, t.status, t.degraded, t.stage_ms, t.counts, t.cost, t.created_at
                        from retrieval_traces t
                        left join retrieval_profiles p on p.id = t.profile_id
                        where t.id = %(id)s and t.workspace_id = %(w)s
                    """, {"id": body.trace_id, "w": wid})
                    trace = await cur.fetchone()
                    if trace is None:
                        return JSONResponse(status_code=404, content={"error_code": "NOT_FOUND", "message": "trace not found", "retryable": False})
                    await cur.execute("""
                        select answerability as state, top_rerank_score from evidence_bundles
                        where trace_id = %(id)s and workspace_id = %(w)s order by created_at, id limit 1
                    """, {"id": body.trace_id, "w": wid})
                    answerability = await cur.fetchone()
                    await cur.execute("""
                        select r.id, case when f.id is not null then c.id end as chunk_id,
                               f.id as source_id, f.name as source_name,
                               case when f.id is not null then v.id end as source_version_id,
                               case when f.id is not null then v.version_number end as version_number,
                               r.dense_rank, r.dense_score, r.lexical_rank, r.lexical_score,
                               r.fusion_rank, r.rerank_rank, r.rerank_score, r.selected,
                               (f.id is null) as source_unavailable
                        from retrieval_candidates r
                        left join chunks c on c.id = r.chunk_id and c.workspace_id = %(w)s
                        left join source_versions v on v.id = c.source_version_id and v.workspace_id = %(w)s
                        left join files f on f.id = v.source_id and f.workspace_id = %(w)s and f.deleted_at is null
                        where r.trace_id = %(id)s and r.workspace_id = %(w)s
                        order by r.rerank_rank nulls last, r.fusion_rank nulls last,
                                 r.dense_rank nulls last, r.lexical_rank nulls last, r.id
                        limit %(limit)s
                    """, {"id": body.trace_id, "w": wid, "limit": MAX_CANDIDATES + 1})
                    candidates = await cur.fetchall()
    except (psycopg.Error, RuntimeError):
        return JSONResponse(status_code=503, content={"error_code": "TRACE_UNAVAILABLE", "message": "trace backend unavailable", "retryable": True})
    trace["degraded"] = trace["degraded"].split(",") if trace["degraded"] else []
    return jsonable_encoder({"trace": trace, "answerability": answerability,
                             "candidates": candidates[:MAX_CANDIDATES],
                             "candidates_truncated": len(candidates) > MAX_CANDIDATES})
