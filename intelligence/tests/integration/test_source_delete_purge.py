"""T20: soft delete leaves search immediately, ingestion refuses to activate a deleted Source, and the purge removes
every row and every blob (including T19 version blobs). Shared test DB, fake embeddings, fake blob store."""
from __future__ import annotations

import hashlib
import uuid

import httpx
import psycopg
import pytest
from _db import aconnect, doc, new_file, new_workspace
from conftest import DATABASE_URL_TEST, needs_db
from scribe_ingest import purge as purge_mod
from scribe_ingest.embedding import FakeEmbedder
from scribe_ingest.indexer import INDEX_PROFILE_NAME
from scribe_ingest.purge import (BlobDeleteError, FakeBlobStore, VercelBlobStore, dedupe_blob_refs,
                                 purge_backoff_minutes, purge_pending, purge_source)
from scribe_intel import ingest_steps
from scribe_intel.evidence import SOURCE_DELETED_LABEL, read_evidence_bundle, read_evidence_items
from scribe_intel.ingest_steps import run_step
from scribe_intel.retrieval.dense import dense_search
from scribe_intel.retrieval.lexical import lexical_search
from scribe_intel.retrieval.parents import load_parents

pytestmark = needs_db
URL = "https://store.private.blob.vercel-storage.com/uploads/u/1/note.md"
USER = "t12"


class Killed(BaseException):
    pass


def dl(data: bytes):
    return lambda url, tok, **k: data


def soft_delete_sync(wid: str, fid: int) -> bool:
    with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
        return c.execute("select scribe_soft_delete_source(%s, %s, %s)", (wid, USER, fid)).fetchone()[0]


async def soft_delete(conn, wid, fid) -> bool:
    return (await (await conn.execute("select scribe_soft_delete_source(%s, %s, %s)", (wid, USER, fid))).fetchone())[0]


async def make_job(conn, wid):
    fid = await new_file(conn, wid, storage_url=URL)
    await conn.execute("update files set storage_key=%s where id=%s", ("uploads/u/1/note.md", fid))
    cur = await conn.execute("insert into ingestion_jobs(file_id, status) values (%s,'queued') returning id", (fid,))
    return fid, (await cur.fetchone())[0]


async def job(conn, jid):
    cur = await conn.execute("select status, stage, error_code, lease_token from ingestion_jobs where id=%s", (jid,))
    return await cur.fetchone()


async def count(conn, table, fid, wid=None) -> int:
    """Rows of one Source (via source_id / version join), regardless of status."""
    q = {
        "files": "select count(*) from files where id=%s",
        "source_versions": "select count(*) from source_versions where source_id=%s",
        "index_generations": "select count(*) from index_generations where source_id=%s",
        "active_indexes": "select count(*) from active_indexes where source_id=%s",
        "ingestion_jobs": "select count(*) from ingestion_jobs where file_id=%s",
        "document_blocks": "select count(*) from document_blocks b join source_versions v on v.id=b.source_version_id where v.source_id=%s",
        "chunks": "select count(*) from chunks c join source_versions v on v.id=c.source_version_id where v.source_id=%s",
        "chunk_source_spans": "select count(*) from chunk_source_spans s join chunks c on c.id=s.chunk_id join source_versions v on v.id=c.source_version_id where v.source_id=%s",
        "chunk_embeddings_1536": "select count(*) from chunk_embeddings_1536 e join chunks c on c.id=e.chunk_id join source_versions v on v.id=c.source_version_id where v.source_id=%s",
    }[table]
    return (await (await conn.execute(q, (fid,))).fetchone())[0]


TABLES = ("source_versions", "index_generations", "active_indexes", "document_blocks", "chunks",
          "chunk_source_spans", "chunk_embeddings_1536")


