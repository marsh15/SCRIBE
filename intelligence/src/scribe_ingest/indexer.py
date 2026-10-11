"""Single-invocation indexing pipeline for ONE source version (DESIGN B3).

Stages are separate, idempotent coroutines so T18 can later run them as leased steps:

    register_version -> ensure_generation -> persist_chunks (PARSE+CHUNK) ->
    embed_generation (EMBED, resumable) -> activate_generation (ACTIVATE)

`build_generation` runs them all and converts every failure into a FAILED generation with an
error_code (INV-3: the previously ACTIVE generation is never touched before ACTIVATE).

Decisions (see docs/scribe2/T12-notes.md)
* Only CHILD chunks are embedded and searched (D9); parents are stored for context expansion.
  `expected_chunks` = number of child chunks.
* Every transaction starts with set_config('app.workspace_id', ..., true) and every query is
  filtered by workspace_id (RLS arrives in T15).
* The connection must be in autocommit mode; transactions are explicit (`conn.transaction()`).
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from . import parse
from .chunking import STRUCTURAL_V1, ChunkProfile, ChunkSet, chunk_document
from .embedding import BATCH_SIZE, Embedder, EmbeddingError
from .errors import IngestError
from .model import ParsedDocument
from .ocr import OCR_PAGES_PER_STEP, OcrClient, OcrError, apply_ocr, check_ocr_limit, clean_ocr_text, render_page_png

INDEX_PROFILE_NAME = "structural-v1"
OCR_UNAVAILABLE_MESSAGE = "Text recognition for scanned pages is not available on this server."
PERMANENT_CODES = {
    "INVALID_DOCUMENT", "UNSUPPORTED_FORMAT", "RESOURCE_LIMIT", "EMPTY_EXTRACTION",
    "OCR_NOT_AVAILABLE", "PROFILE_MISMATCH", "INVALID_REQUEST", "AUTH",
}


class IndexerError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class LeaseLost(Exception):
    """INV-5: this worker no longer holds the job lease; the fenced write touched zero rows and was rolled back."""


@dataclass(frozen=True)
class Fence:
    """Lease fence (DESIGN B3). `touch` runs INSIDE the transaction that writes progress: it updates the job row
    WHERE lease_token = mine; zero rows means the lease was lost, so LeaseLost aborts (and rolls back) the whole
    transaction and nothing is recorded."""
    job_id: int
    lease_token: uuid.UUID
    lease_seconds: int = 330

    async def touch(self, conn: psycopg.AsyncConnection, *, stage: str | None = None,
                    generation_id: uuid.UUID | None = None, version_id: uuid.UUID | None = None,
                    complete: bool = False) -> None:
        cur = await conn.execute(
            "update ingestion_jobs set lease_expires_at = now() + make_interval(secs => %s), updated_at = now(), "
            "stage = coalesce(%s, stage), generation_id = coalesce(%s, generation_id), "
            "source_version_id = coalesce(%s, source_version_id), "
            "status = case when %s then 'completed' else status end, "
            "finished_at = case when %s then now() else finished_at end, "
            "last_error = case when %s then null else last_error end, "
            "error_code = case when %s then null else error_code end "
            "where id = %s and lease_token = %s and status = 'processing'",
            (self.lease_seconds, stage, generation_id, version_id, complete, complete, complete, complete,
             self.job_id, self.lease_token))
        if cur.rowcount == 0:
            raise LeaseLost(f"lease for job {self.job_id} was lost")


class ActivationRefused(IndexerError):
    """INV-3/INV-4: the version is no longer the desired one, or the source is deleted."""


@dataclass
class IndexProfile:
    id: uuid.UUID
    name: str
    parser_profile: str
    chunk_profile_id: uuid.UUID
    chunk_config: dict
    embedding_profile_id: uuid.UUID
    embedding_model: str
    embedding_dims: int


@dataclass
class VersionRef:
    id: uuid.UUID
    version_number: int
    sha256: str
    created: bool


@dataclass
class IngestResult:
    status: str  # ACTIVE | FAILED | CANCELLED | BUILDING (budget exhausted; resumable)
    source_id: int
    version_id: uuid.UUID | None = None
    generation_id: uuid.UUID | None = None
    blocks: int = 0
    parents: int = 0
    children: int = 0
    expected: int = 0
    embedded: int = 0
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    message: str | None = None
    retryable: bool = False
    timings_ms: dict[str, int] = field(default_factory=dict)

    @property
    def chunks(self) -> int:
        return self.parents + self.children

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "source_id": self.source_id,
            "version_id": str(self.version_id) if self.version_id else None,
            "generation_id": str(self.generation_id) if self.generation_id else None,
            "blocks": self.blocks, "parents": self.parents, "children": self.children,
            "chunks": self.chunks, "expected": self.expected, "embedded": self.embedded,
            "warnings": self.warnings, "error_code": self.error_code, "message": self.message,
            "retryable": self.retryable, "timings_ms": self.timings_ms,
        }


# ------------------------------------------------------------------ helpers

async def _begin(conn: psycopg.AsyncConnection, workspace_id: str) -> None:
    await conn.execute("select set_config('app.workspace_id', %s, true)", (str(workspace_id),))


def _vec(v: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _require_autocommit(conn: psycopg.AsyncConnection) -> None:
    if not conn.autocommit:
        raise RuntimeError("indexer requires an autocommit connection (it manages its own transactions)")


# ------------------------------------------------------------------ profile

async def load_index_profile(conn: psycopg.AsyncConnection, name: str = INDEX_PROFILE_NAME,
                             chunk_profile: ChunkProfile = STRUCTURAL_V1) -> IndexProfile:
    """Load the index profile and verify the DB chunk config agrees with the code profile."""
    cur = await conn.execute(
        """select ip.id, ip.name, ip.parser_profile, ip.chunk_profile_id, cp.config,
                  ip.embedding_profile_id, ep.model, ep.dimensions
           from index_profiles ip
           join chunk_profiles cp on cp.id = ip.chunk_profile_id
           join embedding_profiles ep on ep.id = ip.embedding_profile_id
           where ip.name = %s""", (name,))
    row = await cur.fetchone()
    if row is None:
        raise IndexerError("PROFILE_MISMATCH", f"index profile {name!r} not found (run migration 0011)")
    prof = IndexProfile(*row)
    cfg = prof.chunk_config
    expected = {
        "child_max_tokens": chunk_profile.child_max_tokens,
        "overlap_tokens": chunk_profile.overlap_tokens,
        "parent_target_tokens": chunk_profile.parent_target_tokens,
        "tokenizer": chunk_profile.tokenizer_id.split(":")[-1],
    }
    diffs = {k: (cfg.get(k), v) for k, v in expected.items() if cfg.get(k) != v}
    if diffs:
        raise IndexerError(
            "PROFILE_MISMATCH",
            f"chunk profile {name!r} in the database disagrees with the code profile "
            f"(key: (db, code)): {diffs}. Create a new profile row; do not edit the old one.",
        )
    if prof.embedding_dims != 1536:
        raise IndexerError("PROFILE_MISMATCH", "chunk_embeddings_1536 requires a 1536-dim embedding profile")
    return prof


# ------------------------------------------------------------------ REGISTER

async def register_version(
    conn: psycopg.AsyncConnection, workspace_id: str, source_id: int, data: bytes, mime: str,
    *, created_by: str | None = None, storage_key: str | None = None, fence: Fence | None = None,
) -> VersionRef:
    """Insert source_versions v(n) for these bytes if no version of this source has the same
    sha256 (the sha is frozen after insert, so the row is only created once bytes are known),
    and point files.desired_version_id at it."""
    _require_autocommit(conn)
    sha = hashlib.sha256(data).hexdigest()
    async with conn.transaction():
        await _begin(conn, workspace_id)
        if fence:
            await fence.touch(conn, stage="PARSE")
        cur = await conn.execute(
            "select deleted_at from files where id = %s and workspace_id = %s for update",
            (source_id, workspace_id))
        row = await cur.fetchone()
        if row is None:
            raise IndexerError("SOURCE_NOT_FOUND", "source not found in this workspace")
        if row[0] is not None:
            raise ActivationRefused("SOURCE_DELETED", "source was deleted")
        cur = await conn.execute(
            "select id, version_number from source_versions where source_id = %s and workspace_id = %s "
            "and sha256 = %s order by version_number desc limit 1", (source_id, workspace_id, sha))
        existing = await cur.fetchone()
        if existing:
            ref = VersionRef(existing[0], existing[1], sha, False)
        else:
            cur = await conn.execute(
                "select coalesce(max(version_number), 0) + 1 from source_versions "
                "where source_id = %s and workspace_id = %s", (source_id, workspace_id))
            n = (await cur.fetchone())[0]
            cur = await conn.execute(
                "insert into source_versions(source_id, workspace_id, version_number, storage_key, sha256, "
                "size_bytes, mime_type, upload_state, created_by) values (%s,%s,%s,%s,%s,%s,%s,'stored',%s) "
                "returning id", (source_id, workspace_id, n, storage_key, sha, len(data), mime, created_by))
            ref = VersionRef((await cur.fetchone())[0], n, sha, True)
        await conn.execute(
            "update files set desired_version_id = %s, updated_at = now() where id = %s and workspace_id = %s",
            (ref.id, source_id, workspace_id))
    return ref


async def load_stored_version(conn: psycopg.AsyncConnection, workspace_id: str, version_id: uuid.UUID) -> dict[str, Any]:
    """The reserved-and-stored source_versions row a T19 job points at (None fields mean 'not stored yet')."""
    async with conn.transaction():
        await _begin(conn, workspace_id)
        cur = await conn.execute(
            "select version_number, storage_key, sha256, mime_type, upload_state, duplicate_of from source_versions "
            "where id = %s and workspace_id = %s", (version_id, workspace_id))
        row = await cur.fetchone()
    if row is None:
        raise IndexerError("INVALID_DOCUMENT", "the version this job points at no longer exists")
    keys = ("version_number", "storage_key", "sha256", "mime_type", "upload_state", "duplicate_of")
    return dict(zip(keys, row))


def verify_stored_bytes(version: dict[str, Any], data: bytes) -> None:
    """The bytes fetched from storage must be the bytes whose sha256 was recorded when the version was stored."""
    if version["upload_state"] != "stored" or not version["sha256"]:
        raise IndexerError("INVALID_DOCUMENT", "the uploaded version was never stored")
    if hashlib.sha256(data).hexdigest() != version["sha256"]:
        raise IndexerError("INVALID_DOCUMENT", "the stored file does not match its recorded checksum")


async def begin_reserved_version(conn: psycopg.AsyncConnection, workspace_id: str, source_id: int,
                                 version_id: uuid.UUID, fence: Fence) -> None:
    """Counterpart of `register_version` for a version that TS already reserved/stored and pointed
    desired_version_id at: fence the job at PARSE and refuse (ActivationRefused) if it is no longer desired."""
    async with conn.transaction():
        await _begin(conn, workspace_id)
        await fence.touch(conn, stage="PARSE", version_id=version_id)
        await _check_still_desired(conn, workspace_id, source_id, version_id, lock=True)


# ------------------------------------------------------------------ GENERATION

async def ensure_generation(conn: psycopg.AsyncConnection, workspace_id: str, source_id: int,
                            version_id: uuid.UUID, profile: IndexProfile,
                            fence: Fence | None = None) -> tuple[uuid.UUID, str]:
    """Create the generation (BUILDING) or reset a non-ACTIVE one for a rebuild. Returns (id, status)
    where status is the status BEFORE any reset (ACTIVE means: nothing to do)."""
    async with conn.transaction():
        await _begin(conn, workspace_id)
        if fence:
            await fence.touch(conn, stage="CHUNK", version_id=version_id)
        await conn.execute("select 1 from files where id = %s and workspace_id = %s for update",
                           (source_id, workspace_id))
        cur = await conn.execute(
            "select id, status from index_generations where source_version_id = %s and index_profile_id = %s "
            "and workspace_id = %s for update", (version_id, profile.id, workspace_id))
        row = await cur.fetchone()
        if row is None:
            cur = await conn.execute(
                "insert into index_generations(source_id, source_version_id, index_profile_id, workspace_id) "
                "values (%s,%s,%s,%s) returning id", (source_id, version_id, profile.id, workspace_id))
            return (await cur.fetchone())[0], "NEW"
        gen_id, status = row
        if status != "ACTIVE":
            await conn.execute(
                "update index_generations set status='BUILDING', error_code=NULL, ready_at=NULL, "
                "superseded_at=NULL, updated_at=now() where id=%s and workspace_id=%s", (gen_id, workspace_id))
        return gen_id, status


async def _check_still_desired(conn: psycopg.AsyncConnection, workspace_id: str, source_id: int,
                               version_id: uuid.UUID, *, lock: bool = False) -> None:
    cur = await conn.execute(
        "select deleted_at, desired_version_id from files where id = %s and workspace_id = %s"
        + (" for update" if lock else ""), (source_id, workspace_id))
    row = await cur.fetchone()
    if row is None or row[0] is not None:
        raise ActivationRefused("SOURCE_DELETED", "source was deleted while indexing")
    if row[1] != version_id:
        raise ActivationRefused("VERSION_SUPERSEDED", "a newer version of this source was uploaded")


def parse_for_index(data: bytes, mime: str, name: str | None) -> ParsedDocument:
    """Parse only. A scanned/mixed PDF comes back with `needs_ocr_pages` set; `resolve_ocr` turns those pages into
    OCR blocks (T21)."""
    return parse(data, mime, allow_ocr_pending=True, name=name)


async def _load_ocr_pages(conn: psycopg.AsyncConnection, workspace_id: str, version_id: uuid.UUID) -> dict[int, str]:
    async with conn.transaction():
        await _begin(conn, workspace_id)
        cur = await conn.execute(
            "select page, text from source_version_ocr_pages where source_version_id = %s and workspace_id = %s",
            (version_id, workspace_id))
        return {r[0]: r[1] for r in await cur.fetchall()}


async def _save_ocr_page(
    conn: psycopg.AsyncConnection, workspace_id: str, source_id: int, version_id: uuid.UUID,
    generation_id: uuid.UUID, page: int, text: str, model: str, *, fence: Fence | None, usage_user_id: str | None,
) -> None:
    """One page = one transaction: lease fence, 'still desired' check, the page row and its idempotent usage event
    commit together, so a killed worker neither loses a finished page nor double-counts it."""
    async with conn.transaction():
        await _begin(conn, workspace_id)
        if fence:
            await fence.touch(conn, stage="OCR")
        await _check_still_desired(conn, workspace_id, source_id, version_id)
        await conn.execute(
            "insert into source_version_ocr_pages(source_version_id, workspace_id, page, text, model) "
            "values (%s,%s,%s,%s,%s) on conflict (source_version_id, page) do nothing",
            (version_id, workspace_id, page, text, model))
        if usage_user_id:
            await conn.execute(
                "insert into usage_events(user_id, metric, quantity, unit, source_type, source_id, is_estimated, "
                "idempotency_key) values (%s,'ocr_pages',1,'pages','ingest',%s,false,%s) "
                "on conflict (idempotency_key) do nothing",
                (usage_user_id, str(source_id), f"gen:{generation_id}:ocr:{page}"))


async def resolve_ocr(
    conn: psycopg.AsyncConnection, workspace_id: str, source_id: int, version_id: uuid.UUID,
    generation_id: uuid.UUID, data: bytes, doc: ParsedDocument, ocr: OcrClient | None, *,
    fence: Fence | None = None, deadline: float | None = None, max_pages: int | None = OCR_PAGES_PER_STEP,
    usage_user_id: str | None = None,
) -> ParsedDocument:
    """OCR the pages the parser flagged (T21) and return the merged document.

    * More than 50 flagged pages -> RESOURCE_LIMIT before any provider call (D21).
    * Finished pages are read back from `source_version_ocr_pages`, so a killed/yielded step never redoes them.
    * At most `max_pages` pages (and at least one) per invocation, and none after `deadline`; when pages remain this
      raises IndexerError('OCR_PENDING') and the caller yields the job (progress is already committed).
    * Page text is untrusted (INV-13): never logged, never in an error message."""
    if not doc.needs_ocr_pages:
        return doc
    check_ocr_limit(doc.needs_ocr_pages)
    done = await _load_ocr_pages(conn, workspace_id, version_id)
    todo = [p for p in doc.needs_ocr_pages if p not in done]
    if todo and ocr is None:
        raise IndexerError("OCR_NOT_AVAILABLE", OCR_UNAVAILABLE_MESSAGE)
    processed = 0
    for page in todo:
        if processed and ((max_pages is not None and processed >= max_pages)
                          or (deadline is not None and time.monotonic() > deadline)):
            raise IndexerError("OCR_PENDING", "text recognition continues in the next step", retryable=True)
        png = await asyncio.to_thread(render_page_png, data, page)
        res = await ocr.transcribe(png, page=page)  # type: ignore[union-attr]
        text = clean_ocr_text(res.text)
        await _save_ocr_page(conn, workspace_id, source_id, version_id, generation_id, page, text, ocr.model,  # type: ignore[union-attr]
                             fence=fence, usage_user_id=usage_user_id)
        done[page] = text
        processed += 1
    return apply_ocr(doc, done)


async def persist_chunks(
    conn: psycopg.AsyncConnection, workspace_id: str, source_id: int, version_id: uuid.UUID,
    generation_id: uuid.UUID, doc: ParsedDocument, chunkset: ChunkSet, fence: Fence | None = None,
) -> dict[str, int]:
    """Persist blocks (once per version), chunks, spans in ONE transaction. Idempotent: an
    identical existing set is kept, a different partial set is replaced."""
    children = chunkset.children
    if not children:
        raise IngestError("EMPTY_EXTRACTION", "No readable text was found in this file.")
    async with conn.transaction():
        await _begin(conn, workspace_id)
        if fence:
            await fence.touch(conn, stage="EMBED", generation_id=generation_id, version_id=version_id)
        await _check_still_desired(conn, workspace_id, source_id, version_id, lock=True)

        # blocks (version-level; immutable once written)
        cur = await conn.execute(
            "select block_index, id from document_blocks where source_version_id = %s and workspace_id = %s",
            (version_id, workspace_id))
        block_ids: dict[int, uuid.UUID] = {r[0]: r[1] for r in await cur.fetchall()}
        if block_ids and len(block_ids) != len(doc.blocks):
            raise IndexerError("INVALID_DOCUMENT", "stored blocks disagree with the parse of the same bytes")
        if not block_ids:
            for b in doc.blocks:
                meta = dict(b.meta)
                if b.furniture:
                    meta["furniture"] = True
                if b.page_size:
                    meta["page_size"] = list(b.page_size)
                cur = await conn.execute(
                    "insert into document_blocks(source_version_id, workspace_id, block_index, type, text, "
                    "heading_path, page, bbox, ocr, row_number, char_start, char_end, meta) "
                    "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id",
                    (version_id, workspace_id, b.index, b.type, b.text, list(b.heading_path), b.page,
                     list(b.bbox) if b.bbox else None, b.ocr, b.row_number, b.char_start, b.char_end,
                     Jsonb(meta)))
                block_ids[b.index] = (await cur.fetchone())[0]

        cur = await conn.execute(
            "select count(*) from chunks where generation_id = %s and workspace_id = %s", (generation_id, workspace_id))
        have = (await cur.fetchone())[0]
        if have != len(chunkset.chunks):
            if have:
                await conn.execute("delete from chunks where generation_id = %s and workspace_id = %s",
                                   (generation_id, workspace_id))
            ids: dict[int, uuid.UUID] = {}
            for c in chunkset.chunks:  # parents precede their children
                parent_id = ids[c.parent_index] if c.parent_index is not None else None
                cur = await conn.execute(
                    "insert into chunks(generation_id, source_version_id, workspace_id, parent_id, chunk_index, "
                    "content, heading_text, content_hash, token_count) values (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                    "returning id",
                    (generation_id, version_id, workspace_id, parent_id, c.chunk_index, c.content,
                     c.heading_text or None, c.content_hash, c.token_count))
                ids[c.chunk_index] = (await cur.fetchone())[0]
                for s in c.spans:
                    await conn.execute(
                        "insert into chunk_source_spans(chunk_id, block_id, workspace_id, char_start, char_end, "
                        "page, bbox) values (%s,%s,%s,%s,%s,%s,%s)",
                        (ids[c.chunk_index], block_ids[s.block_index], workspace_id, s.char_start, s.char_end,
                         s.page, list(s.bbox) if s.bbox else None))
        await conn.execute(
            "update index_generations set expected_chunks = %s, updated_at = now() where id = %s and workspace_id = %s",
            (len(children), generation_id, workspace_id))
    return {"blocks": len(doc.blocks), "parents": len(chunkset.parents), "children": len(children)}


async def embed_generation(
    conn: psycopg.AsyncConnection, workspace_id: str, source_id: int, version_id: uuid.UUID,
    generation_id: uuid.UUID, profile: IndexProfile, embedder: Embedder, *, deadline: float | None = None,
    batch_size: int = BATCH_SIZE, fence: Fence | None = None, usage_user_id: str | None = None,
) -> tuple[int, int]:
    """Embed child chunks lacking an embedding, batch by batch (each batch is its own transaction,
    so a crash resumes). Marks the generation READY when embedded == expected.
    Returns (embedded, expected)."""
    if embedder.dimensions != profile.embedding_dims:
        raise IndexerError("PROFILE_MISMATCH",
                           f"embedder dims {embedder.dimensions} != profile dims {profile.embedding_dims}")
    cur = await conn.execute(
        "select c.id, c.content, c.heading_text, c.token_count from chunks c where c.generation_id = %s and c.workspace_id = %s "
        "and c.parent_id is not null and not exists (select 1 from chunk_embeddings_1536 e where e.chunk_id = c.id) "
        "order by c.chunk_index", (generation_id, workspace_id))
    todo = await cur.fetchall()
    cur = await conn.execute(
        "select count(*) from chunk_embeddings_1536 e join chunks c on c.id = e.chunk_id "
        "where c.generation_id = %s and c.workspace_id = %s", (generation_id, workspace_id))
    already = (await cur.fetchone())[0]  # batches are committed in order, so this keeps batch numbers stable on resume
    for i in range(0, len(todo), batch_size):
        if deadline is not None and time.monotonic() > deadline:
            raise IndexerError("TIME_BUDGET", "time budget exhausted; retry to resume", retryable=True)
        batch = todo[i : i + batch_size]
        vectors = await embedder.embed_documents([r[1] for r in batch], [r[2] for r in batch])
        if len(vectors) != len(batch):
            raise EmbeddingError("INVALID_REQUEST", "embedder returned the wrong number of vectors", retryable=False)
        async with conn.transaction():
            await _begin(conn, workspace_id)
            if fence:
                await fence.touch(conn, stage="EMBED")
            await _check_still_desired(conn, workspace_id, source_id, version_id)
            async with conn.cursor() as c2:
                await c2.executemany(
                    "insert into chunk_embeddings_1536(chunk_id, workspace_id, embedding_profile_id, embedding) "
                    "values (%s,%s,%s,%s::vector) on conflict (chunk_id) do nothing",
                    [(r[0], workspace_id, profile.embedding_profile_id, _vec(v)) for r, v in zip(batch, vectors)])
            if usage_user_id:  # idempotent per batch: a replayed batch cannot double-count (D18)
                await conn.execute(
                    "insert into usage_events(user_id, metric, quantity, unit, source_type, source_id, is_estimated, "
                    "idempotency_key) values (%s,'embedding_input_tokens',%s,'tokens','ingest',%s,true,%s) "
                    "on conflict (idempotency_key) do nothing",
                    (usage_user_id, sum(r[3] or 0 for r in batch), str(source_id),
                     f"gen:{generation_id}:embed:{(already + i) // batch_size}"))
            await conn.execute(
                "update index_generations g set embedded_chunks = (select count(*) from chunk_embeddings_1536 e "
                "join chunks c on c.id = e.chunk_id where c.generation_id = g.id), updated_at = now() "
                "where g.id = %s and g.workspace_id = %s", (generation_id, workspace_id))
    async with conn.transaction():
        await _begin(conn, workspace_id)
        if fence:
            await fence.touch(conn, stage="EMBED")
        cur = await conn.execute(
            "update index_generations set embedded_chunks = (select count(*) from chunk_embeddings_1536 e "
            "join chunks c on c.id = e.chunk_id where c.generation_id = %s), "
            "status = case when status = 'BUILDING' and expected_chunks > 0 and expected_chunks = "
            "(select count(*) from chunk_embeddings_1536 e join chunks c on c.id = e.chunk_id "
            "where c.generation_id = %s) then 'READY' else status end, "
            "ready_at = case when status = 'BUILDING' then now() else ready_at end, updated_at = now() "
            "where id = %s and workspace_id = %s returning embedded_chunks, expected_chunks",
            (generation_id, generation_id, generation_id, workspace_id))
        embedded, expected = await cur.fetchone()
    return embedded, expected


async def activate_generation(
    conn: psycopg.AsyncConnection, workspace_id: str, source_id: int, version_id: uuid.UUID,
    generation_id: uuid.UUID, profile: IndexProfile, *, text_bytes: int | None = None,
    extracted_text: str | None = None, fence: Fence | None = None,
) -> uuid.UUID | None:
    """Design B3 ACTIVATE: one transaction. Returns the superseded generation id (if any)."""
    async with conn.transaction():
        await _begin(conn, workspace_id)
        if fence:  # job -> completed commits (or rolls back) together with the activation
            await fence.touch(conn, stage="DONE", complete=True)
        await _check_still_desired(conn, workspace_id, source_id, version_id, lock=True)
        cur = await conn.execute(
            "select status, expected_chunks, embedded_chunks, source_version_id from index_generations "
            "where id = %s and workspace_id = %s for update", (generation_id, workspace_id))
        row = await cur.fetchone()
        if row is None or row[3] != version_id:
            raise ActivationRefused("GENERATION_NOT_FOUND", "generation does not belong to this version")
        status, expected, embedded, _ = row
        if status == "ACTIVE":
            return None
        if status != "READY" or expected <= 0 or embedded != expected:
            raise ActivationRefused("NOT_READY", f"generation is {status} with {embedded}/{expected} embedded")
        cur = await conn.execute(
            "select id from index_generations where source_id = %s and index_profile_id = %s and workspace_id = %s "
            "and status = 'ACTIVE' and id <> %s for update", (source_id, profile.id, workspace_id, generation_id))
        old = [r[0] for r in await cur.fetchall()]
        # old ACTIVE first: the partial unique index allows only one ACTIVE per (source, profile).
        if old:
            await conn.execute(
                "update index_generations set status='SUPERSEDED', superseded_at=now(), updated_at=now() "
                "where id = any(%s) and workspace_id = %s", (old, workspace_id))
        await conn.execute(
            "update index_generations set status='ACTIVE', activated_at=now(), updated_at=now() "
            "where id = %s and workspace_id = %s", (generation_id, workspace_id))
        await conn.execute(
            "insert into active_indexes(source_id, index_profile_id, generation_id, workspace_id) "
            "values (%s,%s,%s,%s) on conflict (source_id, index_profile_id) do update set "
            "generation_id = excluded.generation_id, workspace_id = excluded.workspace_id, activated_at = now()",
            (source_id, profile.id, generation_id, workspace_id))
        await conn.execute(
            "update files set active_version_id = %s, status = 'ready', processing_error = NULL, "
            "text_bytes = coalesce(%s, text_bytes), extracted_text = coalesce(%s, extracted_text), "
            "size = coalesce((select v.size_bytes from source_versions v where v.id = %s), size), "
            "updated_at = now() where id = %s and workspace_id = %s",
            (version_id, text_bytes, extracted_text, version_id, source_id, workspace_id))
        if old:  # D20: superseded generations keep chunks/spans but lose embeddings
            await conn.execute(
                "delete from chunk_embeddings_1536 e using chunks c where c.id = e.chunk_id "
                "and c.generation_id = any(%s) and e.workspace_id = %s", (old, workspace_id))
        return old[0] if old else None


async def fail_generation(conn: psycopg.AsyncConnection, workspace_id: str, source_id: int,
                          generation_id: uuid.UUID, code: str, message: str, *, cancelled: bool = False) -> None:
    """Mark the generation FAILED/CANCELLED. The previous ACTIVE generation is not touched; the
    file only turns 'failed' when it has no active version at all."""
    async with conn.transaction():
        await _begin(conn, workspace_id)
        cur = await conn.execute(
            "update index_generations set status = %s, error_code = %s, updated_at = now() "
            "where id = %s and workspace_id = %s and status in ('BUILDING','READY')",
            ("CANCELLED" if cancelled else "FAILED", code, generation_id, workspace_id))
        if cancelled:
            if cur.rowcount:  # a cancelled build was never searchable: drop its partial embeddings (D20 keeps chunks/spans)
                await conn.execute(
                    "delete from chunk_embeddings_1536 e using chunks c where c.id = e.chunk_id "
                    "and c.generation_id = %s and e.workspace_id = %s", (generation_id, workspace_id))
        else:
            await conn.execute(
                "update files set status = 'failed', processing_error = %s, updated_at = now() "
                "where id = %s and workspace_id = %s and active_version_id is null and deleted_at is null",
                (message[:500], source_id, workspace_id))
        # only the job that built THIS generation (a Source has one job per version)
        await conn.execute(
            "update ingestion_jobs set generation_id = %s, error_code = %s where file_id = %s and "
            "source_version_id = (select source_version_id from index_generations where id = %s)",
            (generation_id, code, source_id, generation_id))


# ------------------------------------------------------------------ driver

async def build_generation(
    conn: psycopg.AsyncConnection, workspace_id: str, source_id: int, version: VersionRef,
    data: bytes, mime: str, embedder: Embedder, *, name: str | None = None,
    profile: IndexProfile | None = None, deadline: float | None = None, ocr: OcrClient | None = None,
    usage_user_id: str | None = None,
) -> IngestResult:
    """PARSE -> CHUNK -> EMBED -> ACTIVATE for one registered version."""
    _require_autocommit(conn)
    res = IngestResult(status="FAILED", source_id=source_id, version_id=version.id)
    profile = profile or await load_index_profile(conn)
    gen_id, prior = await ensure_generation(conn, workspace_id, source_id, version.id, profile)
    res.generation_id = gen_id
    try:
        if prior == "ACTIVE":
            cur = await conn.execute(
                "select expected_chunks, embedded_chunks from index_generations where id=%s and workspace_id=%s",
                (gen_id, workspace_id))
            res.expected, res.embedded = await cur.fetchone()
            cur = await conn.execute(
                "select count(*) filter (where parent_id is null), count(*) filter (where parent_id is not null) "
                "from chunks where generation_id=%s and workspace_id=%s", (gen_id, workspace_id))
            res.parents, res.children = await cur.fetchone()
            res.status = "ACTIVE"
            return res
        t = time.monotonic()
        doc = parse_for_index(data, mime, name)
        res.timings_ms["parse"] = _ms(t)
        if doc.needs_ocr_pages:  # one invocation: all flagged pages (<= 50), bounded only by the deadline
            t = time.monotonic()
            doc = await resolve_ocr(conn, workspace_id, source_id, version.id, gen_id, data, doc, ocr,
                                    deadline=deadline, max_pages=None, usage_user_id=usage_user_id)
            res.timings_ms["ocr"] = _ms(t)
        res.warnings = list(doc.warnings)
        t = time.monotonic()
        chunkset = chunk_document(doc, STRUCTURAL_V1)
        res.timings_ms["chunk"] = _ms(t)
        t = time.monotonic()
        counts = await persist_chunks(conn, workspace_id, source_id, version.id, gen_id, doc, chunkset)
        res.timings_ms["persist"] = _ms(t)
        res.blocks, res.parents, res.children = counts["blocks"], counts["parents"], counts["children"]
        t = time.monotonic()
        res.embedded, res.expected = await embed_generation(
            conn, workspace_id, source_id, version.id, gen_id, profile, embedder, deadline=deadline)
        res.timings_ms["embed"] = _ms(t)
        t = time.monotonic()
        await activate_generation(conn, workspace_id, source_id, version.id, gen_id, profile,
                                  text_bytes=len(doc.text), extracted_text=doc.text)
        res.timings_ms["activate"] = _ms(t)
        res.status = "ACTIVE"
        return res
    except IndexerError as exc:
        res.error_code, res.message, res.retryable = exc.code, exc.message, exc.retryable
        if exc.code in ("TIME_BUDGET", "OCR_PENDING"):
            res.status = "BUILDING"  # resumable; generation stays BUILDING
            return res
        cancelled = isinstance(exc, ActivationRefused)
        res.status = "CANCELLED" if cancelled else "FAILED"
        await _safe_fail(conn, workspace_id, source_id, gen_id, exc.code, exc.message, cancelled)
        return res
    except IngestError as exc:
        res.error_code, res.message, res.retryable = exc.code, exc.message, False
    except (EmbeddingError, OcrError) as exc:
        res.error_code, res.message, res.retryable = exc.code, exc.message, exc.retryable
    except psycopg.OperationalError as exc:
        res.error_code, res.message, res.retryable = "DATABASE_UNAVAILABLE", type(exc).__name__, True
    except Exception as exc:  # noqa: BLE001 - unknown failures are surfaced, never swallowed silently
        res.error_code, res.message, res.retryable = "INTERNAL", f"{type(exc).__name__}: {exc}"[:300], True
    res.status = "FAILED"
    await _safe_fail(conn, workspace_id, source_id, gen_id, res.error_code or "INTERNAL", res.message or "", False)
    return res


async def _safe_fail(conn, workspace_id, source_id, gen_id, code, message, cancelled) -> None:
    try:
        await fail_generation(conn, workspace_id, source_id, gen_id, code, message, cancelled=cancelled)
    except psycopg.Error:
        pass  # database is down: the generation stays BUILDING and is retried/reaped later
