"""Background purge of soft-deleted Sources (T20, DESIGN B6 "Source deleted during indexing", INV-8/14).

Soft delete (`scribe_soft_delete_source`, drizzle/0016) only sets `files.deleted_at` and cancels the Source's jobs; that
alone removes it from retrieval and evidence reads. This module is the later, destructive half:

    list (app_list_purgeable_sources, grace period) -> collect every blob -> delete blobs -> delete rows -> file row

Order matters. Blobs go FIRST: if the rows were deleted first, a failed blob delete would leave blobs nobody can find again.
Blob deletion is idempotent, so a retry after a half-finished purge is safe. The database half is ONE transaction.

Failures never disappear: the Source keeps `purge_attempts`, `purge_next_retry_at` (exponential backoff, capped at one
day), `purge_error_code` and `purge_last_error`, and `app_list_purgeable_sources` hides it until the backoff has elapsed.

Blob deletion goes through the injectable `BlobStore` (tests use `FakeBlobStore`; production uses `VercelBlobStore`).
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence
from urllib.parse import urlparse

import httpx
import psycopg

from .indexer import _begin

DEFAULT_GRACE_SECONDS = 24 * 3600
MAX_BACKOFF_MINUTES = 24 * 60
VERCEL_BLOB_DELETE_URL = "https://blob.vercel-storage.com/delete"


class BlobDeleteError(Exception):
    """A blob could not be deleted. The message never contains the token."""

    def __init__(self, message: str, *, code: str = "BLOB_DELETE_FAILED", retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class BlobStore(Protocol):
    def delete(self, urls_or_pathnames: Sequence[str]) -> None:
        """Delete all of them (idempotent: an already-missing blob is not an error) or raise BlobDeleteError."""


class VercelBlobStore:
    """Vercel Blob delete API (POST /delete {urls: [...]}); accepts URLs and pathnames. Not exercised live in T20."""

    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None, timeout: float = 30.0,
                 batch_size: int = 100):
        self._token, self._transport, self._timeout, self._batch = token, transport, timeout, batch_size

    def delete(self, urls_or_pathnames: Sequence[str]) -> None:
        items = list(urls_or_pathnames)
        if not items:
            return
        if not self._token:
            raise BlobDeleteError("BLOB_READ_WRITE_TOKEN is not configured", code="STORAGE_UNAVAILABLE")
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                for i in range(0, len(items), self._batch):
                    resp = client.post(VERCEL_BLOB_DELETE_URL, json={"urls": items[i : i + self._batch]},
                                       headers={"Authorization": f"Bearer {self._token}"})
                    if resp.status_code >= 400:
                        raise BlobDeleteError(f"Blob delete failed with HTTP {resp.status_code}.",
                                              retryable=resp.status_code in (408, 429) or resp.status_code >= 500)
        except httpx.HTTPError as exc:
            raise BlobDeleteError(f"Blob delete failed ({type(exc).__name__}).") from exc


@dataclass
class FakeBlobStore:
    """In-memory store for tests: `blobs` is the set that still exists; `fail_next` makes the next N calls raise."""

    blobs: set[str] = field(default_factory=set)
    fail_next: int = 0
    calls: list[list[str]] = field(default_factory=list)

    def delete(self, urls_or_pathnames: Sequence[str]) -> None:
        items = list(urls_or_pathnames)
        self.calls.append(items)
        if self.fail_next > 0:
            self.fail_next -= 1
            raise BlobDeleteError("simulated storage outage")
        gone = {_blob_identity(i) for i in items}  # a URL and the pathname of the same blob are one blob
        self.blobs = {b for b in self.blobs if _blob_identity(b) not in gone}


def blob_store_from_env() -> BlobStore:
    return VercelBlobStore(os.environ.get("BLOB_READ_WRITE_TOKEN", ""))


# ------------------------------------------------------------------ pure helpers

def _blob_identity(ref: str) -> str:
    """URL and pathname of the same blob compare equal (a private blob URL is origin + '/' + pathname)."""
    if ref.startswith(("http://", "https://")):
        return urlparse(ref).path.lstrip("/")
    return ref.lstrip("/")


def dedupe_blob_refs(refs: Sequence[str | None]) -> list[str]:
    """Unique blobs, in first-seen order; when a URL and a pathname name the same blob the URL form wins."""
    chosen: dict[str, str] = {}
    for ref in refs:
        if not ref:
            continue
        key = _blob_identity(ref)
        if key not in chosen or (ref.startswith("http") and not chosen[key].startswith("http")):
            chosen[key] = ref
    return list(chosen.values())


def purge_backoff_minutes(attempts: int, rng: random.Random | Any = random) -> float:
    """2^n minutes (n = failed attempts so far) plus up to 25% jitter, capped at one day (the cron is daily)."""
    return min(MAX_BACKOFF_MINUTES, 2 ** max(1, attempts)) * (1 + rng.uniform(0, 0.25))


@dataclass
class PurgeResult:
    source_id: int
    status: str  # purged | retry_scheduled | skipped
    reason: str | None = None
    error_code: str | None = None
    blobs_deleted: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    retry_in_minutes: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, {}, 0) or k in ("source_id", "status")}


# ------------------------------------------------------------------ the purge

async def collect_blob_refs(conn: psycopg.AsyncConnection, workspace_id: str, source_id: int) -> list[str] | None:
    """Every blob that belongs to the Source: the original, plus the blob of EVERY version row (T19 stores a URL per
    version, including `duplicate_of` rows whose own upload is a separate blob). None => no such deleted Source."""
    async with conn.transaction():
        await _begin(conn, workspace_id)
        cur = await conn.execute(
            "select storage_url, storage_key, deleted_at from files where id = %s and workspace_id = %s",
            (source_id, workspace_id))
        row = await cur.fetchone()
        if row is None or row[2] is None:
            return None
        cur = await conn.execute(
            "select storage_key from source_versions where source_id = %s and workspace_id = %s "
            "order by version_number", (source_id, workspace_id))
        version_keys = [r[0] for r in await cur.fetchall()]
    # files.storage_url is the URL form of files.storage_key: URLs first so they win the de-duplication
    return dedupe_blob_refs([row[0], *version_keys, row[1]])


_DELETE_ORDER: tuple[tuple[str, str], ...] = (
    # (result key, SQL). %(s)s = source id, %(w)s = workspace. Children before parents; every statement is scoped.
    ("embeddings_1536", "delete from chunk_embeddings_1536 e using chunks c, source_versions v where c.id = e.chunk_id "
                        "and v.id = c.source_version_id and v.source_id = %(s)s and e.workspace_id = %(w)s"),
    ("embeddings_3072", "delete from chunk_embeddings_3072 e using chunks c, source_versions v where c.id = e.chunk_id "
                        "and v.id = c.source_version_id and v.source_id = %(s)s and e.workspace_id = %(w)s"),
    ("spans", "delete from chunk_source_spans sp using chunks c, source_versions v where c.id = sp.chunk_id "
              "and v.id = c.source_version_id and v.source_id = %(s)s and sp.workspace_id = %(w)s"),
    ("chunks", "delete from chunks c using source_versions v where v.id = c.source_version_id "
               "and v.source_id = %(s)s and c.workspace_id = %(w)s"),
    ("blocks", "delete from document_blocks b using source_versions v where v.id = b.source_version_id "
               "and v.source_id = %(s)s and b.workspace_id = %(w)s"),
    ("active_indexes", "delete from active_indexes where source_id = %(s)s and workspace_id = %(w)s"),
    ("generations", "delete from index_generations where source_id = %(s)s and workspace_id = %(w)s"),
    ("jobs", "delete from ingestion_jobs where file_id = %(s)s"),
    ("versions", "delete from source_versions where source_id = %(s)s and workspace_id = %(w)s"),
)


async def _delete_rows(conn: psycopg.AsyncConnection, workspace_id: str, source_id: int) -> dict[str, int] | None:
    """ONE transaction. None => the Source is not (or no longer) a deleted Source of this workspace."""
    params = {"s": source_id, "w": workspace_id}
    counts: dict[str, int] = {}
    async with conn.transaction():
        await _begin(conn, workspace_id)
        cur = await conn.execute("select deleted_at from files where id = %s and workspace_id = %s for update",
                                 (source_id, workspace_id))
        row = await cur.fetchone()
        if row is None or row[0] is None:
            return None
        # Evidence quotes / legacy trace previews are copies of document text: blank them (the rows themselves are
        # history; their chunk and version links are cleared by the FK ON DELETE SET NULL below).
        cur = await conn.execute(
            "update evidence_items e set quote = '' from source_versions v where v.id = e.source_version_id "
            "and v.source_id = %(s)s and e.workspace_id = %(w)s and e.quote <> ''", params)
        counts["evidence_scrubbed"] = cur.rowcount
        cur = await conn.execute(
            "update rag_trace_chunks set content_preview = '', metadata = null where file_id = %(s)s "
            "and exists (select 1 from rag_traces t where t.id = rag_trace_chunks.trace_id "
            "and t.workspace_id = %(w)s)", params)
        counts["trace_previews_scrubbed"] = cur.rowcount
        for key, sql in _DELETE_ORDER:
            cur = await conn.execute(sql, params)
            counts[key] = cur.rowcount
        cur = await conn.execute("delete from files where id = %s and workspace_id = %s and deleted_at is not null",
                                 (source_id, workspace_id))
        counts["files"] = cur.rowcount
    return counts


async def _record_failure(conn: psycopg.AsyncConnection, workspace_id: str, source_id: int, code: str, message: str,
                          rng) -> float | None:
    """Bump purge_attempts, set the backoff and make the failure visible. Returns the delay, None if it could not be
    recorded (database down: the Source stays due and the next sweep tries again)."""
    try:
        async with conn.transaction():
            await _begin(conn, workspace_id)
            cur = await conn.execute(
                "select purge_attempts from files where id = %s and workspace_id = %s for update",
                (source_id, workspace_id))
            row = await cur.fetchone()
            if row is None:
                return None
            minutes = purge_backoff_minutes(row[0] + 1, rng)
            await conn.execute(
                "update files set purge_attempts = purge_attempts + 1, purge_error_code = %s, purge_last_error = %s, "
                "purge_next_retry_at = now() + make_interval(secs => %s), updated_at = now() "
                "where id = %s and workspace_id = %s", (code, message[:500], minutes * 60, source_id, workspace_id))
            return minutes
    except psycopg.Error:
        return None


async def purge_source(conn: psycopg.AsyncConnection, workspace_id: str, source_id: int, store: BlobStore, *,
                       rng: random.Random | Any = random) -> PurgeResult:
    """Purge one soft-deleted Source. `conn` must be autocommit (each step manages its own transaction)."""
    try:
        refs = await collect_blob_refs(conn, workspace_id, source_id)
    except psycopg.Error as exc:
        return PurgeResult(source_id, "skipped", reason=f"database unavailable ({type(exc).__name__})",
                           error_code="DATABASE_UNAVAILABLE")
    if refs is None:
        return PurgeResult(source_id, "skipped", reason="not a deleted Source of this workspace")
    try:
        await asyncio.to_thread(store.delete, refs)
    except BlobDeleteError as exc:
        minutes = await _record_failure(conn, workspace_id, source_id, exc.code, str(exc), rng)
        return PurgeResult(source_id, "retry_scheduled", error_code=exc.code, reason=str(exc)[:200],
                           retry_in_minutes=minutes)
    except Exception as exc:  # noqa: BLE001 - a misbehaving store must not stop the sweep or hide the row
        minutes = await _record_failure(conn, workspace_id, source_id, "BLOB_DELETE_FAILED",
                                        f"unexpected {type(exc).__name__}", rng)
        return PurgeResult(source_id, "retry_scheduled", error_code="BLOB_DELETE_FAILED",
                           reason=f"unexpected {type(exc).__name__}", retry_in_minutes=minutes)
    try:
        counts = await _delete_rows(conn, workspace_id, source_id)
    except psycopg.Error as exc:
        minutes = await _record_failure(conn, workspace_id, source_id, "DATABASE_UNAVAILABLE",
                                        f"row deletion failed ({type(exc).__name__})", rng)
        return PurgeResult(source_id, "retry_scheduled", error_code="DATABASE_UNAVAILABLE",
                           reason=f"row deletion failed ({type(exc).__name__})", retry_in_minutes=minutes)
    if counts is None:
        return PurgeResult(source_id, "skipped", reason="not a deleted Source of this workspace")
    return PurgeResult(source_id, "purged", blobs_deleted=len(refs), counts=counts)


async def purge_pending(conn: psycopg.AsyncConnection, store: BlobStore, *,
                        older_than_seconds: int = DEFAULT_GRACE_SECONDS, limit: int = 20,
                        budget_s: float | None = None, rng: random.Random | Any = random) -> dict[str, Any]:
    """The sweep: purge soft-deleted Sources past the grace period (and past their retry backoff)."""
    deadline = time.monotonic() + budget_s if budget_s else None
    rows = await (await conn.execute("select source_id, workspace_id from app_list_purgeable_sources(%s, %s)",
                                     (older_than_seconds, limit))).fetchall()
    results: list[PurgeResult] = []
    for source_id, wid in rows:
        if deadline is not None and time.monotonic() > deadline:
            break
        results.append(await purge_source(conn, str(wid), source_id, store, rng=rng))
    summary = {s: sum(1 for r in results if r.status == s) for s in ("purged", "retry_scheduled", "skipped")}
    return {"listed": len(rows), **summary, "results": [r.as_dict() for r in results],
            "more": len(rows) >= limit}
