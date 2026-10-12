"""T19: upload a new version of a Source without losing the searchable one (fake embeddings only).

The reserve/complete transitions are the real SQL functions from migration 0015 (the ones the TS routes call); the
build is the real leased step machine; "is it searchable" goes through the real retrieval path (run_retrievers inside a
REPEATABLE READ snapshot), not through status columns."""
from __future__ import annotations

import asyncio
import hashlib

import psycopg
import pytest
from _db import aconnect, new_file, new_workspace, skip_or_fail_if_foreign_jobs
from conftest import DATABASE_URL_TEST, needs_db
from scribe_ingest.embedding import EmbeddingError, FakeEmbedder
from scribe_intel import ingest_steps
from scribe_intel.ingest_steps import run_step, sweep
from scribe_intel.retrieval.search import _end, resolve_index_profile, run_retrievers, snapshot_on

pytestmark = needs_db
MD = "text/markdown"


class Killed(BaseException):
    """The process dies: not an Exception, so run_step cannot handle it."""


def mk(marker: str) -> bytes:
    body = "\n\n".join(f"Paragraph {i} about routine matters. " + "The quick brown fox jumps. " * 12 for i in range(14))
    return f"# Manual\n\nThe {marker} marker is stated here.\n\n{body}\n".encode()


V1, V2, V3 = mk("ALPHAV1"), mk("BRAVOV2"), mk("CHARLIEV3")


class Blobs:
    """A fake private blob store keyed by URL."""

    def __init__(self):
        self.files: dict[str, bytes] = {}

    def put(self, url: str, data: bytes) -> str:
        self.files[url] = data
        return url

    def __call__(self, url, token, **kw):
        return self.files[url]


@pytest.fixture()
async def env(monkeypatch):
    conn = await aconnect(DATABASE_URL_TEST)
    await skip_or_fail_if_foreign_jobs(conn)
    wid = await new_workspace(conn)
    real = ingest_steps.embed_generation
    monkeypatch.setattr(ingest_steps, "embed_generation", lambda *a, **k: real(*a, **{**k, "batch_size": 2}))
    yield conn, wid, Blobs()
    await conn.execute("delete from usage_events where source_id in (select id::text from files where workspace_id=%s)", (wid,))
    await conn.execute("delete from ingestion_jobs where file_id in (select id from files where workspace_id=%s)", (wid,))
    await conn.close()


# ----------------------------------------------------------------------------------------------- helpers

async def scalar(conn, sql, params=()):
    return (await (await conn.execute(sql, params)).fetchone())[0]


async def v1_active(conn, wid, blobs, data=V1):
    """Version 1 through the T18 flow (job without a version, storage_url on the Source)."""
    fid = await new_file(conn, wid, "manual.md", MD, storage_url=blobs.put("https://blob.example/v1.md", data))
    await conn.execute("insert into ingestion_jobs(file_id, status) values (%s,'queued')", (fid,))
    r = await run_step(conn, FakeEmbedder(), download=blobs)
    assert r["outcome"] == "completed", r
    return fid, await scalar(conn, "select active_version_id from files where id=%s", (fid,))


async def reserve(conn, wid, fid, *, user="t12", mime=MD, size=10):
    cur = await conn.execute("select * from scribe_reserve_source_version(%s,%s,%s,%s,%s)", (wid, fid, user, mime, size))
    return await cur.fetchone()


async def complete(conn, wid, vid, blobs, data, n=None):
    url = blobs.put(f"https://blob.example/v{n or vid}.md", data)
    cur = await conn.execute("select * from scribe_complete_source_version(%s,%s,%s,%s,%s)",
                             (wid, vid, url, hashlib.sha256(data).hexdigest(), len(data)))
    return await cur.fetchone()


async def upload_version(conn, wid, fid, blobs, data):
    vid, n = await reserve(conn, wid, fid)
    out = await complete(conn, wid, vid, blobs, data, n=f"{fid}-{n}")
    return vid, n, out


