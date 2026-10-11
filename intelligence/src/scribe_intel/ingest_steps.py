"""Leased, resumable ingestion steps (T18, DESIGN B3, INV-5).

One `run_step` claims ONE job through the SECURITY DEFINER `app_claim_ingestion_job` (fresh lease_token),
then runs PARSE -> (OCR, T21) -> CHUNK -> EMBED (batched, resumes from the embedded count) -> ACTIVATE inside a time budget.
OCR transcribes at most OCR_PAGES_PER_STEP scanned pages per invocation; each finished page is committed (with its
idempotent usage event) before the next one starts, so a yielded or killed step resumes at the first page without a row.
All progress is written by the existing indexer stages with a `Fence`: `UPDATE ingestion_jobs ... WHERE
lease_token = :mine` in the same transaction as the work; zero rows => LeaseLost, rollback, write nothing.

Attempts: the claim increments `attempts`. A step that merely ran out of time (progress was made) gives the
attempt back, so a large document can take many steps without exhausting its retries.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import psycopg

from scribe_ingest.blob import BlobDownloadError, download_private_blob
from scribe_ingest.chunking import STRUCTURAL_V1, chunk_document
from scribe_ingest.embedding import Embedder, EmbeddingError
from scribe_ingest.errors import IngestError
from scribe_ingest.indexer import (
    PERMANENT_CODES,
    ActivationRefused,
    Fence,
    IndexerError,
    LeaseLost,
    _begin,
    _check_still_desired,
    activate_generation,
    begin_reserved_version,
    embed_generation,
    ensure_generation,
    fail_generation,
    load_index_profile,
    load_stored_version,
    parse_for_index,
    persist_chunks,
    register_version,
    resolve_ocr,
    verify_stored_bytes,
)
from scribe_ingest.model import BLOCK_SEPARATOR
from scribe_ingest.ocr import OcrClient, OcrError

LEASE_SECONDS = 330
MAX_LIVE_LEASES = 3
MAX_ATTEMPTS = 5
UNKNOWN_MAX_ATTEMPTS = 2
CLAIM_LOCK_KEY = "scribe:ingest:claim"

TRANSIENT_CODES = frozenset({"RATE_LIMITED", "TRANSIENT_PROVIDER", "DATABASE_UNAVAILABLE", "STORAGE_UNAVAILABLE"})
PERMANENT = frozenset({"INVALID_DOCUMENT", "UNSUPPORTED_FORMAT", "RESOURCE_LIMIT", "EMPTY_EXTRACTION"}) | PERMANENT_CODES


# ------------------------------------------------------------------ retry taxonomy

def classify(code: str) -> str:
    """'transient' | 'permanent' | 'unknown'."""
    if code in TRANSIENT_CODES:
        return "transient"
    if code in PERMANENT:
        return "permanent"
    return "unknown"


def backoff_minutes(attempts: int, rng: random.Random | Any = random) -> float:
    """2^n minutes (n = attempts so far, capped at 16 like the legacy path) plus up to 25% jitter."""
    base = min(16, 2 ** max(1, attempts))
    return base * (1 + rng.uniform(0, 0.25))


@dataclass(frozen=True)
class Decision:
    action: str  # "retry" | "fail"
    delay_minutes: float = 0.0


def decide(code: str, attempts: int, rng: random.Random | Any = random) -> Decision:
    kind = classify(code)
    if kind == "permanent":
        return Decision("fail")
    limit = MAX_ATTEMPTS if kind == "transient" else UNKNOWN_MAX_ATTEMPTS
    if attempts >= limit:
        return Decision("fail")
    return Decision("retry", backoff_minutes(attempts, rng))


def error_from_exception(exc: BaseException) -> tuple[str, str]:
    """(code, plain message safe to show to a user). Unknown exceptions never leak their text."""
    if isinstance(exc, IngestError):
        return exc.code, exc.message
    if isinstance(exc, (EmbeddingError, OcrError)):
        return exc.code, exc.message
    if isinstance(exc, IndexerError):
        return exc.code, exc.message
    if isinstance(exc, BlobDownloadError):
        return ("STORAGE_UNAVAILABLE", str(exc)) if exc.retryable else ("INVALID_DOCUMENT", str(exc))
    if isinstance(exc, (psycopg.OperationalError, psycopg.errors.AdminShutdown)):
        return "DATABASE_UNAVAILABLE", "database unavailable"
    return "INTERNAL", f"unexpected {type(exc).__name__}"


# ------------------------------------------------------------------ fenced job-state writes

async def _job_update(conn, wid: str, fence: Fence, sql: str, params: tuple) -> None:
    """Run one UPDATE ingestion_jobs ... WHERE id = job AND lease_token = mine; zero rows => LeaseLost."""
    async with conn.transaction():
        await _begin(conn, wid)
        cur = await conn.execute(sql + " where id = %s and lease_token = %s", params + (fence.job_id, fence.lease_token))
        if cur.rowcount == 0:
            raise LeaseLost(f"lease for job {fence.job_id} was lost")


async def _terminal(conn, wid, fence: Fence, status: str, stage: str, code: str | None, message: str | None) -> None:
    await _job_update(
        conn, wid, fence,
        "update ingestion_jobs set status = %s, stage = %s, error_code = %s, last_error = %s, finished_at = now(), "
        "lease_token = null, lease_expires_at = null, next_retry_at = null, updated_at = now()",
        (status, stage, code, (message or "")[:500] or None))


async def _yield_job(conn, wid, fence: Fence, stage: str = "EMBED") -> None:
    """Out of time with progress saved: back to the queue and hand the attempt back."""
    await _job_update(
        conn, wid, fence,
        "update ingestion_jobs set status = 'queued', stage = %s, attempts = greatest(attempts - 1, 0), "
        "lease_token = null, lease_expires_at = null, next_retry_at = null, updated_at = now()", (stage,))


async def _schedule_retry(conn, wid, fence: Fence, code: str, message: str, minutes: float) -> None:
    await _job_update(
        conn, wid, fence,
        "update ingestion_jobs set status = 'retrying', error_code = %s, last_error = %s, "
        "next_retry_at = now() + make_interval(secs => %s), "
        "lease_token = null, lease_expires_at = null, updated_at = now()",
        (code, message[:500], minutes * 60))
    async with conn.transaction():
        await _begin(conn, wid)
        await conn.execute(
            "update files set status = 'retrying', processing_error = %s, updated_at = now() "
            "where id = (select file_id from ingestion_jobs where id = %s) and active_version_id is null "
            "and deleted_at is null", (message[:500], fence.job_id))


# ------------------------------------------------------------------ the step

def _out(**kw: Any) -> dict[str, Any]:
    return {"claimed": True, "more": False, **kw}


async def claim(conn, *, max_live: int, lease_seconds: int, max_attempts: int):
    """Atomically (advisory lock) enforce the live-lease cap and claim one job. Returns the row or a reason string."""
    async with conn.transaction():
        await conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (CLAIM_LOCK_KEY,))
        live = (await (await conn.execute("select app_count_live_leases()")).fetchone())[0]
        if live >= max_live:
            return "at_capacity"
        cur = await conn.execute("select * from app_claim_ingestion_job(%s, %s)", (lease_seconds, max_attempts))
        row = await cur.fetchone()
    return row if row else "no_work"


async def more_work(conn) -> bool:
    try:
        return (await (await conn.execute("select app_count_claimable_jobs()")).fetchone())[0] > 0
    except psycopg.Error:
        return False


async def run_step(
    conn: psycopg.AsyncConnection, embedder: Embedder, *, budget_s: float = 240.0, max_live: int = MAX_LIVE_LEASES,
    lease_seconds: int = LEASE_SECONDS, download: Callable[..., bytes] = download_private_blob, blob_token: str = "",
    rng: random.Random | Any = random, ocr: OcrClient | None = None, ocr_pages_per_step: int | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + budget_s
    got = await claim(conn, max_live=max_live, lease_seconds=lease_seconds, max_attempts=MAX_ATTEMPTS)
    if isinstance(got, str):
        # at capacity: do not ask for an immediate re-fire (the lease holders re-fire when they finish)
        return {"claimed": False, "reason": got, "more": got != "at_capacity" and await more_work(conn)}
    job_id, file_id, wid, lease = got
    wid = str(wid)
    fence = Fence(job_id, lease, lease_seconds)
    out = _out(job_id=job_id, file_id=file_id)
    attempts = 0
    gen_id: uuid.UUID | None = None
    try:
        async with conn.transaction():
            await _begin(conn, wid)
            f = await (await conn.execute(
                "select name, type, storage_url, storage_key, deleted_at, user_id from files "
                "where id = %s and workspace_id = %s", (file_id, wid))).fetchone()
            j = await (await conn.execute(
                "select stage, source_version_id, generation_id, attempts from ingestion_jobs where id = %s",
                (job_id,))).fetchone()
        stage, ver_id, gen_id, attempts = j
        out["attempts"] = attempts
        if f is None or f[4] is not None:
            await _terminal(conn, wid, fence, "cancelled", "CANCELLED", "SOURCE_DELETED", "source was deleted")
            return {**out, "outcome": "cancelled", "stage": "CANCELLED", "more": await more_work(conn)}
        name, mime, storage_url, storage_key, _, user_id = f
        async with conn.transaction():  # fenced: show "processing" only while we really hold the lease
            await _begin(conn, wid)
            await fence.touch(conn)
            await conn.execute("update files set status = 'processing', processing_error = null, updated_at = now() "
                               "where id = %s and workspace_id = %s and status in ('queued','retrying')", (file_id, wid))
        profile = await load_index_profile(conn)

        text_bytes = extracted = None
        if stage == "EMBED" and ver_id and gen_id:
            # resume: chunks are already persisted; check the step boundary (SUPERSEDED / CANCELLED), then embed
            async with conn.transaction():
                await _begin(conn, wid)
                await _check_still_desired(conn, wid, file_id, ver_id)
                rows = await (await conn.execute(
                    "select text, coalesce((meta->>'furniture')::boolean, false) from document_blocks "
                    "where source_version_id = %s and workspace_id = %s order by block_index", (ver_id, wid))).fetchall()
            extracted = BLOCK_SEPARATOR.join(r[0] for r in rows if not r[1])
            text_bytes = len(extracted)
        else:
            stored = await load_stored_version(conn, wid, ver_id) if ver_id else None
            if stored and (stored["storage_key"] or "").startswith("http"):
                # T19: a version TS reserved and stored (desired_version_id already points at it). Its own blob, its own
                # mime; never re-register it (that would re-point desired_version_id and defeat cancellation).
                async with conn.transaction():  # step boundary: a newer upload / a delete cancels before any download
                    await _begin(conn, wid)
                    await _check_still_desired(conn, wid, file_id, ver_id)
                data = await asyncio.to_thread(download, stored["storage_key"], blob_token)
                verify_stored_bytes(stored, data)
                mime = stored["mime_type"] or mime
                await begin_reserved_version(conn, wid, file_id, ver_id, fence)
            else:
                if not storage_url:
                    raise IndexerError("INVALID_DOCUMENT", "source has no stored original to ingest")
                data = await asyncio.to_thread(download, storage_url, blob_token)
                ver = await register_version(conn, wid, file_id, data, mime, created_by=user_id,
                                             storage_key=storage_key, fence=fence)
                ver_id = ver.id
            gen_id, prior = await ensure_generation(conn, wid, file_id, ver_id, profile, fence=fence)
            if prior == "ACTIVE":  # these bytes are already live: nothing to build
                async with conn.transaction():
                    await _begin(conn, wid)
                    await fence.touch(conn, stage="DONE", complete=True)
                return {**out, "outcome": "completed", "stage": "DONE", "more": await more_work(conn)}
            doc = parse_for_index(data, mime, name)
            if doc.needs_ocr_pages:
                # stage OCR is set by the fenced page writes; a yield (OCR_PENDING) below re-queues at stage OCR and the
                # next claim re-runs this path (cheap: re-download + re-parse) and skips the pages already stored
                doc = await resolve_ocr(
                    conn, wid, file_id, ver_id, gen_id, data, doc, ocr, fence=fence, deadline=deadline,
                    **({} if ocr_pages_per_step is None else {"max_pages": ocr_pages_per_step}),
                    usage_user_id=user_id)
            chunkset = chunk_document(doc, STRUCTURAL_V1)
            await persist_chunks(conn, wid, file_id, ver_id, gen_id, doc, chunkset, fence=fence)
            text_bytes, extracted = len(doc.text), doc.text

        embedded, expected = await embed_generation(
            conn, wid, file_id, ver_id, gen_id, profile, embedder, deadline=deadline, fence=fence,
            usage_user_id=user_id)
        out.update(embedded=embedded, expected=expected)
        await activate_generation(conn, wid, file_id, ver_id, gen_id, profile, text_bytes=text_bytes,
                                  extracted_text=extracted, fence=fence)
        return {**out, "outcome": "completed", "stage": "DONE", "more": await more_work(conn)}
    except LeaseLost:
        # INV-5: someone else owns the job now. Write nothing.
        return {**out, "outcome": "lease_lost", "more": await more_work(conn)}
    except ActivationRefused as exc:
        status = "cancelled" if exc.code == "SOURCE_DELETED" else "superseded"
        try:
            await _terminal(conn, wid, fence, status, status.upper(), exc.code, exc.message)
        except LeaseLost:
            return {**out, "outcome": "lease_lost"}
        if gen_id:
            await _safe(fail_generation(conn, wid, file_id, gen_id, exc.code, exc.message, cancelled=True))
        return {**out, "outcome": status, "error_code": exc.code, "more": await more_work(conn)}
    except IndexerError as exc:
        if exc.code in ("TIME_BUDGET", "OCR_PENDING"):
            stage = "OCR" if exc.code == "OCR_PENDING" else "EMBED"
            try:
                await _yield_job(conn, wid, fence, stage)
            except LeaseLost:
                return {**out, "outcome": "lease_lost"}
            return {**out, "outcome": "yielded", "stage": stage, "more": True}
        return await _handle_failure(conn, wid, fence, file_id, gen_id, out, attempts, exc, rng)
    except Exception as exc:  # noqa: BLE001 - classified below; never swallowed
        return await _handle_failure(conn, wid, fence, file_id, gen_id, out, attempts, exc, rng)


async def _safe(coro) -> None:
    try:
        await coro
    except psycopg.Error:
        pass


async def _handle_failure(conn, wid, fence, file_id, gen_id, out, attempts, exc, rng) -> dict[str, Any]:
    code, message = error_from_exception(exc)
    d = decide(code, attempts, rng)
    out.update(error_code=code, message=message)
    try:
        if d.action == "retry":
            await _schedule_retry(conn, wid, fence, code, message, d.delay_minutes)
            return {**out, "outcome": "retry_scheduled", "retry_in_minutes": d.delay_minutes,
                    "more": await more_work(conn)}
        await _terminal(conn, wid, fence, "failed", "FAILED", code, message)
    except LeaseLost:
        return {**out, "outcome": "lease_lost"}
    except psycopg.Error:
        return {**out, "outcome": "lease_held_until_sweep"}  # database down: the sweep will requeue the job
    if gen_id:
        await _safe(fail_generation(conn, wid, file_id, gen_id, code, message))
    else:
        await _safe(_fail_file(conn, wid, file_id, message))
    return {**out, "outcome": "failed", "stage": "FAILED", "more": await more_work(conn)}


async def _fail_file(conn, wid: str, file_id: int, message: str) -> None:
    async with conn.transaction():
        await _begin(conn, wid)
        await conn.execute(
            "update files set status = 'failed', processing_error = %s, updated_at = now() "
            "where id = %s and workspace_id = %s and active_version_id is null and deleted_at is null",
            (message[:500], file_id, wid))


async def sweep(conn) -> list[int]:
    """Requeue expired leases (or fail them once attempts are exhausted)."""
    rows = await (await conn.execute("select job_id from app_sweep_expired_leases(%s)", (MAX_ATTEMPTS,))).fetchall()
    return [r[0] for r in rows]