async def searchable(conn, wid, fid) -> tuple[int, int, int]:
    """(dense hits, lexical hits, parents) this Source can still produce for its workspace."""
    cur = await conn.execute("select id from index_profiles where name=%s", (INDEX_PROFILE_NAME,))
    prof = (await cur.fetchone())[0]
    cur = await conn.execute(
        "select e.embedding::text from chunk_embeddings_1536 e join chunks c on c.id=e.chunk_id "
        "join source_versions v on v.id=c.source_version_id where v.source_id=%s limit 1", (fid,))
    row = await cur.fetchone()
    vec = [float(x) for x in row[0].strip("[]").split(",")] if row else await FakeEmbedder().embed_queries(["fox"])
    vec = vec if isinstance(vec[0], float) else vec[0]
    dense = await dense_search(conn, wid, vec, index_profile_id=prof, mode="exact", k=20)
    lex = await lexical_search(conn, wid, "quick brown fox", index_profile_id=prof, k=20)
    cur = await conn.execute(
        "select c.id from chunks c join source_versions v on v.id=c.source_version_id "
        "where v.source_id=%s and c.parent_id is null", (fid,))
    parent_ids = [r[0] for r in await cur.fetchall()]
    parents = await load_parents(conn, wid, prof, parent_ids)
    return len([c for c in dense.candidates if c.source_id == fid]), len([c for c in lex if c.source_id == fid]), len(parents)


@pytest.fixture()
async def env(monkeypatch):
    conn = await aconnect(DATABASE_URL_TEST)
    cur = await conn.execute("select count(*) from ingestion_jobs where status in ('queued','retrying','processing')")
    if (await cur.fetchone())[0]:
        await conn.close()
        pytest.skip("foreign claimable jobs present in the shared DB")
    wid = await new_workspace(conn)
    real = ingest_steps.embed_generation
    monkeypatch.setattr(ingest_steps, "embed_generation", lambda *a, **k: real(*a, **{**k, "batch_size": 2}))
    yield conn, wid
    await conn.execute("delete from usage_events where source_id in (select id::text from files where workspace_id=%s)", (wid,))
    await conn.execute("delete from ingestion_jobs where file_id in (select id from files where workspace_id=%s)", (wid,))
    await conn.close()


async def build_live_source(conn, wid):
    fid, jid = await make_job(conn, wid)
    r = await run_step(conn, FakeEmbedder(), download=dl(doc(1)))
    assert r["outcome"] == "completed", r
    return fid, jid


# ------------------------------------------------------------------ soft delete function

async def test_soft_delete_is_atomic_cancels_jobs_and_is_owner_scoped(env):
    conn, wid = env
    other = await new_workspace(conn)
    fid, jid = await make_job(conn, wid)
    await conn.execute("update ingestion_jobs set status='processing', lease_token=gen_random_uuid(), "
                       "lease_expires_at=now()+interval '5 minutes' where id=%s", (jid,))
    assert await soft_delete(conn, other, fid) is False  # wrong workspace
    cur = await conn.execute("select scribe_soft_delete_source(%s, 'someone-else', %s)", (wid, fid))
    assert (await cur.fetchone())[0] is False  # wrong owner
    assert (await job(conn, jid))[0] == "processing"
    assert await soft_delete(conn, wid, fid) is True
    status, stage, code, lease = await job(conn, jid)
    assert (status, stage, code, lease) == ("cancelled", "CANCELLED", "SOURCE_DELETED", None)
    assert await soft_delete(conn, wid, fid) is False  # already deleted
    cur = await conn.execute("select deleted_at is not null from files where id=%s", (fid,))
    assert (await cur.fetchone())[0] is True


# ------------------------------------------------------------------ deletion during each stage

async def test_delete_while_queued_never_runs(env):
    conn, wid = env
    fid, jid = await make_job(conn, wid)
    assert await soft_delete(conn, wid, fid)
    r = await run_step(conn, FakeEmbedder(), download=lambda *a, **k: pytest.fail("deleted source must not be downloaded"))
    assert r["claimed"] is False
    assert (await job(conn, jid))[0] == "cancelled"
    assert await count(conn, "chunks", fid) == 0 and await count(conn, "active_indexes", fid) == 0