async def search(conn, wid, query):
    """(set of source_version_ids the dense retriever returned, set the lexical one returned) via the real search path."""
    pid = await resolve_index_profile(conn, "structural-v1")
    (vec,) = await FakeEmbedder().embed_queries([query])
    await snapshot_on(conn, wid)
    try:
        snap = await run_retrievers(conn, wid, query, query_vec=vec, index_profile_id=pid, dense_k=100, lexical_k=100,
                                    dense_mode="exact")
    finally:
        await _end(conn)
    return {c.source_version_id for c in snap.dense}, {c.source_version_id for c in snap.lexical}


async def gen_status(conn, vid):
    return await scalar(conn, "select status from index_generations where source_version_id=%s", (vid,))


async def n_emb(conn, vid):
    return await scalar(conn, "select count(*) from chunk_embeddings_1536 e join chunks c on c.id=e.chunk_id "
                              "where c.source_version_id=%s", (vid,))


async def job_for(conn, vid):
    cur = await conn.execute("select status, stage, error_code from ingestion_jobs where source_version_id=%s", (vid,))
    return await cur.fetchall()


class DiesOnSecondBatch(FakeEmbedder):
    async def _embed(self, texts):
        if self.requests_made >= 1:
            raise Killed()
        return await super()._embed(texts)


# ----------------------------------------------------------------------------------------------- tests

async def test_killed_mid_embed_keeps_v1_searchable_then_resume_activates_v2(env):
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    dense, lexical = await search(conn, wid, "ALPHAV1")
    assert dense == {v1} and lexical == {v1}

    v2, n, out = await upload_version(conn, wid, fid, blobs, V2)
    assert n == 2 and out[0] == "queued"
    assert await scalar(conn, "select desired_version_id from files where id=%s", (fid,)) == v2

    with pytest.raises(Killed):
        await run_step(conn, DiesOnSecondBatch(), download=blobs)
    total = await scalar(conn, "select count(*) from chunks where source_version_id=%s and parent_id is not null", (v2,))
    assert total > 2 and await n_emb(conn, v2) == 2  # v2 is half embedded...

    # ...and still invisible: only v1 is searchable by either retriever, and the Source is still active on v1
    dense, lexical = await search(conn, wid, "BRAVOV2")
    assert dense == {v1} and lexical == set()
    assert (await search(conn, wid, "ALPHAV1")) == ({v1}, {v1})
    assert await scalar(conn, "select active_version_id from files where id=%s", (fid,)) == v1
    assert await scalar(conn, "select status from files where id=%s", (fid,)) == "ready"

    await conn.execute("update ingestion_jobs set lease_expires_at = now() - interval '1 minute' where source_version_id=%s", (v2,))
    assert len(await sweep(conn)) == 1
    emb = FakeEmbedder()
    r = await run_step(conn, emb, download=lambda *a, **k: pytest.fail("resume must not re-download"))
    assert r["outcome"] == "completed" and emb.texts_embedded == total - 2

    dense, lexical = await search(conn, wid, "BRAVOV2")
    assert dense == {v2} and lexical == {v2}  # v2 only
    assert (await search(conn, wid, "ALPHAV1")) == ({v2}, set())
    assert await scalar(conn, "select active_version_id from files where id=%s", (fid,)) == v2
    assert await gen_status(conn, v1) == "SUPERSEDED" and await gen_status(conn, v2) == "ACTIVE"
    # D20: the old generation lost its embeddings but kept chunks and spans
    assert await n_emb(conn, v1) == 0
    assert await scalar(conn, "select count(*) from chunks where source_version_id=%s", (v1,)) > 0
    assert await scalar(conn, "select count(*) from chunk_source_spans s join chunks c on c.id=s.chunk_id "
                              "where c.source_version_id=%s", (v1,)) > 0
    assert await scalar(conn, "select size from files where id=%s", (fid,)) == len(V2)
    assert await scalar(conn, "select count(*) from active_indexes where source_id=%s", (fid,)) == 1


