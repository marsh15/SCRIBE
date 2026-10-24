"""POST /retrieve (T13). Scope `retrieve`; the workspace comes from the token only (INV-15).

Errors are JSON objects ``{"error_code", "message", "retryable"}``: 422 validation (FastAPI default
body), 404 UNKNOWN_PROFILE, 500 RERANK_CACHE_MISS (replay-mode configuration problem), 503
RETRIEVAL_FAILED (embedding provider or database failure: never a silent empty result, INV-16/B4). A retrieval trace is written in a separate short transaction after
the read snapshot closes; if that write fails the response still succeeds and carries the degraded flag
``trace_persist_failed``. Document text is never logged (INV-13).

T25: for profiles that pack a context, the trace, an evidence bundle and its labelled items (`E<next_label>`,
`E<next_label+1>`, ...) are written in ONE transaction, and the response carries `evidence`
(bundle_id, items, next_label) and `answerability` (the D13 gate, see `evidence_bundle`). A failed retrieval
answers 503 with `answerability.state == "RETRIEVAL_FAILED"`, distinct from INSUFFICIENT_EVIDENCE. If the trace/bundle
write fails the response still succeeds but carries NO labels and `evidence.bundle_id` null (nothing citable can
exist without a stored item) plus the degraded flag `evidence_not_recorded`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from scribe_ingest.ocr import OCR_NOTICE

from scribe_ingest.embedding import (Embedder, EmbeddingError, FakeEmbedder, FakeEmbedderRefused,
                                     GeminiEmbedder, TaskFormat, database_name, embedder_from_env)
from scribe_ingest.tokens import count_tokens

from .auth import Principal, require_scope
from .config import get_settings
from .db import get_pool
from . import evidence_bundle as eb
from .retrieval.profiles import BY_NAME, gate_for
from .retrieval.registry import DEFAULT_PROFILE, RetrieveContext, StrategyResult, get_handler, profile_names
from .retrieval.packing import PackedContext
from .retrieval.rerank import CacheMiss, FakeReranker, VoyageReranker
from .retrieval.types import RankedCandidate, SpanRef
from .pricing import PRICE_TABLE_DATE, UnknownPrice, tokens_usd

router = APIRouter()
log = logging.getLogger("scribe.retrieve")

LEGACY_MODEL = "gemini-embedding-2-preview"
LEGACY_TASK_FORMAT = TaskFormat(name="legacy-none-v1", document="{text}", query="{text}")


class _RetrievalCost:
    """Tracks only provider stages actually invoked by the selected retrieval strategy."""

    def __init__(self) -> None:
        self.embed_tokens = 0
        self.rerank_tokens = 0
        self.usd = 0.0
        self.estimated = False
        self.unknown_price = False

    def add(self, model: str, token_count: int, *, cached: bool = False, is_fake: bool = False) -> None:
        self.estimated = True  # current retrieval adapters do not return provider token usage
        if cached or is_fake or model.startswith("fake"):
            return
        try:
            self.usd += tokens_usd(model, token_count)
        except UnknownPrice:
            # Keep an unknown model from failing retrieval; null USD is more honest than an invented rate.
            self.unknown_price = True

    def as_json(self) -> dict[str, Any]:
        return {"embed_tokens": self.embed_tokens, "rerank_tokens": self.rerank_tokens,
                "usd": None if self.unknown_price else round(self.usd, 8), "estimated": self.estimated,
                "prices_as_of": PRICE_TABLE_DATE}


class _TrackedEmbedder:
    def __init__(self, inner: Embedder, cost: _RetrievalCost, dimensions: int | None = None) -> None:
        self._inner, self._cost = inner, cost
        model = getattr(inner, "model", None)
        self.model = model if isinstance(model, str) and model else "unknown-embedding"
        self.dimensions = getattr(inner, "dimensions", dimensions)
        self.is_fake = bool(getattr(inner, "is_fake", False))

    async def embed_documents(self, texts, titles=None):
        return await self._inner.embed_documents(texts, titles)

    async def embed_queries(self, texts):
        before = getattr(self._inner, "requests_made", None)
        task_format = getattr(self._inner, "task_format", None)
        billed_texts = [task_format.format_query(text) for text in texts] if task_format else texts
        tokens = sum(count_tokens(text) for text in billed_texts)
        try:
            return await self._inner.embed_queries(texts)
        finally:
            after = getattr(self._inner, "requests_made", None)
            cached = before is not None and after == before
            self._cost.embed_tokens += tokens
            self._cost.add(self.model, tokens, cached=cached, is_fake=self.is_fake)


class _TrackedReranker:
    def __init__(self, inner, cost: _RetrievalCost) -> None:
        self._inner, self._cost = inner, cost
        model = getattr(inner, "model", None)
        self.model = model if isinstance(model, str) and model else "unknown-reranker"
        self.is_fake = bool(getattr(inner, "is_fake", False))

    async def rerank(self, query, docs, top_n):
        before_requests = getattr(getattr(self._inner, "inner", self._inner), "requests_made", None)
        before_hits = getattr(self._inner, "hits", 0)
        tokens = count_tokens(query) * len(docs) + sum(count_tokens(doc) for doc in docs)
        try:
            return await self._inner.rerank(query, docs, top_n)
        finally:
            after_requests = getattr(getattr(self._inner, "inner", self._inner), "requests_made", None)
            after_hits = getattr(self._inner, "hits", before_hits)
            cached = after_hits > before_hits or (before_requests is not None and after_requests == before_requests)
            self._cost.rerank_tokens += tokens
            self._cost.add(self.model, tokens, cached=cached, is_fake=self.is_fake)


def _costed_embedder_factory(factory, cost: _RetrievalCost):
    return lambda dims: _TrackedEmbedder(factory(dims), cost, dims)


def _costed_reranker_factory(factory, cost: _RetrievalCost):
    def tracked(model):
        reranker = factory(model)
        return _TrackedReranker(reranker, cost) if reranker is not None else None
    return tracked


class RetrieveBody(BaseModel):
    model_config = ConfigDict(extra="ignore")  # a workspace_id in the body is ignored, never read
    query: str = Field(min_length=1, max_length=2000)
    profile: str = DEFAULT_PROFILE
    source_ids: list[int] | None = Field(default=None, max_length=500)
    limit: int = Field(default=10, ge=1, le=50)
    # First evidence label number for this call. The caller (one chat turn) passes the `evidence.next_label` of the
    # previous search so that E-labels are unique across all searches of the turn.
    next_label: int = Field(default=1, ge=1, le=eb.MAX_LABEL_START)

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


class FakeRerankerRefused(RuntimeError):
    pass


def make_reranker_for(dsn: str, env: "os._Environ[str] | dict[str, str] | None" = None):
    """model name -> Reranker | None.

    SCRIBE_RERANKER=voyage (default): VoyageReranker(model) (reads VOYAGE_API_KEY itself; without a key
    it raises AUTH on use, which the pipeline turns into the degraded flag `rerank_unavailable`, no
    request is made); fake: FakeReranker, only with SCRIBE_ALLOW_FAKE_RERANKER=1 AND a database whose
    name contains 'test' (else FakeRerankerRefused, raised here so the route fails fast); none: no
    reranker (profiles that rerank degrade to fused order)."""
    env = os.environ if env is None else env
    kind = (env.get("SCRIBE_RERANKER") or "voyage").lower()
    if kind == "voyage":
        return lambda model: VoyageReranker(model=model)
    if kind == "none":
        return lambda model: None
    if kind == "fake":
        name = database_name(dsn)
        if env.get("SCRIBE_ALLOW_FAKE_RERANKER") != "1" or "test" not in name.lower():
            raise FakeRerankerRefused(
                "REFUSING to use the fake reranker: it requires SCRIBE_ALLOW_FAKE_RERANKER=1 and a database "
                f"whose name contains 'test' (database is {name!r}).")
        return lambda model: FakeReranker()
    raise ValueError(f"SCRIBE_RERANKER must be voyage|fake|none, got {kind!r}")


def span_json(s: SpanRef) -> dict[str, Any]:
    return {"block_id": str(s.block_id), "block_index": s.block_index, "char_start": s.char_start,
            "char_end": s.char_end, "page": s.page, "bbox": list(s.bbox) if s.bbox else None,
            "ocr": s.ocr, "ocr_notice": OCR_NOTICE if s.ocr else None, "row_number": s.row_number}


def context_json(p: PackedContext, items: dict[Any, "eb.ItemDraft"] | None = None) -> dict[str, Any]:
    items = items or {}
    context = {
        "budget_tokens": p.budget_tokens, "used_tokens": p.used_tokens,
        "entries": [{
            "chunk_id": str(e.item.chunk_id), "parent_id": str(e.item.parent_id) if e.item.parent_id else None,
            "text": e.item.text, "heading_text": e.item.heading_text, "doc_name": e.item.doc_name,
            "source_id": e.item.source_id, "source_version_id": str(e.item.source_version_id),
            "token_count": e.item.token_count, "rerank_score": e.item.rerank_score,
            "expanded_from_parent": e.item.expanded_from_parent,
            "merged_chunk_ids": [str(x) for x in e.merged_chunk_ids],
            "duplicates": [str(x) for x in e.duplicates],
            "spans": [span_json(s) for s in e.item.spans],
            "label": items[e.item.chunk_id].label if e.item.chunk_id in items else None,
            "evidence_item_id": str(items[e.item.chunk_id].id) if e.item.chunk_id in items else None,
        } for e in p.entries],
        "dropped": [{
            "chunk_id": str(d.chunk_id), "reason": d.reason, "token_count": d.token_count,
            **({"dropped_spans": [{"start": span.start, "end": span.end} for span in d.dropped_spans]}
               if p.context_strategy == "compressed" and d.dropped_spans else {}),
        } for d in p.dropped],
    }
    if p.context_strategy is not None:
        context["strategy"] = p.context_strategy
        if p.context_strategy == "compressed":
            for entry, serialized in zip(p.entries, context["entries"]):
                serialized["kept_spans"] = [
                    {"start": span.start, "end": span.end} for span in entry.kept_spans
                ]
                serialized["dropped_spans"] = [
                    {"start": span.start, "end": span.end} for span in entry.dropped_spans
                ]
    return context


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
        "spans": [span_json(s) for s in c.spans],
        "provenance": {"source_id": c.source_id, "source_version_id": str(c.source_version_id),
                       "generation_id": str(c.generation_id), "chunk_id": str(c.chunk_id), "doc_name": c.doc_name},
    }


def trace_config_hash(body: RetrieveBody) -> str:
    """sha256 of the request-level knobs. For a seeded retrieval_profiles profile the profile's own
    config hash replaces its name, so the hash pins the exact immutable config and is the same for
    ok and failed traces of one request."""
    seed = BY_NAME.get(body.profile)
    key: dict[str, Any] = {"profile": seed.config_hash if seed is not None else body.profile,
                           "limit": body.limit, "source_ids": body.source_ids}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


async def persist_trace(pool, wid: str, body: RetrieveBody, status: str, *, result: StrategyResult | None,
                        returned: int, stage_ms: dict[str, int], degraded: list[str],
                        cost: dict[str, Any] | None = None,
                        drafts: list["eb.ItemDraft"] | None = None) -> tuple[uuid.UUID, "eb.EvidenceOutcome | None"]:
    """Separate short write transaction (the read snapshot is already closed). With `drafts` (a packed profile) the
    evidence bundle + items are written in the same transaction and returned."""
    counts: dict[str, Any] = {"profile": body.profile, "returned": returned}
    if result is not None:
        counts.update(result.counts)
        counts.update(result.extra)
    # config_hash is identical for ok and failed traces of the same request (see trace_config_hash);
    # profile_id is linked by (name, config_hash) so a stale same-name row can never be linked.
    seed = BY_NAME.get(body.profile)
    cfg_hash = trace_config_hash(body)
    profile_name, profile_hash = (seed.name, seed.config_hash) if seed is not None else (None, None)
    packed = result is not None and result.packed is not None
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("select set_config('app.workspace_id', %s, true)", (wid,))
            cur = await conn.execute(
                "insert into retrieval_traces(workspace_id, query, config_hash, status, degraded, stage_ms, counts, cost, "
                "profile_id) values (%s,%s,%s,%s,%s,%s,%s,%s, (select id from retrieval_profiles "
                "where name = %s and config_hash = %s)) returning id",
                (wid, body.query, cfg_hash, status, ",".join(degraded) or None,
                 Jsonb(stage_ms), Jsonb(counts), Jsonb(cost) if cost is not None else None, profile_name, profile_hash))
            trace_id = (await cur.fetchone())[0]
            outcome = None
            if drafts is not None and result is not None:
                outcome = await eb.persist_bundle(conn, wid, trace_id, gate_for(body.profile), drafts,
                                                  result.extra.get("top_rerank_score"), body.next_label)
            if result is not None and result.ranked:
                async with conn.cursor() as c2:
                    await c2.executemany(
                        "insert into retrieval_candidates(trace_id, workspace_id, chunk_id, dense_rank, dense_score, "
                        "lexical_rank, lexical_score, fusion_rank, rerank_rank, rerank_score, selected) "
                        "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        [(trace_id, wid, rc.chunk_id, rc.dense_rank, rc.dense_score, rc.lexical_rank, rc.lexical_score,
                          rc.fusion_rank, rc.rerank_rank, rc.rerank_score, rc.selected if packed else i < body.limit)
                         for i, rc in enumerate(result.ranked)])
    return trace_id, outcome


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

    try:
        reranker_for = make_reranker_for(settings.dsn)
    except FakeRerankerRefused as exc:
        return _error(500, "FAKE_RERANKER_REFUSED", str(exc), False)
    except ValueError as exc:
        return _error(500, "RERANKER_MISCONFIGURED", str(exc), False)

    retrieval_cost = _RetrievalCost()
    ctx = RetrieveContext(pool=pool, workspace_id=wid, query=body.query, source_ids=body.source_ids,
                          limit=body.limit, embedder_for=_costed_embedder_factory(embedder_for, retrieval_cost),
                          reranker_for=_costed_reranker_factory(reranker_for, retrieval_cost))
    try:
        result = await handler(ctx)
    except (EmbeddingError, CacheMiss, psycopg.Error, LookupError, OSError) as exc:
        # message carries only the error class/code, never query or document text (INV-13)
        code = exc.code if isinstance(exc, EmbeddingError) else type(exc).__name__
        cache_miss = isinstance(exc, CacheMiss)
        # CacheMiss (replay mode) is a configuration problem, never silently degraded: loud and non-retryable
        retryable = (exc.retryable if isinstance(exc, EmbeddingError)
                     else not isinstance(exc, (LookupError, CacheMiss)))
        log.warning("retrieval failed profile=%s workspace=%s cause=%s", body.profile, wid, code)
        trace_id = None
        try:
            trace_id = str((await persist_trace(pool, wid, body, "failed", result=None, returned=0,
                                                stage_ms={"total": int((time.monotonic() - started) * 1000)},
                                                degraded=[], cost=retrieval_cost.as_json()))[0])
        except Exception:  # noqa: BLE001
            log.warning("trace persist failed (failed retrieval) workspace=%s", wid)
        failed = eb.retrieval_failed(gate_for(body.profile)).as_json()
        if cache_miss:
            return _error(500, "RERANK_CACHE_MISS", "rerank cache miss in replay mode (configuration problem)",
                          False, cause=code, trace_id=trace_id, answerability=failed)
        msg = ("embedding provider failed" if isinstance(exc, EmbeddingError) else "retrieval backend failed")
        return _error(503, "RETRIEVAL_FAILED", f"{msg} ({code})", retryable, cause=code, trace_id=trace_id,
                      answerability=failed)

    degraded = list(result.degraded)
    returned = result.ranked[: body.limit]
    stage_ms = dict(result.stage_ms)
    trace_id: str | None = None
    gate = gate_for(body.profile)
    top_score = result.extra.get("top_rerank_score")
    drafts = (eb.plan_items(result.packed, result.ranked, body.next_label) if result.packed is not None else None)
    outcome: eb.EvidenceOutcome | None = None
    t = time.monotonic()
    try:
        tid, outcome = await persist_trace(pool, wid, body, "ok", result=result, returned=len(returned),
                                           stage_ms=stage_ms, degraded=degraded, cost=retrieval_cost.as_json(),
                                           drafts=drafts)
        trace_id = str(tid)
    except Exception as exc:  # noqa: BLE001 - tracing must never fail the request
        log.warning("trace persist failed workspace=%s cause=%s", wid, type(exc).__name__)
        degraded.append("trace_persist_failed")
        if drafts is not None:
            degraded.append("evidence_not_recorded")  # no stored item exists, so no label may be offered
    stage_ms["trace_persist"] = int((time.monotonic() - t) * 1000)
    stage_ms["request_total"] = int((time.monotonic() - started) * 1000)
    if outcome is not None:
        answer = outcome.answerability
        evidence: dict[str, Any] = {"bundle_id": str(outcome.bundle_id), "next_label": outcome.next_label,
                                    "items": [eb.item_json(d) for d in outcome.items]}
    else:
        # strategy without a packed context (T13 single-retriever) or the bundle write failed: no labels exist
        answer = eb.evaluate_gate(gate, entries=len(drafts) if drafts is not None else len(returned),
                                  top_rerank_score=top_score)
        evidence = {"bundle_id": None, "next_label": body.next_label, "items": []}
    out: dict[str, Any] = {
        "profile": body.profile, "trace_id": trace_id, "degraded": degraded, "config_hash": trace_config_hash(body),
        "candidates": [candidate_json(i, rc) for i, rc in enumerate(returned, 1)],
        "stage_ms": stage_ms, "counts": {**result.counts, "returned": len(returned)}, "meta": result.extra,
        "answerability": answer.as_json(), "evidence": evidence,
        "cost": retrieval_cost.as_json(),
    }
    if result.packed is not None:
        out["context"] = context_json(result.packed, {d.chunk_id: d for d in outcome.items} if outcome else None)
    return out