async def test_delete_during_parsing_discards_the_step(env):
    conn, wid = env
    fid, jid = await make_job(conn, wid)

    def download_then_user_deletes(url, tok, **k):  # the user clicks delete while the bytes are being fetched
        assert soft_delete_sync(wid, fid)
        return doc(1)

    r = await run_step(conn, FakeEmbedder(), download=download_then_user_deletes)
    assert r["outcome"] == "lease_lost"  # INV-5: the cancelled job no longer matches this worker's lease
    assert await count(conn, "chunks", fid) == 0 and await count(conn, "active_indexes", fid) == 0
    cur = await conn.execute("select active_version_id is null from files where id=%s", (fid,))
    assert (await cur.fetchone())[0] is True
    assert (await job(conn, jid))[0] == "cancelled"


async def test_delete_mid_embedding_leaves_nothing_searchable(env):
    conn, wid = env
    fid, jid = await make_job(conn, wid)

    class DeletesOnSecondBatch(FakeEmbedder):
        async def _embed(self, texts):
            if self.requests_made >= 1:
                assert soft_delete_sync(wid, fid)
            return await super()._embed(texts)

    r = await run_step(conn, DeletesOnSecondBatch(), download=dl(doc(1)))
    assert r["outcome"] == "lease_lost"
    assert 0 < await count(conn, "chunk_embeddings_1536", fid) < await count(conn, "chunks", fid)  # partial, one batch
    assert await count(conn, "active_indexes", fid) == 0
    cur = await conn.execute("select count(*) from index_generations where source_id=%s and status='ACTIVE'", (fid,))
    assert (await cur.fetchone())[0] == 0
    assert (await searchable(conn, wid, fid))[:2] == (0, 0)


async def test_delete_before_activate_refuses_activation(env, monkeypatch):
    conn, wid = env
    fid, jid = await make_job(conn, wid)
    real = ingest_steps.activate_generation

    async def delete_then_activate(*a, **k):
        assert soft_delete_sync(wid, fid)
        return await real(*a, **k)

    monkeypatch.setattr(ingest_steps, "activate_generation", delete_then_activate)
    r = await run_step(conn, FakeEmbedder(), download=dl(doc(1)))
    assert r["outcome"] == "lease_lost"
    assert await count(conn, "active_indexes", fid) == 0
    cur = await conn.execute("select status from index_generations where source_id=%s", (fid,))
    assert [x[0] for x in await cur.fetchall()] == ["READY"]  # fully built, but never ACTIVE
    cur = await conn.execute("select active_version_id is null from files where id=%s", (fid,))
    assert (await cur.fetchone())[0] is True
    assert (await searchable(conn, wid, fid))[:2] == (0, 0)


async def test_activation_is_refused_even_if_only_deleted_at_was_set(env, monkeypatch):
    """Defence in depth: a deletion that did NOT cancel the job (plain UPDATE of deleted_at, lease intact) is still
    caught by the ACTIVATE transaction's own deleted_at check (DESIGN B3 step 2)."""
    conn, wid = env
    fid, jid = await make_job(conn, wid)
    real = ingest_steps.activate_generation

    async def mark_deleted_then_activate(*a, **k):
        with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
            c.execute("update files set deleted_at=now() where id=%s", (fid,))
        return await real(*a, **k)

    monkeypatch.setattr(ingest_steps, "activate_generation", mark_deleted_then_activate)
    r = await run_step(conn, FakeEmbedder(), download=dl(doc(1)))
    assert r["outcome"] == "cancelled" and r["error_code"] == "SOURCE_DELETED"
    assert await count(conn, "active_indexes", fid) == 0
    assert (await job(conn, jid))[:3] == ("cancelled", "CANCELLED", "SOURCE_DELETED")
    assert (await searchable(conn, wid, fid))[:2] == (0, 0)