async def test_failed_new_version_leaves_the_previous_one_searchable(env):
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    v2, _, _ = await upload_version(conn, wid, fid, blobs, V2)

    class Rejecting(FakeEmbedder):
        async def _embed(self, texts):
            raise EmbeddingError("INVALID_REQUEST", "provider rejected the input", retryable=False)

    r = await run_step(conn, Rejecting(), download=blobs)
    assert r["outcome"] == "failed" and r["error_code"] == "INVALID_REQUEST"
    assert await job_for(conn, v2) == [("failed", "FAILED", "INVALID_REQUEST")]
    assert await gen_status(conn, v2) == "FAILED"
    # v1's own (completed) job and generation were not touched by v2's failure
    assert (await job_for(conn, v1))[0][0] == "completed" and await gen_status(conn, v1) == "ACTIVE"
    assert await scalar(conn, "select status from files where id=%s", (fid,)) == "ready"
    assert await scalar(conn, "select active_version_id from files where id=%s", (fid,)) == v1
    assert (await search(conn, wid, "ALPHAV1")) == ({v1}, {v1})
    assert (await search(conn, wid, "BRAVOV2")) == ({v1}, set())


async def test_same_bytes_are_marked_duplicate_and_build_nothing(env):
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    v2, n, out = await upload_version(conn, wid, fid, blobs, V1)
    assert out[0] == "duplicate" and out[3] == v1 and n == 2
    assert await scalar(conn, "select duplicate_of from source_versions where id=%s", (v2,)) == v1
    assert await scalar(conn, "select upload_state from source_versions where id=%s", (v2,)) == "stored"
    assert await job_for(conn, v2) == []
    assert await scalar(conn, "select count(*) from index_generations where source_version_id=%s", (v2,)) == 0
    assert await scalar(conn, "select desired_version_id from files where id=%s", (fid,)) == v1
    assert (await run_step(conn, FakeEmbedder(), download=blobs))["claimed"] is False
    assert (await search(conn, wid, "ALPHAV1")) == ({v1}, {v1})


async def test_reuploading_the_active_bytes_cancels_a_newer_in_flight_build(env):
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    v2, _, _ = await upload_version(conn, wid, fid, blobs, V2)
    v3, _, out = await upload_version(conn, wid, fid, blobs, V1)  # revert while v2 is queued
    assert out[0] == "duplicate" and out[3] == v1
    assert await scalar(conn, "select desired_version_id from files where id=%s", (fid,)) == v1
    r = await run_step(conn, FakeEmbedder(), download=lambda *a, **k: pytest.fail("must not download"))
    assert r["outcome"] == "superseded"
    assert (await job_for(conn, v2))[0][0] == "superseded"
    assert (await search(conn, wid, "ALPHAV1")) == ({v1}, {v1})


async def test_v3_uploaded_while_v2_builds_cancels_v2_and_v3_activates(env):
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    v2, _, _ = await upload_version(conn, wid, fid, blobs, V2)
    with pytest.raises(Killed):
        await run_step(conn, DiesOnSecondBatch(), download=blobs)
    assert await n_emb(conn, v2) == 2
    await conn.execute("update ingestion_jobs set lease_expires_at = now() - interval '1 minute' where source_version_id=%s", (v2,))
    await sweep(conn)

    v3, n3, out = await upload_version(conn, wid, fid, blobs, V3)
    assert n3 == 3 and out[0] == "queued"
    assert await scalar(conn, "select desired_version_id from files where id=%s", (fid,)) == v3

    # first claim is v2's job (older): its step boundary notices v3 is desired and stops without re-downloading
    r = await run_step(conn, FakeEmbedder(), download=lambda *a, **k: pytest.fail("cancelled build must not download"))
    assert r["outcome"] == "superseded" and r["error_code"] == "VERSION_SUPERSEDED"
    assert (await job_for(conn, v2))[0][:2] == ("superseded", "SUPERSEDED")
    assert await gen_status(conn, v2) == "CANCELLED" and await n_emb(conn, v2) == 0
    # still v1 until v3 finishes
    assert (await search(conn, wid, "ALPHAV1")) == ({v1}, {v1})

    r = await run_step(conn, FakeEmbedder(), download=blobs)
    assert r["outcome"] == "completed"
    assert await scalar(conn, "select active_version_id from files where id=%s", (fid,)) == v3
    assert (await search(conn, wid, "CHARLIEV3")) == ({v3}, {v3})
    assert (await search(conn, wid, "BRAVOV2")) == ({v3}, set())
    assert [await gen_status(conn, v) for v in (v1, v2, v3)] == ["SUPERSEDED", "CANCELLED", "ACTIVE"]


