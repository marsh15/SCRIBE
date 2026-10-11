"""POST /ingest/source - index one source version in a single invocation (T12; T18 splits it
into leased steps). Scope `ingest`; the workspace comes from the token only (INV-15)."""

from __future__ import annotations

import asyncio
import os
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from scribe_ingest.blob import BlobDownloadError, download_private_blob
from scribe_ingest.embedding import FakeEmbedderRefused, embedder_from_env
from scribe_ingest.indexer import IndexerError, _begin, build_generation, load_index_profile, register_version

from .auth import Principal, require_scope
from .config import get_settings
from .db import get_pool

router = APIRouter()
TIME_BUDGET_S = float(os.environ.get("INGEST_TIME_BUDGET_S", "270"))


class IngestSourceBody(BaseModel):
    model_config = ConfigDict(extra="ignore")  # a workspace_id in the body is ignored, never read
    source_id: int
    version_id: uuid.UUID | None = None


def _fail(status: int, code: str, message: str, retryable: bool) -> HTTPException:
    return HTTPException(status_code=status, detail={"error_code": code, "message": message, "retryable": retryable})


@router.post("/ingest/source")
async def ingest_source(body: IngestSourceBody, principal: Principal = Depends(require_scope("ingest"))) -> dict:
    wid = principal.wid
    started = time.monotonic()
    try:
        uuid.UUID(wid)
    except ValueError:
        raise _fail(403, "INVALID_WORKSPACE", "invalid workspace", False) from None
    settings = get_settings()
    try:
        embedder = embedder_from_env(settings.dsn)
    except FakeEmbedderRefused as exc:
        raise _fail(500, "FAKE_EMBEDDER_REFUSED", str(exc), False) from None
    try:
        pool = await get_pool()
    except RuntimeError:
        raise _fail(503, "DATABASE_UNAVAILABLE", "database unavailable", True) from None
    try:
        async with pool.connection() as conn:
            # RLS: the lookup needs the workspace context, which is transaction-local (same pattern as the indexer).
            async with conn.transaction():
                await _begin(conn, wid)
                cur = await conn.execute(
                    "select name, type, storage_url, storage_key, deleted_at from files "
                    "where id = %s and workspace_id = %s",
                    (body.source_id, wid))
                row = await cur.fetchone()
            if row is None:
                raise _fail(404, "SOURCE_NOT_FOUND", "source not found", False)
            name, mime, storage_url, storage_key, deleted_at = row
            if deleted_at is not None:
                raise _fail(409, "SOURCE_DELETED", "source was deleted", False)
            if not storage_url:
                raise _fail(422, "NO_STORAGE_URL", "source has no stored original to ingest", False)
            token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
            try:
                data = await asyncio.to_thread(download_private_blob, storage_url, token)
            except BlobDownloadError as exc:
                raise _fail(503 if exc.retryable else 422, "STORAGE_UNAVAILABLE" if exc.retryable else "INVALID_DOCUMENT",
                            str(exc), exc.retryable) from None
            ver = await register_version(conn, wid, body.source_id, data, mime,
                                         created_by=principal.sub, storage_key=storage_key)
            if body.version_id is not None and body.version_id != ver.id:
                raise _fail(409, "VERSION_MISMATCH", "stored bytes do not match the requested version", False)
            profile = await load_index_profile(conn)
            res = await build_generation(conn, wid, body.source_id, ver, data, mime, embedder, name=name,
                                         profile=profile, deadline=time.monotonic() + TIME_BUDGET_S)
    except HTTPException:
        raise
    except IndexerError as exc:
        status = 409 if exc.code in ("SOURCE_DELETED", "VERSION_SUPERSEDED") else (404 if exc.code == "SOURCE_NOT_FOUND" else 500)
        raise _fail(status, exc.code, exc.message, exc.retryable) from None
    out = res.as_dict()
    out["timings_ms"]["total"] = int((time.monotonic() - started) * 1000)
    if res.status == "ACTIVE":
        return out
    status = 409 if res.status == "CANCELLED" else (503 if res.retryable else 422)
    raise HTTPException(status_code=status, detail=out)


# ---------------------------------------------------------------- T18: leased steps

STEP_BUDGET_S = float(os.environ.get("INGEST_STEP_BUDGET_S", "240"))


@router.post("/ingest/step")
async def ingest_step(principal: Principal = Depends(require_scope("ingest_step"))) -> dict:
    """Claim ONE job (any workspace; the workspace comes from the claimed row, never the request) and run it
    for up to STEP_BUDGET_S. Always 200 with an outcome; the caller re-fires while `more` is true."""
    from .ingest_steps import run_step

    settings = get_settings()
    try:
        embedder = embedder_from_env(settings.dsn)
    except FakeEmbedderRefused as exc:
        raise _fail(500, "FAKE_EMBEDDER_REFUSED", str(exc), False) from None
    try:
        pool = await get_pool()
        async with pool.connection() as conn:
            return await run_step(conn, embedder, budget_s=STEP_BUDGET_S,
                                  blob_token=os.environ.get("BLOB_READ_WRITE_TOKEN", ""))
    except RuntimeError:
        raise _fail(503, "DATABASE_UNAVAILABLE", "database unavailable", True) from None


@router.post("/ingest/sweep")
async def ingest_sweep(principal: Principal = Depends(require_scope("ingest_step"))) -> dict:
    from .ingest_steps import more_work, sweep

    try:
        pool = await get_pool()
        async with pool.connection() as conn:
            requeued = await sweep(conn)
            return {"swept": len(requeued), "job_ids": requeued, "more": await more_work(conn)}
    except RuntimeError:
        raise _fail(503, "DATABASE_UNAVAILABLE", "database unavailable", True) from None
