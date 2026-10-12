"""P0-3: the legacy-engine delete (lib/ingestion/source-delete.ts) hard-deletes a Source and everything under it in ONE
statement. The SQL is read out of the TypeScript source and run here against the real schema, so the cascade
(documents, versions, generations, chunks, embeddings, spans, blocks, jobs) and the scrubbing are proven on Postgres,
not only on mocks. Shared test DB, fake embeddings."""
from __future__ import annotations

import re

import pytest
from _db import REPO, aconnect, doc, new_file, new_workspace, skip_or_fail_if_foreign_jobs
from conftest import DATABASE_URL_TEST, needs_db
from scribe_ingest.embedding import FakeEmbedder
from scribe_intel.ingest_steps import run_step

pytestmark = needs_db
TS = (REPO / "lib" / "ingestion" / "source-delete.ts").read_text()
PARAMS = {"${sourceId}": "%(s)s", "${workspaceId}": "%(w)s", "${userId}": "%(u)s", "${onlySoftDeleted}": "%(o)s",
          "${batch}": "%(l)s"}


def ts_sql(marker: str) -> str:
    """The tagged-template SQL that contains `marker`, with ${params} turned into psycopg named parameters."""
    for body in re.findall(r"sql`(.*?)`", TS, re.S):
        if marker in body:
            for k, v in PARAMS.items():
                body = body.replace(k, v)
            assert "${" not in body, body
            return body
    raise AssertionError(marker)


REFS_SQL = ts_sql("version_keys")
DELETE_SQL = ts_sql("WITH src AS")


@pytest.fixture()
async def env():
    conn = await aconnect(DATABASE_URL_TEST)
    await skip_or_fail_if_foreign_jobs(conn)
    wid = await new_workspace(conn, kind="personal")
    yield conn, wid
    await conn.execute("delete from usage_events where source_id in (select id::text from files where workspace_id=%s)", (wid,))
    await conn.execute("delete from ingestion_jobs where file_id in (select id from files where workspace_id=%s)", (wid,))
    await conn.close()


async def build(conn, wid) -> int:
    fid = await new_file(conn, wid, storage_url="https://s.private.blob.vercel-storage.com/uploads/u/1/note.md")
    await conn.execute("update files set storage_key=%s, user_id=%s where id=%s", ("uploads/u/1/note.md", "owner-1", fid))
    await conn.execute("insert into ingestion_jobs(file_id, status) values (%s,'queued')", (fid,))
    r = await run_step(conn, FakeEmbedder(), download=lambda *a, **k: doc(1))
    assert r["outcome"] == "completed", r
    return fid


async def n(conn, q, *a) -> int:
    return (await (await conn.execute(q, a)).fetchone())[0]


CHILD_COUNTS = {
    "versions": "select count(*) from source_versions where source_id=%s",
    "generations": "select count(*) from index_generations where source_id=%s",
    "active": "select count(*) from active_indexes where source_id=%s",
    "jobs": "select count(*) from ingestion_jobs where file_id=%s",
    "blocks": "select count(*) from document_blocks b join source_versions v on v.id=b.source_version_id where v.source_id=%s",
    "chunks": "select count(*) from chunks c join source_versions v on v.id=c.source_version_id where v.source_id=%s",
    "embeddings": "select count(*) from chunk_embeddings_1536 e join chunks c on c.id=e.chunk_id join source_versions v on v.id=c.source_version_id where v.source_id=%s",
}


async def test_the_delete_statement_removes_the_source_its_cascade_and_scrubs_copies_of_the_text(env):
    conn, wid = env
    fid = await build(conn, wid)
    other = await new_workspace(conn, kind="personal")
    other_fid = await new_file(conn, other)
    await conn.execute("insert into chats(id, title, user_id, workspace_id) values ('c-legacy', 't', 'owner-1', %s)", (wid,))
    await conn.execute("insert into rag_traces(id, chat_id, user_id, workspace_id, query, top_k, threshold) "
                       "values ('t-legacy', 'c-legacy', 'owner-1', %s, 'q', 5, 0.3)", (wid,))
    await conn.execute("insert into rag_trace_chunks(trace_id, document_id, file_id, file_name, chunk_index, similarity, "
                       "rank, content_preview) values ('t-legacy', 1, %s, 'note.md', 0, 0.9, 1, 'secret document text')",
                       (fid,))
    before = {k: await n(conn, q, fid) for k, q in CHILD_COUNTS.items()}
    assert all(v > 0 for k, v in before.items() if k != "jobs"), before

    refs = await (await conn.execute(REFS_SQL, {"s": fid, "w": wid, "u": "owner-1", "o": False})).fetchall()
    assert len(refs) == 1 and refs[0][0].endswith("note.md") and refs[0][2], refs  # url, key, version keys

    # wrong owner / wrong workspace / not soft-deleted (cleanup mode): nothing happens
    for params in ({"u": "someone-else", "w": wid, "o": False}, {"u": "owner-1", "w": other, "o": False},
                   {"u": None, "w": wid, "o": True}):
        got = await (await conn.execute(DELETE_SQL, {"s": fid, **params})).fetchall()
        assert got == [], params
    assert await n(conn, "select count(*) from files where id=%s", fid) == 1

    got = await (await conn.execute(DELETE_SQL, {"s": fid, "w": wid, "u": "owner-1", "o": False})).fetchall()
    assert got == [(fid,)]
    assert await n(conn, "select count(*) from files where id=%s", fid) == 0
    assert {k: await n(conn, q, fid) for k, q in CHILD_COUNTS.items()} == dict.fromkeys(CHILD_COUNTS, 0)
    assert await n(conn, "select count(*) from documents where file_id=%s", fid) == 0
    cur = await conn.execute("select content_preview from rag_trace_chunks where trace_id='t-legacy'")
    assert [r[0] for r in await cur.fetchall()] == [""]  # the trace row stays as history, the text is gone
    assert await n(conn, "select count(*) from files where id=%s", other_fid) == 1  # nobody else was touched
    # idempotent
    assert await (await conn.execute(DELETE_SQL, {"s": fid, "w": wid, "u": "owner-1", "o": False})).fetchall() == []


async def test_cleanup_mode_only_removes_rows_that_are_already_soft_deleted(env):
    conn, wid = env
    fid = await build(conn, wid)
    params = {"s": fid, "w": wid, "u": None, "o": True}
    assert await (await conn.execute(DELETE_SQL, params)).fetchall() == []
    await conn.execute("select scribe_soft_delete_source(%s, 'owner-1', %s)", (wid, fid))
    assert await (await conn.execute(DELETE_SQL, params)).fetchall() == [(fid,)]
    assert await n(conn, "select count(*) from files where id=%s", fid) == 0
    assert {k: await n(conn, q, fid) for k, q in CHILD_COUNTS.items()} == dict.fromkeys(CHILD_COUNTS, 0)


async def test_cleanup_listing_skips_rows_in_purge_backoff(env):
    conn, wid = env
    a = await new_file(conn, wid)
    b = await new_file(conn, wid)
    await conn.execute("update files set deleted_at=now() where id in (%s,%s)", (a, b))
    await conn.execute("update files set purge_next_retry_at=now()+interval '1 hour' where id=%s", (b,))
    sql = ts_sql('ORDER BY "deleted_at" ASC').replace("%(l)s", "100")
    ids = {r[0] for r in await (await conn.execute(sql)).fetchall()}
    assert a in ids and b not in ids