async def test_queued_v2_replaced_by_v3_never_downloads(env):
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    v2, _, _ = await upload_version(conn, wid, fid, blobs, V2)
    v3, _, _ = await upload_version(conn, wid, fid, blobs, V3)
    r = await run_step(conn, FakeEmbedder(), download=lambda *a, **k: pytest.fail("v2 was never wanted"))
    assert r["outcome"] == "superseded" and r["more"] is True
    assert await scalar(conn, "select count(*) from index_generations where source_version_id=%s", (v2,)) == 0
    assert (await run_step(conn, FakeEmbedder(), download=blobs))["outcome"] == "completed"
    assert await scalar(conn, "select active_version_id from files where id=%s", (fid,)) == v3


async def test_duplicate_callback_is_a_noop_even_when_concurrent(env):
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    vid, n = await reserve(conn, wid, fid)
    url = blobs.put("https://blob.example/dup-callback.md", V2)
    args = (wid, vid, url, hashlib.sha256(V2).hexdigest(), len(V2))
    extra = [await aconnect(DATABASE_URL_TEST) for _ in range(3)]
    try:
        async def call(c):
            return (await (await c.execute("select * from scribe_complete_source_version(%s,%s,%s,%s,%s)", args)).fetchone())[0]
        outcomes = await asyncio.gather(*[call(c) for c in extra])
    finally:
        for c in extra:
            await c.close()
    assert sorted(outcomes) == ["queued", "replayed", "replayed"]
    assert (await complete(conn, wid, vid, blobs, V2, n="again"))[0] == "replayed"  # and a late sequential one
    assert await scalar(conn, "select count(*) from source_versions where source_id=%s", (fid,)) == 2
    assert await scalar(conn, "select count(*) from ingestion_jobs where source_version_id=%s", (vid,)) == 1
    assert await scalar(conn, "select desired_version_id from files where id=%s", (fid,)) == vid


async def test_reservation_rules_and_numbering(env):
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    # concurrent reservations get distinct, consecutive numbers (UNIQUE(source_id, version_number) never trips)
    conns = [await aconnect(DATABASE_URL_TEST) for _ in range(3)]
    try:
        got = await asyncio.gather(*[reserve(c, wid, fid) for c in conns])
    finally:
        for c in conns:
            await c.close()
    assert sorted(n for _, n in got) == [2, 3, 4]

    def refused(code):
        return pytest.raises(psycopg.errors.Error, match=code)

    with refused("SOURCE_NOT_FOUND"):
        await reserve(conn, wid, fid, user="someone-else")
    with refused("TYPE_MISMATCH"):
        await reserve(conn, wid, fid, mime="application/pdf")
    other = await new_file(conn, wid, "x.md", MD)  # never indexed: no active version
    with refused("NO_ACTIVE_VERSION"):
        await reserve(conn, wid, other)
    await conn.execute("update files set deleted_at = now() where id=%s", (fid,))
    with refused("SOURCE_NOT_FOUND"):
        await reserve(conn, wid, fid)
    # a reservation that completes after the Source was deleted fails instead of queueing work
    out = await complete(conn, wid, got[0][0], blobs, V2, n="late")
    assert out[0] == "source_deleted"
    assert await scalar(conn, "select upload_state from source_versions where id=%s", (got[0][0],)) == "failed"
    assert await scalar(conn, "select count(*) from ingestion_jobs where source_version_id=%s", (got[0][0],)) == 0
    # unknown version / foreign workspace
    other_ws = await new_workspace(conn)
    assert (await complete(conn, other_ws, got[1][0], blobs, V2, n="x"))[0] == "not_found"