async def test_delete_mid_embedding_by_plain_update_cancels_at_next_batch(env):
    conn, wid = env
    fid, jid = await make_job(conn, wid)

    class MarksDeleted(FakeEmbedder):
        async def _embed(self, texts):
            if self.requests_made >= 1:
                with psycopg.connect(DATABASE_URL_TEST, autocommit=True) as c:
                    c.execute("update files set deleted_at=now() where id=%s", (fid,))
            return await super()._embed(texts)

    r = await run_step(conn, MarksDeleted(), download=dl(doc(1)))
    assert r["outcome"] == "cancelled" and r["error_code"] == "SOURCE_DELETED"
    assert (await searchable(conn, wid, fid))[:2] == (0, 0)
    cur = await conn.execute("select status from index_generations where source_id=%s", (fid,))
    assert [x[0] for x in await cur.fetchall()] == ["CANCELLED"]
    assert await count(conn, "chunk_embeddings_1536", fid) == 0  # a cancelled build drops its partial embeddings


# ------------------------------------------------------------------ immediate removal from retrieval

async def test_deleted_source_is_gone_from_dense_lexical_and_parent_loading_immediately(env):
    conn, wid = env
    other = await new_workspace(conn)
    fid, _ = await build_live_source(conn, wid)
    fid_other = await new_file(conn, other, storage_url=URL)  # a second live Source in ANOTHER workspace
    await conn.execute("insert into ingestion_jobs(file_id,status) values (%s,'queued')", (fid_other,))
    await run_step(conn, FakeEmbedder(), download=dl(doc(1)))
    dense, lex, parents = await searchable(conn, wid, fid)
    assert dense > 0 and lex > 0 and parents > 0  # it WAS searchable
    assert await soft_delete(conn, wid, fid)
    assert await searchable(conn, wid, fid) == (0, 0, 0)  # same request path, no purge has run yet
    assert await count(conn, "chunks", fid) > 0  # rows still exist until the purge
    other_hits = await searchable(conn, other, fid_other)
    assert other_hits[0] > 0 and other_hits[1] > 0  # the other workspace is unaffected


# ------------------------------------------------------------------ purge

async def seed_versions_and_evidence(conn, wid, fid):
    """T19-style extra versions (blob URL in storage_key, one a duplicate) plus an evidence item quoting the live chunk."""
    cur = await conn.execute("select id from source_versions where source_id=%s order by version_number", (fid,))
    v1 = (await cur.fetchone())[0]
    ids = {}
    for n, key, dup in ((2, "https://store.private.blob.vercel-storage.com/uploads/u/1/note-v2.md", None),
                        (3, "https://store.private.blob.vercel-storage.com/uploads/u/1/note-v3-dup.md", v1)):
        sha = hashlib.sha256(f"v{n}".encode()).hexdigest()
        cur = await conn.execute(
            "insert into source_versions(source_id,workspace_id,version_number,storage_key,sha256,size_bytes,mime_type,"
            "upload_state,duplicate_of) values (%s,%s,%s,%s,%s,1,'text/markdown','stored',%s) returning id",
            (fid, wid, n, key, sha, dup))
        ids[n] = (await cur.fetchone())[0]
    cur = await conn.execute("select id from chunks where source_version_id=%s and parent_id is not null limit 1", (v1,))
    chunk = (await cur.fetchone())[0]
    cur = await conn.execute("insert into retrieval_traces(workspace_id, query) values (%s,'q') returning id", (wid,))
    trace = (await cur.fetchone())[0]
    cur = await conn.execute("insert into retrieval_candidates(trace_id, workspace_id, chunk_id) values (%s,%s,%s)", (trace, wid, chunk))
    cur = await conn.execute("insert into evidence_bundles(trace_id, workspace_id, answerability) values (%s,%s,'ANSWERABLE') "
                             "returning id", (trace, wid))
    bundle = (await cur.fetchone())[0]
    cur = await conn.execute(
        "insert into evidence_items(bundle_id, workspace_id, label, chunk_id, source_version_id, quote, quote_hash, "
        "page_start, heading_path) values (%s,%s,'E1',%s,%s,'The quick brown fox jumps.','h1',3,'{Title 1}') returning id",
        (bundle, wid, chunk, v1))
    return ids, bundle, (await cur.fetchone())[0]


