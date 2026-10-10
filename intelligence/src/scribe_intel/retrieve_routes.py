"""POST /retrieve (T13). Scope `retrieve`; the workspace comes from the token only (INV-15).

Errors are JSON objects ``{"error_code", "message", "retryable"}``: 422 validation (FastAPI default
body), 404 UNKNOWN_PROFILE, 503 RETRIEVAL_FAILED (embedding provider or database failure: never a
silent empty result, INV-16/B4). A retrieval trace is written in a separate short transaction after
the read snapshot closes; if that write fails the response still succeeds and carries the degraded flag
``trace_persist_failed``. Document text is never logged (INV-13).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from scribe_ingest.embedding import (Embedder, EmbeddingError, FakeEmbedder, FakeEmbedderRefused,
                                     GeminiEmbedder, TaskFormat, embedder_from_env)

from .auth import Principal, require_scope
from .config import get_settings
from .db import get_pool
from .retrieval.registry import DEFAULT_PROFILE, RetrieveContext, StrategyResult, get_handler, profile_names
from .retrieval.types import RankedCandidate

router = APIRouter()
log = logging.getLogger("scribe.retrieve")

LEGACY_MODEL = "gemini-embedding-2-preview"
LEGACY_TASK_FORMAT = TaskFormat(name="legacy-none-v1", document="{text}", query="{text}")


class RetrieveBody(BaseModel):
    model_config = ConfigDict(extra="ignore")  # a workspace_id in the body is ignored, never read
    query: str = Field(min_length=1, max_length=2000)
    profile: str = DEFAULT_PROFILE
    source_ids: list[int] | None = Field(default=None, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.replace("\x00", " ").strip():
            raise ValueError("query must not be blank")
        return v


def _error(status: int, code: str, message: str, retryable: bool, **extra: Any) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error_code": code, "message": message,
                                                     "retryable": retryable, **extra})


def make_embedder_for(dsn: str):
    """dims -> embedder. 1536: the v2 query embedder (task format: query prefix). 3072: the legacy
    Scribe 1.0 space (gemini-embedding-2-preview, raw text, no task prefix)."""
    def embedder_for(dims: int) -> Embedder:
        if dims == 1536:
            return embedder_from_env(dsn)
        base = embedder_from_env(dsn)
        if isinstance(base, FakeEmbedder):
            return FakeEmbedder(dims)
        return GeminiEmbedder(model=LEGACY_MODEL, dimensions=dims, task_format=LEGACY_TASK_FORMAT)
    return embedder_for


def candidate_json(rank: int, rc: RankedCandidate) -> dict[str, Any]:
    c = rc.candidate
    return {
        "rank": rank, "chunk_id": str(c.chunk_id), "parent_id": str(c.parent_id) if c.parent_id else None,
        "doc_name": c.doc_name, "heading_text": c.heading_text, "content": c.content,
        "method": c.method, "score": c.score,
        "dense_rank": rc.dense_rank, "dense_score": rc.dense_score,
        "lexical_rank": rc.lexical_rank, "lexical_score": rc.lexical_score,
        "fusion_rank": rc.fusion_rank, "fusion_score": rc.fusion_score,
        "rerank_rank": rc.rerank_rank, "rerank_score": rc.rerank_score,
        "source_id": c.source_id, "source_version_id": str(c.source_version_id),
        "generation_id": str(c.generation_id), "content_hash": c.content_hash, "token_count": c.token_count,
        "spans": [{"block_id": str(s.block_id), "block_index": s.block_index, "char_start": s.char_start,
                   "char_end": s.char_end, "page": s.page, "bbox": list(s.bbox) if s.bbox else None,
                   "ocr": s.ocr, "row_number": s.row_number} for s in c.spans],
        "provenance": {"source_id": c.source_id, "source_version_id": str(c.source_version_id),
                       "generation_id": str(c.generation_id), "chunk_id": str(c.chunk_id), "doc_name": c.doc_name},
    }


async def persist_trace(pool, wid: str, body: RetrieveBody, status: str, *, result: StrategyResult | None,
                        returned: int, stage_ms: dict[str, int], degraded: list[str]) -> uuid.UUID:
    """Separate short write transaction (the read snapshot is already closed)."""
    counts: dict[str, Any] = {"profile": body.profile, "returned": returned}
    if result is not None:
        counts.update(result.counts)
        counts.update(result.extra)
    cfg_hash = hashlib.sha256(json.dumps({"profile": body.profile, "limit": body.limit,
                                          "source_ids": body.source_ids}, sort_keys=True).encode()).hexdigest()
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("select set_config('app.workspace_id', %s, true)", (wid,))
            cur = await conn.execute(
                "insert into retrieval_traces(workspace_id, query, config_hash, status, degraded, stage_ms, counts) "
                "values (%s,%s,%s,%s,%s,%s,%s) returning id",
                (wid, body.query, cfg_hash, status, ",".join(degraded) or None,
                 Jsonb(stage_ms), Jsonb(counts)))
            trace_id = (await cur.fetchone())[0]
            if result is not None and result.ranked:
                async with conn.cursor() as c2:
                    await c2.executemany(
                        "insert into retrieval_candidates(trace_id, workspace_id, chunk_id, dense_rank, dense_score, "
                        "lexical_rank, lexical_score, fusion_rank, rerank_rank, rerank_score, selected) "
                        "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [(trace_id, wid, rc.chunk_id, rc.dense_rank, rc.dense_score, rc.lexical_rank, rc.lexical_score,
                          rc.fusion_rank, rc.rerank_rank, rc.rerank_score, i < body.limit)
                         for i, rc in enumerate(result.ranked)])
    return trace_id


@router.post("/retrieve")
async def retrieve(body: RetrieveBody, principal: Principal = Depends(require_scope("retrieve"))):
    wid = principal.wid
    started = time.monotonic()
    try:
        uuid.UUID(wid)
    except ValueError:
        return _error(403, "INVALID_WORKSPACE", "invalid workspace", False)
    handler = get_handler(body.profile)
    if handler is None:
        return _error(404, "UNKNOWN_PROFILE", f"unknown retrieval profile {body.profile!r}", False,
                      profiles=profile_names())
    settings = get_settings()
    try:
        embedder_for = make_embedder_for(settings.dsn)
        embedder_for(1536)  # fail fast on a refused fake embedder / bad config
    except FakeEmbedderRefused as exc:
        return _error(500, "FAKE_EMBEDDER_REFUSED", str(exc), False)
    except ValueError as exc:
        return _error(500, "EMBEDDER_MISCONFIGURED", str(exc), False)
    try:
        pool = await get_pool()
    except RuntimeError:
        return _error(503, "RETRIEVAL_FAILED", "database unavailable", True)

    ctx = RetrieveContext(pool=pool, workspace_id=wid, query=body.query, source_ids=body.source_ids,
                          limit=body.limit, embedder_for=embedder_for)
    try:
        result = await handler(ctx)
    except (EmbeddingError, psycopg.Error, LookupError, OSError) as exc:
        # message carries only the error class/code, never query or document text (INV-13)
        code = exc.code if isinstance(exc, EmbeddingError) else type(exc).__name__
        retryable = exc.retryable if isinstance(exc, EmbeddingError) else not isinstance(exc, LookupError)
        log.warning("retrieval failed profile=%s workspace=%s cause=%s", body.profile, wid, code)
        trace_id = None
        try:
            trace_id = str(await persist_trace(pool, wid, body, "failed", result=None, returned=0,
                                               stage_ms={"total": int((time.monotonic() - started) * 1000)},
                                               degraded=[]))
        except Exception:  # noqa: BLE001
            log.warning("trace persist failed (failed retrieval) workspace=%s", wid)
        msg = ("embedding provider failed" if isinstance(exc, EmbeddingError) else "retrieval backend failed")
        return _error(503, "RETRIEVAL_FAILED", f"{msg} ({code})", retryable, cause=code, trace_id=trace_id)

    degraded = list(result.degraded)
    returned = result.ranked[: body.limit]
    stage_ms = dict(result.stage_ms)
    trace_id: str | None = None
    t = time.monotonic()
    try:
        trace_id = str(await persist_trace(pool, wid, body, "ok", result=result, returned=len(returned),
                                           stage_ms=stage_ms, degraded=degraded))
    except Exception as exc:  # noqa: BLE001 - tracing must never fail the request
        log.warning("trace persist failed workspace=%s cause=%s", wid, type(exc).__name__)
        degraded.append("trace_persist_failed")
    stage_ms["trace_persist"] = int((time.monotonic() - t) * 1000)
    stage_ms["request_total"] = int((time.monotonic() - started) * 1000)
    return {
        "profile": body.profile, "trace_id": trace_id, "degraded": degraded,
        "candidates": [candidate_json(i, rc) for i, rc in enumerate(returned, 1)],
        "stage_ms": stage_ms, "counts": {**result.counts, "returned": len(returned)}, "meta": result.extra,
    }
