"""POST /compare (T29). Scope `compare`; the workspace comes from the token only (INV-15).

Runs 2-5 seeded retrieval profiles for one query inside ONE `REPEATABLE READ READ ONLY` snapshot (D12): every profile
sees exactly the same active generations, even if a Source is deleted or re-indexed while the request runs. The query
is embedded once (all seeded profiles use the same 1536-d structural embedding space) and the vector is shared.
Reranking happens after the snapshot closed, as in /retrieve. Each profile gets its own trace (marked
``compare: true`` in the trace counts, with a shared ``compare_id``); no evidence bundle or E-label is minted, so a
comparison can never produce a citable answer. Scores are reported per ranker and never summed (INV-10). Document text
is returned to the caller (as /retrieve does) but never logged (INV-13).

Errors: 422 validation (count outside 2..5, duplicates, blank query), 404 UNKNOWN_PROFILE, 503 RETRIEVAL_FAILED
(embedding provider or database failure), 500 RERANK_CACHE_MISS (replay-mode configuration problem).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any

import psycopg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from scribe_ingest.embedding import EmbeddingError, FakeEmbedderRefused
from scribe_ingest.tokens import count_tokens

from .auth import Principal, require_scope
from .config import get_settings
from .db import get_pool
from .retrieval import compare as cmp
from .retrieval.profiles import BY_NAME, RetrievalProfile
from .retrieval.registry import STRUCTURAL_INDEX, _first, _release
from .retrieval.rerank import DEFAULT_MAX_DOC_TOKENS, CacheMiss
from .retrieval.search import resolve_index_profile, snapshot_tx
from .retrieval.strategies.hybrid import InSnapshot, finish_profile, read_in_snapshot
from .retrieve_routes import (FakeRerankerRefused, RetrieveBody, _error, make_embedder_for, make_reranker_for,
                              persist_trace, span_json)

router = APIRouter()
log = logging.getLogger("scribe.compare")

EMBED_DIMS = 1536  # every seeded profile queries the structural-v1 (1536-d) space
COST_NOTE = "estimated; 0 for fake adapters"


class CompareBody(BaseModel):
    model_config = ConfigDict(extra="ignore")  # a workspace_id in the body is ignored, never read
    query: str = Field(min_length=1, max_length=2000)
    profiles: list[str] = Field(min_length=cmp.MIN_PROFILES, max_length=cmp.MAX_PROFILES)
    source_ids: list[int] | None = Field(default=None, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.replace("\x00", " ").strip():
            raise ValueError("query must not be blank")
        return v

    @field_validator("profiles")
    @classmethod
    def _distinct(cls, v: list[str]) -> list[str]:
        if any(not isinstance(p, str) or not p or len(p) > 64 for p in v):
            raise ValueError("profile names must be 1-64 characters")
        if len(set(v)) != len(v):
            raise ValueError("profiles must be distinct")
        return v


def compare_config_hash(profiles: list[RetrievalProfile], limit: int, source_ids: list[int] | None) -> str:
    """sha256 over the exact immutable profile configs (by their own hashes), the limit and the source filter."""
    key = {"profiles": [[p.name, p.config_hash] for p in profiles], "limit": limit, "source_ids": source_ids}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


def _price(env_name: str) -> float | None:
    try:
        v = float(os.environ.get(env_name, ""))
    except ValueError:
        return None
    return v if v >= 0 else None


def _usd(tokens: int, fake: bool, env_name: str) -> float | None:
    """0 for fake adapters; None when no unit price is configured (no price is invented); else tokens x price."""
    if fake:
        return 0.0
    price = _price(env_name)
    return None if price is None else round(tokens / 1_000_000 * price, 8)


def rerank_cost(query_tokens: int, result, fake: bool) -> dict[str, Any]:
    """Rerank usage estimate: documents actually scored, and query tokens per document plus document tokens (each
    capped like the adapter does). A degraded rerank is not counted (no scores were produced)."""
    docs = [rc for rc in result.ranked if rc.rerank_rank is not None]
    doc_tokens = sum(min(rc.candidate.token_count or 0, DEFAULT_MAX_DOC_TOKENS) for rc in docs)
    tokens = query_tokens * len(docs) + doc_tokens
    return {"estimated": True, "note": COST_NOTE, "rerank_documents": len(docs), "rerank_tokens": tokens,
            "usd": _usd(tokens, fake, "SCRIBE_PRICE_RERANK_PER_MTOK_USD") if docs or fake else 0.0}


def result_json(rank: int, rc) -> dict[str, Any]:
    c = rc.candidate
    return {
        "rank": rank, "chunk_id": str(c.chunk_id), "parent_id": str(c.parent_id) if c.parent_id else None,
        "doc_name": c.doc_name, "heading_text": c.heading_text, "snippet": cmp.snippet(c.content),
        "source_id": c.source_id, "source_version_id": str(c.source_version_id),
        # one score per ranker, never combined (INV-10); fusion_score is derived from ranks only
        "dense_rank": rc.dense_rank, "dense_score": rc.dense_score,
        "lexical_rank": rc.lexical_rank, "lexical_score": rc.lexical_score,
        "fusion_rank": rc.fusion_rank, "fusion_score": rc.fusion_score,
        "rerank_rank": rc.rerank_rank, "rerank_score": rc.rerank_score,
        "in_context": rc.selected,
        "spans": [span_json(s) for s in c.spans[:3]],
    }


@router.post("/compare")
async def compare(body: CompareBody, principal: Principal = Depends(require_scope("compare"))):
    wid = principal.wid
    started = time.monotonic()
    try:
        uuid.UUID(wid)
    except ValueError:
        return _error(403, "INVALID_WORKSPACE", "invalid workspace", False)
    unknown = [n for n in body.profiles if n not in BY_NAME]
    if unknown:
        return _error(404, "UNKNOWN_PROFILE", f"unknown retrieval profile(s) {unknown!r}", False,
                      unknown=unknown, profiles=sorted(BY_NAME))
    profiles = [BY_NAME[n] for n in body.profiles]
    settings = get_settings()
    try:
        embedder_for = make_embedder_for(settings.dsn)
        embedder = embedder_for(EMBED_DIMS)  # fail fast on a refused fake embedder / bad config
    except FakeEmbedderRefused as exc:
        return _error(500, "FAKE_EMBEDDER_REFUSED", str(exc), False)
    except ValueError as exc:
        return _error(500, "EMBEDDER_MISCONFIGURED", str(exc), False)
    try:
        reranker_for = make_reranker_for(settings.dsn)
    except FakeRerankerRefused as exc:
        return _error(500, "FAKE_RERANKER_REFUSED", str(exc), False)
    except ValueError as exc:
        return _error(500, "RERANKER_MISCONFIGURED", str(exc), False)
    try:
        pool = await get_pool()
    except RuntimeError:
        return _error(503, "RETRIEVAL_FAILED", "database unavailable", True)

    # ---- one embedding for every profile that uses the dense retriever --------------------------------------------
    uses_dense = [p for p in profiles if p.config.dense_top_k is not None]
    embed_ms: dict[str, int] = {}

    async def timed_embed() -> list[float]:
        t = time.monotonic()
        vec = await _first(embedder.embed_queries([body.query]))
        embed_ms["embed"] = int((time.monotonic() - t) * 1000)
        return vec

    embed = asyncio.ensure_future(timed_embed()) if uses_dense else None

    # ---- ONE snapshot: every profile reads inside it, sequentially on its connection ------------------------------
    reads: dict[str, InSnapshot] = {}
    read_ms: dict[str, int] = {}
    snapshot_id: str | None = None
    try:
        async with snapshot_tx(pool, wid) as conn:
            cur = await conn.execute("select pg_current_snapshot()::text")
            snapshot_id = (await cur.fetchone())[0]
            prof = await resolve_index_profile(conn, STRUCTURAL_INDEX)
            # dense profiles first: the first one overlaps its lexical query with the embedding still in flight
            exec_order = sorted(profiles, key=lambda p: p.config.dense_top_k is None)
            vec: Any = embed
            for p in exec_order:
                t = time.monotonic()
                reads[p.name] = await read_in_snapshot(conn, wid, body.query, p, prof, source_ids=body.source_ids,
                                                       query_vec=vec)
                read_ms[p.name] = int((time.monotonic() - t) * 1000)
                if embed is not None and embed.done() and not embed.cancelled() and embed.exception() is None:
                    vec = embed.result()  # later profiles reuse the ready vector
    except (EmbeddingError, psycopg.Error, LookupError, OSError) as exc:
        code = exc.code if isinstance(exc, EmbeddingError) else type(exc).__name__
        retryable = exc.retryable if isinstance(exc, EmbeddingError) else not isinstance(exc, LookupError)
        log.warning("compare retrieval failed workspace=%s cause=%s", wid, code)  # class/code only (INV-13)
        msg = "embedding provider failed" if isinstance(exc, EmbeddingError) else "retrieval backend failed"
        return _error(503, "RETRIEVAL_FAILED", f"{msg} ({code})", retryable, cause=code)
    finally:
        if embed is not None:
            _release(embed)

    # ---- after the snapshot: fusion / rerank / dedup / pack, profiles in parallel (network rerank calls) --------------
    outcomes = await asyncio.gather(
        *(finish_profile(p, body.query, reads[p.name],
                         reranker_for(p.config.rerank_model) if p.config.rerank and p.config.rerank_model else None)
          for p in profiles),
        return_exceptions=True)
    for o in outcomes:
        if isinstance(o, CacheMiss):
            log.warning("compare rerank cache miss workspace=%s", wid)
            return _error(500, "RERANK_CACHE_MISS", "rerank cache miss in replay mode (configuration problem)", False)
    for o in outcomes:
        if isinstance(o, BaseException):
            raise o
    results = dict(zip(body.profiles, outcomes))

    compare_id = str(uuid.uuid4())
    query_tokens = count_tokens(body.query)
    embed_fake = bool(getattr(embedder, "is_fake", False))
    entries: list[dict[str, Any]] = []
    lists: dict[str, list[dict[str, Any]]] = {}
    total_rerank_tokens = 0
    total_usd: float | None = 0.0
    for p in profiles:
        res = results[p.name]
        returned = res.ranked[: body.limit]
        meta = dict(res.extra)
        degraded = list(res.degraded)
        stage_ms = dict(res.stage_ms)
        stage_ms["read_in_snapshot"] = read_ms[p.name]
        # a trace per profile, with a compare marker; no evidence bundle (nothing citable comes out of a comparison)
        res.extra = {**res.extra, "compare": True, "compare_id": compare_id, "compare_profiles": body.profiles}
        trace_id: str | None = None
        t = time.monotonic()
        try:
            tid, _ = await persist_trace(pool, wid, RetrieveBody(query=body.query, profile=p.name,
                                                                 source_ids=body.source_ids, limit=body.limit),
                                         "ok", result=res, returned=len(returned), stage_ms=stage_ms,
                                         degraded=degraded, drafts=None)
            trace_id = str(tid)
        except Exception as exc:  # noqa: BLE001 - tracing must never fail the comparison
            log.warning("compare trace persist failed workspace=%s cause=%s", wid, type(exc).__name__)
            degraded.append("trace_persist_failed")
        stage_ms["trace_persist"] = int((time.monotonic() - t) * 1000)

        rows = [result_json(i, rc) for i, rc in enumerate(returned, 1)]
        lists[p.name] = rows
        fake_reranker = bool(meta.get("reranker_is_fake"))
        cost = rerank_cost(query_tokens, res, fake_reranker) if p.config.rerank else {
            "estimated": True, "note": COST_NOTE, "rerank_documents": 0, "rerank_tokens": 0, "usd": 0.0}
        total_rerank_tokens += cost["rerank_tokens"]
        total_usd = None if (total_usd is None or cost["usd"] is None) else total_usd + cost["usd"]
        entries.append({
            "profile": p.name, "description": p.description, "config": p.config_json, "config_hash": p.config_hash,
            "trace_id": trace_id, "degraded": degraded, "stage_ms": stage_ms,
            "counts": {**res.counts, "returned": len(returned)}, "meta": meta, "cost": cost, "results": rows,
        })

    embed_tokens = query_tokens if uses_dense else 0
    embed_usd = _usd(embed_tokens, embed_fake, "SCRIBE_PRICE_EMBED_PER_MTOK_USD") if uses_dense else 0.0
    total_usd = None if (total_usd is None or embed_usd is None) else round(total_usd + embed_usd, 8)
    return {
        "compare_id": compare_id, "query": body.query, "limit": body.limit, "profiles": body.profiles,
        "config_hash": compare_config_hash(profiles, body.limit, body.source_ids),
        "snapshot": {"id": snapshot_id, "isolation": "repeatable read", "read_only": True,
                     "shared_by": body.profiles},
        "shared": {"embedding": {"calls": 1 if uses_dense else 0, "dims": EMBED_DIMS, "ms": embed_ms.get("embed"),
                                 "shared_by": [p.name for p in uses_dense], "is_fake": embed_fake}},
        "profile_results": entries,
        "alignment": cmp.build_alignment(body.profiles, lists),
        "cost": {"estimated": True, "note": COST_NOTE, "embed_tokens": embed_tokens, "embed_usd": embed_usd,
                 "rerank_tokens": total_rerank_tokens, "usd": total_usd,
                 "price_env": ["SCRIBE_PRICE_EMBED_PER_MTOK_USD", "SCRIBE_PRICE_RERANK_PER_MTOK_USD"]},
        "request_total_ms": int((time.monotonic() - started) * 1000),
    }