def all_blobs(fid):
    return {URL, "uploads/u/1/note.md",  # files.storage_url + storage_key (the SAME blob as v1's key)
            "https://store.private.blob.vercel-storage.com/uploads/u/1/note-v2.md",
            "https://store.private.blob.vercel-storage.com/uploads/u/1/note-v3-dup.md"}


async def test_purge_removes_every_row_and_every_version_blob(env):
    conn, wid = env
    other = await new_workspace(conn)
    fid, _ = await build_live_source(conn, wid)
    fid2 = await new_file(conn, other, storage_url="https://store.private.blob.vercel-storage.com/other.md")  # bystander
    await conn.execute("insert into ingestion_jobs(file_id,status) values (%s,'queued')", (fid2,))
    await run_step(conn, FakeEmbedder(), download=dl(doc(2)))
    ids, bundle, item = await seed_versions_and_evidence(conn, wid, fid)
    await conn.execute("update source_versions set storage_key='uploads/u/1/note.md' where source_id=%s and version_number=1", (fid,))
    chat_id, trace_id = f"c-{uuid.uuid4()}", f"t-{uuid.uuid4()}"
    await conn.execute("insert into chats(id,title,user_id,workspace_id) values (%s,'t','t12',%s)", (chat_id, wid))
    await conn.execute("insert into rag_traces(id,chat_id,user_id,workspace_id,query,top_k,threshold) "
                       "values (%s,%s,'t12',%s,'q',5,0.3)", (trace_id, chat_id, wid))
    await conn.execute("insert into rag_trace_chunks(trace_id,document_id,file_id,file_name,chunk_index,similarity,rank,"
                       "content_preview) values (%s,1,%s,'note.md',0,0.5,1,'SECRET TEXT')", (trace_id, fid))
    for t in TABLES:
        assert await count(conn, t, fid) > 0, t

    store = FakeBlobStore(blobs=all_blobs(fid) | {"https://store.private.blob.vercel-storage.com/other.md"})
    res = await purge_source(conn, wid, fid, store)
    assert res.status == "skipped" and store.calls == []  # not deleted => refused, nothing touched
    assert await count(conn, "chunks", fid) > 0

    assert await soft_delete(conn, wid, fid)
    res = await purge_source(conn, wid, fid, store)
    assert res.status == "purged", res
    assert store.blobs == {"https://store.private.blob.vercel-storage.com/other.md"}  # everything of fid, nothing else
    assert len(store.calls) == 1 and len(store.calls[0]) == 3  # original URL + v1 pathname are ONE blob; v2 and the duplicate row add two
    assert await count(conn, "files", fid) == 0
    for t in ("ingestion_jobs",) + TABLES:
        assert await count(conn, t, fid) == 0, t
    # evidence item survives as history but its text is gone and it points at nothing
    cur = await conn.execute("select quote, chunk_id, source_version_id from evidence_items where id=%s", (item,))
    assert await cur.fetchone() == ("", None, None)
    cur = await conn.execute("select content_preview from rag_trace_chunks where trace_id=%s", (trace_id,))
    assert (await cur.fetchone())[0] == ""
    # the bystander in the other workspace is untouched
    for t in ("chunks", "chunk_embeddings_1536", "active_indexes", "source_versions"):
        assert await count(conn, t, fid2) > 0, t
    again = await purge_source(conn, wid, fid, store)
    assert again.status == "skipped"  # idempotent