async def test_stored_versions_are_immutable_but_reservations_are_fillable(env):
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    vid, _ = await reserve(conn, wid, fid)
    with pytest.raises(psycopg.errors.CheckViolation):  # cannot be 'stored' without a checksum
        await conn.execute("update source_versions set upload_state='stored' where id=%s", (vid,))
    out = await complete(conn, wid, vid, blobs, V2, n="imm")
    assert out[0] == "queued"
    for col, val in (("sha256", "'zzz'"), ("storage_key", "'k'"), ("size_bytes", "5"), ("mime_type", "'x/y'"),
                     ("duplicate_of", f"'{v1}'"), ("upload_state", "'failed'"), ("version_number", "9")):
        with pytest.raises(psycopg.errors.RestrictViolation, match="immutable"):
            await conn.execute(f"update source_versions set {col} = {val} where id=%s", (vid,))
    # the same applies to the first version written by the T18 flow
    with pytest.raises(psycopg.errors.RestrictViolation):
        await conn.execute("update source_versions set sha256='zzz' where id=%s", (v1,))


def history_sql() -> str:
    """The version-history query exactly as lib/ingestion/source-versions.ts runs it (placeholders -> psycopg)."""
    import re

    from _db import REPO

    ts = (REPO / "lib" / "ingestion" / "source-versions.ts").read_text()
    body = ts[ts.index("export async function listSourceVersions"):]
    body = body[body.index("sql`") + 4:]
    body = body[:body.index("`)")]
    named = re.sub(r"\$\{(\w+)\}", r"@@\1@@", body)  # protect placeholders while escaping literal percent signs
    return re.sub(r"@@(\w+)@@", r"%(\1)s", named.replace("%", "%%"))


async def test_history_query_reports_each_versions_state(env):
    conn, wid, blobs = env
    fid, v1 = await v1_active(conn, wid, blobs)
    v2, _, _ = await upload_version(conn, wid, fid, blobs, V2)

    class Rejecting(FakeEmbedder):
        async def _embed(self, texts):
            raise EmbeddingError("INVALID_REQUEST", "provider rejected the input", retryable=False)

    await run_step(conn, Rejecting(), download=blobs)  # v2 fails, v1 stays active
    v3, _, out = await upload_version(conn, wid, fid, blobs, V1)  # same bytes as the active version
    assert out[0] == "duplicate"
    v4, _, _ = await upload_version(conn, wid, fid, blobs, V3)  # queued, not started
    sql = history_sql()
    cur = await conn.execute(sql, {"workspaceId": wid, "userId": "t12", "sourceId": fid, "fetchLimit": 51, "offset": 0})
    cols = [c.name for c in cur.description]
    rows = {r[cols.index("version_number")]: dict(zip(cols, r)) for r in await cur.fetchall()}
    assert sorted(rows) == [1, 2, 3, 4]
    assert rows[1]["generation_status"] == "ACTIVE" and rows[1]["is_active"] is True
    assert rows[2]["generation_status"] == "FAILED" and rows[2]["job_status"] == "failed" and rows[2]["error_code"] == "INVALID_REQUEST"
    assert rows[3]["duplicate_of"] == v1 and rows[3]["duplicate_of_number"] == 1 and rows[3]["job_status"] is None
    assert rows[4]["job_status"] == "queued" and rows[4]["is_desired"] is True and rows[4]["generation_status"] is None
    # a different user / workspace sees nothing
    cur = await conn.execute(sql, {"workspaceId": wid, "userId": "intruder", "sourceId": fid, "fetchLimit": 51, "offset": 0})
    assert await cur.fetchall() == []