async def test_purge_failure_is_recorded_retried_with_backoff_and_visible(env):
    conn, wid = env
    fid, _ = await build_live_source(conn, wid)
    await soft_delete(conn, wid, fid)
    store = FakeBlobStore(blobs={URL}, fail_next=1)
    res = await purge_source(conn, wid, fid, store)
    assert res.status == "retry_scheduled" and res.error_code == "BLOB_DELETE_FAILED" and res.retry_in_minutes
    assert await count(conn, "chunks", fid) > 0 and await count(conn, "files", fid) == 1  # nothing deleted on failure
    cur = await conn.execute("select purge_attempts, purge_error_code, purge_last_error, purge_next_retry_at > now() "
                             "from files where id=%s", (fid,))
    attempts, code, msg, future = await cur.fetchone()
    assert (attempts, code, future) == (1, "BLOB_DELETE_FAILED", True) and "simulated" in msg

    # backoff: the sweep does not list it (grace period 0) until purge_next_retry_at has passed
    summary = await purge_pending(conn, store, older_than_seconds=0, limit=1000)
    assert fid not in [r["source_id"] for r in summary["results"]]
    await conn.execute("update files set purge_next_retry_at = now() - interval '1 minute' where id=%s", (fid,))
    store.fail_next = 1
    summary = await purge_pending(conn, store, older_than_seconds=0, limit=1000)
    mine = [r for r in summary["results"] if r["source_id"] == fid][0]
    assert mine["status"] == "retry_scheduled"
    cur = await conn.execute("select purge_attempts from files where id=%s", (fid,))
    assert (await cur.fetchone())[0] == 2  # attempts accumulate => longer backoff
    await conn.execute("update files set purge_next_retry_at = now() - interval '1 minute' where id=%s", (fid,))
    summary = await purge_pending(conn, store, older_than_seconds=0, limit=1000)
    assert [r["status"] for r in summary["results"] if r["source_id"] == fid] == ["purged"]
    assert store.blobs == set() and await count(conn, "files", fid) == 0


async def test_unexpected_store_exception_is_contained_and_recorded(env):
    conn, wid = env
    fid, _ = await build_live_source(conn, wid)
    await soft_delete(conn, wid, fid)

    class Boom:
        def delete(self, refs):
            raise RuntimeError("secret-token-123 leaked?")

    res = await purge_source(conn, wid, fid, Boom())
    assert res.status == "retry_scheduled"
    cur = await conn.execute("select purge_last_error from files where id=%s", (fid,))
    assert "secret-token" not in (await cur.fetchone())[0]  # only the exception TYPE is stored


async def test_grace_period_keeps_recent_deletions(env):
    conn, wid = env
    fid, _ = await build_live_source(conn, wid)
    await soft_delete(conn, wid, fid)
    store = FakeBlobStore(blobs={URL})
    summary = await purge_pending(conn, store, older_than_seconds=3600, limit=1000)
    assert fid not in [r["source_id"] for r in summary["results"]]
    assert await count(conn, "files", fid) == 1 and store.calls == []
    await conn.execute("update files set deleted_at = now() - interval '2 hours' where id=%s", (fid,))
    summary = await purge_pending(conn, store, older_than_seconds=3600, limit=1000)
    assert [r["status"] for r in summary["results"] if r["source_id"] == fid] == ["purged"]


async def test_purge_cannot_cross_workspaces(env):
    conn, wid = env
    other = await new_workspace(conn)
    fid, _ = await build_live_source(conn, wid)
    await soft_delete(conn, wid, fid)
    store = FakeBlobStore(blobs={URL})
    res = await purge_source(conn, other, fid, store)  # right id, wrong workspace
    assert res.status == "skipped" and store.calls == []
    assert await count(conn, "chunks", fid) > 0 and await count(conn, "files", fid) == 1


# ------------------------------------------------------------------ evidence reads (INV-8 / INV-14)

async def test_evidence_shows_source_deleted_and_hides_the_quote(env):
    conn, wid = env
    other = await new_workspace(conn)
    fid, _ = await build_live_source(conn, wid)
    _, bundle, item = await seed_versions_and_evidence(conn, wid, fid)
    [view] = await read_evidence_items(conn, wid, [item])
    assert view.readable and view.quote == "The quick brown fox jumps." and view.source_id == fid and view.page_start == 3
    assert await read_evidence_items(conn, other, [item]) == []  # INV-8: another workspace sees nothing
    assert await read_evidence_bundle(conn, other, bundle) == []

    await soft_delete(conn, wid, fid)
    [view] = await read_evidence_items(conn, wid, [item])
    assert not view.readable and view.status == "source_deleted" and view.notice == SOURCE_DELETED_LABEL
    assert view.label == "E1"
    assert (view.quote, view.quote_hash, view.page_start, view.heading_path, view.source_name, view.source_id) == \
        (None, None, None, None, None, None)
    assert [v.status for v in await read_evidence_bundle(conn, wid, bundle)] == ["source_deleted"]

    await purge_source(conn, wid, fid, FakeBlobStore())  # after the purge the link is gone: still "Source deleted"
    [view] = await read_evidence_items(conn, wid, [item])
    assert view.status == "source_deleted" and view.quote is None
    assert await read_evidence_items(conn, wid, []) == []


# ------------------------------------------------------------------ unit tests

def test_dedupe_blob_refs_prefers_urls_and_drops_duplicates():
    u = "https://s.private.blob.vercel-storage.com/a/b.pdf"
    assert dedupe_blob_refs([None, "a/b.pdf", u, u, "/a/b.pdf", "c.pdf", ""]) == [u, "c.pdf"]


def test_backoff_doubles_with_jitter_and_caps_at_one_day():
    class Rng:
        @staticmethod
        def uniform(a, b):
            return 0.0
    assert [purge_backoff_minutes(n, Rng) for n in (1, 2, 3, 4)] == [2, 4, 8, 16]
    assert purge_backoff_minutes(30, Rng) == 24 * 60
    assert 2 <= purge_backoff_minutes(1) <= 2.5


def test_vercel_blob_store_requests_and_errors():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((str(req.url), req.headers["authorization"], req.content))
        return httpx.Response(500 if len(seen) == 3 else 200)

    store = VercelBlobStore("tok", transport=httpx.MockTransport(handler), batch_size=2)
    store.delete(["a", "b", "c"])  # 2 batches, both OK
    assert [s[0] for s in seen] == ["https://blob.vercel-storage.com/delete"] * 2 and seen[0][1] == "Bearer tok"
    assert seen[0][2] == b'{"urls":["a","b"]}'
    with pytest.raises(BlobDeleteError) as e:
        store.delete(["d"])
    assert e.value.retryable and "tok" not in str(e.value)
    with pytest.raises(BlobDeleteError):
        VercelBlobStore("").delete(["x"])
    VercelBlobStore("").delete([])  # nothing to do needs no token


# ------------------------------------------------------------------ endpoint

async def test_purge_endpoint_requires_scope_and_purges(env, monkeypatch):
    import time as _t

    import jwt
    from conftest import SECRET
    from scribe_intel import db, ingest_routes
    from scribe_intel.app import create_app
    from scribe_intel.config import get_settings

    conn, wid = env
    fid, _ = await build_live_source(conn, wid)
    await soft_delete(conn, wid, fid)
    await conn.execute("update files set deleted_at = now() - interval '2 days' where id=%s", (fid,))
    store = FakeBlobStore(blobs={URL})
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL_TEST)
    get_settings.cache_clear()
    monkeypatch.setattr(purge_mod, "blob_store_from_env", lambda: store)

    def tok(scope):
        now = int(_t.time())
        return {"authorization": "Bearer " + jwt.encode(
            {"sub": "ingest-purge", "wid": "00000000-0000-0000-0000-000000000000", "scope": scope,
             "aud": "scribe-intel", "iat": now, "exp": now + 60}, SECRET, algorithm="HS256")}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        assert (await c.post("/api/py/ingest/purge")).status_code == 401
        assert (await c.post("/api/py/ingest/purge", headers=tok("ingest"))).status_code == 403
        r = await c.post("/api/py/ingest/purge", headers=tok("ingest_step"))
    await db.close_pool()
    get_settings.cache_clear()
    assert r.status_code == 200, r.text
    assert any(x["source_id"] == fid and x["status"] == "purged" for x in r.json()["results"])
    assert store.blobs == set() and await count(conn, "files", fid) == 0
