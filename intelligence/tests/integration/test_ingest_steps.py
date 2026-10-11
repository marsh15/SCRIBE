"""T18: leased resumable ingestion steps against the shared test DB (fake embeddings only)."""
from __future__ import annotations

import asyncio
import uuid

import httpx
import jwt
import pytest
from _db import aconnect, doc, new_file, new_workspace
from conftest import DATABASE_URL_TEST, SECRET, needs_db
from scribe_ingest.embedding import EmbeddingError, FakeEmbedder
from scribe_ingest.indexer import Fence, LeaseLost, embed_generation
from scribe_intel import ingest_steps
from scribe_intel.ingest_steps import claim, run_step, sweep

pytestmark = needs_db
URL = "https://blob.example/x.md"


class Killed(BaseException):
    """Simulates the process being killed: not an Exception, so run_step cannot handle it."""


def dl(data: bytes):
    return lambda url, tok, **k: data


async def make_job(conn, wid, mime="text/markdown"):
    fid = await new_file(conn, wid, mime=mime, storage_url=URL)
    cur = await conn.execute("insert into ingestion_jobs(file_id, status) values (%s,'queued') returning id", (fid,))
    return fid, (await cur.fetchone())[0]


async def job(conn, jid):
    cur = await conn.execute("select status, stage, attempts, error_code, lease_token, next_retry_at from ingestion_jobs "
                             "where id=%s", (jid,))
    return await cur.fetchone()


async def n_emb(conn, fid):
    cur = await conn.execute("select count(*) from chunk_embeddings_1536 e join chunks c on c.id=e.chunk_id "
                             "join source_versions v on v.id=c.source_version_id where v.source_id=%s", (fid,))
    return (await cur.fetchone())[0]


async def n_children(conn, fid):
    cur = await conn.execute("select count(*) from chunks c join source_versions v on v.id=c.source_version_id "
                             "where v.source_id=%s and c.parent_id is not null", (fid,))
    return (await cur.fetchone())[0]


@pytest.fixture()
async def env(monkeypatch):
    conn = await aconnect(DATABASE_URL_TEST)
    cur = await conn.execute("select count(*) from ingestion_jobs where status in ('queued','retrying','processing')")
    if (await cur.fetchone())[0]:
        await conn.close()
        pytest.skip("foreign claimable jobs present in the shared DB")
    wid = await new_workspace(conn)
    real = ingest_steps.embed_generation

    def small_batches(*a, **k):
        return real(*a, **{**k, "batch_size": 2})
    monkeypatch.setattr(ingest_steps, "embed_generation", small_batches)
    yield conn, wid
    # leave no claimable/leased job behind for the next test (workspace rows are purged at module end)
    await conn.execute("delete from usage_events where source_id in (select id::text from files where workspace_id=%s)", (wid,))
    await conn.execute("delete from ingestion_jobs where file_id in (select id from files where workspace_id=%s)", (wid,))
    await conn.close()


async def test_happy_path_completes_in_one_step(env):
    conn, wid = env
    fid, jid = await make_job(conn, wid)
    r = await run_step(conn, FakeEmbedder(), download=dl(doc(1)))
    assert r["outcome"] == "completed" and r["embedded"] == r["expected"] > 0 and r["more"] is False
    assert (await job(conn, jid))[:3] == ("completed", "DONE", 1)
    cur = await conn.execute("select status, active_version_id is not null from files where id=%s", (fid,))
    assert await cur.fetchone() == ("ready", True)
    assert await n_emb(conn, fid) == await n_children(conn, fid)


async def test_step_killed_mid_embed_resumes_without_duplicates(env):
    conn, wid = env
    fid, jid = await make_job(conn, wid)

    class DiesOnSecondBatch(FakeEmbedder):
        async def _embed(self, texts):
            if self.requests_made >= 1:
                raise Killed()
            return await super()._embed(texts)

    with pytest.raises(Killed):
        await run_step(conn, DiesOnSecondBatch(), download=dl(doc(1)))
    total = await n_children(conn, fid)
    assert total > 2 and await n_emb(conn, fid) == 2  # exactly one committed batch survived the kill
    assert (await job(conn, jid))[0] == "processing"  # lease still held by the dead worker
    await conn.execute("update ingestion_jobs set lease_expires_at = now() - interval '1 minute' where id=%s", (jid,))
    assert await sweep(conn) == [jid]
    assert (await job(conn, jid))[:2] == ("queued", "EMBED")

    emb = FakeEmbedder()
    r = await run_step(conn, emb, download=lambda *a, **k: pytest.fail("resume must not re-download"))
    assert r["outcome"] == "completed"
    assert emb.texts_embedded == total - 2  # only the missing chunks were embedded
    assert await n_emb(conn, fid) == total
    cur = await conn.execute("select count(*), count(distinct chunk_id) from chunk_embeddings_1536 e join chunks c on "
                             "c.id=e.chunk_id join source_versions v on v.id=c.source_version_id where v.source_id=%s", (fid,))
    n, d = await cur.fetchone()
    assert n == d == total
    cur = await conn.execute("select count(*), count(distinct idempotency_key) from usage_events "
                             "where idempotency_key like 'gen:%%:embed:%%' and source_id=%s", (str(fid),))
    n, d = await cur.fetchone()
    assert n == d >= 2  # one usage event per batch, none duplicated
    await conn.execute("delete from usage_events where source_id=%s", (str(fid),))


async def test_time_budget_yields_and_hands_the_attempt_back(env):
    conn, wid = env
    fid, jid = await make_job(conn, wid)
    r = await run_step(conn, FakeEmbedder(), budget_s=0, download=dl(doc(1)))
    assert r["outcome"] == "yielded" and r["more"] is True
    assert (await job(conn, jid))[:3] == ("queued", "EMBED", 0) and await n_emb(conn, fid) == 0
    r = await run_step(conn, FakeEmbedder(), download=dl(doc(1)))
    assert r["outcome"] == "completed"
    await conn.execute("delete from usage_events where source_id=%s", (str(fid),))


async def test_stale_lease_writes_zero_rows(env):
    conn, wid = env
    fid, jid = await make_job(conn, wid)
    thief = uuid.uuid4()

    class StolenWhileEmbedding(FakeEmbedder):
        async def _embed(self, texts):
            await conn.execute("update ingestion_jobs set lease_token=%s where id=%s", (thief, jid))
            return await super()._embed(texts)

    r = await run_step(conn, StolenWhileEmbedding(), download=dl(doc(1)))
    assert r["outcome"] == "lease_lost"
    assert await n_emb(conn, fid) == 0  # the fenced batch transaction rolled back
    st, stage, att, code, tok, _ = await job(conn, jid)
    assert (st, tok) == ("processing", thief) and code is None  # the loser did not touch the thief's job
    cur = await conn.execute("select status from files where id=%s", (fid,))
    assert (await cur.fetchone())[0] != "ready"


async def test_fence_touch_direct(env):
    conn, wid = env
    _, jid = await make_job(conn, wid)
    row = await claim(conn, max_live=3, lease_seconds=60, max_attempts=5)
    fence = Fence(row[0], row[3])
    await fence.touch(conn, stage="EMBED")
    await conn.execute("update ingestion_jobs set lease_token=gen_random_uuid() where id=%s", (jid,))
    with pytest.raises(LeaseLost):
        await fence.touch(conn, stage="DONE", complete=True)
    assert (await job(conn, jid))[0] == "processing"


async def test_permanent_failure_fails_fast(env):
    conn, wid = env
    fid, jid = await make_job(conn, wid, mime="image/png")
    r = await run_step(conn, FakeEmbedder(), download=dl(b"\x00\x01 not a doc"))
    assert r["outcome"] == "failed" and r["error_code"] == "UNSUPPORTED_FORMAT"
    assert (await job(conn, jid))[:4] == ("failed", "FAILED", 1, "UNSUPPORTED_FORMAT")
    cur = await conn.execute("select status from files where id=%s", (fid,))
    assert (await cur.fetchone())[0] == "failed"
    assert (await run_step(conn, FakeEmbedder(), download=dl(b"")))["claimed"] is False


async def test_transient_failure_schedules_backoff_then_exhausts(env):
    conn, wid = env
    fid, jid = await make_job(conn, wid)

    class Limited(FakeEmbedder):
        async def _embed(self, texts):
            raise EmbeddingError("RATE_LIMITED", "slow down", retryable=True)

    for attempt in range(1, 6):
        r = await run_step(conn, Limited(), download=dl(doc(1)))
        st, stage, att, code, tok, nxt = await job(conn, jid)
        assert att == attempt and code == "RATE_LIMITED" and tok is None
        if attempt < 5:
            assert r["outcome"] == "retry_scheduled" and st == "retrying" and nxt is not None
            assert 2 ** attempt <= r["retry_in_minutes"] <= min(16, 2 ** attempt) * 1.25
            assert (await run_step(conn, Limited(), download=dl(doc(1))))["claimed"] is False  # not due yet
            await conn.execute("update ingestion_jobs set next_retry_at = now() - interval '1 second' where id=%s", (jid,))
        else:
            assert r["outcome"] == "failed" and st == "failed"


async def test_unknown_error_gets_two_attempts(env):
    conn, wid = env
    fid, jid = await make_job(conn, wid)

    class Boom(FakeEmbedder):
        async def _embed(self, texts):
            raise RuntimeError("kaboom with details")

    r1 = await run_step(conn, Boom(), download=dl(doc(1)))
    assert r1["outcome"] == "retry_scheduled" and "kaboom" not in r1["message"]
    await conn.execute("update ingestion_jobs set next_retry_at = now() - interval '1 second' where id=%s", (jid,))
    r2 = await run_step(conn, Boom(), download=dl(doc(1)))
    assert r2["outcome"] == "failed" and (await job(conn, jid))[2] == 2


async def test_deleted_source_is_cancelled_and_superseded_version_stops(env):
    conn, wid = env
    fid, jid = await make_job(conn, wid)
    await conn.execute("update files set deleted_at = now() where id=%s", (fid,))
    assert (await run_step(conn, FakeEmbedder(), download=dl(doc(1))))["claimed"] is False  # claim skips deleted
    await conn.execute("update files set deleted_at = null where id=%s", (fid,))

    class SupersedeAfterFirstBatch(FakeEmbedder):
        async def _embed(self, texts):
            out = await super()._embed(texts)
            if self.requests_made == 1:
                await conn.execute("update files set desired_version_id = null where id=%s", (fid,))
            return out

    r = await run_step(conn, SupersedeAfterFirstBatch(), download=dl(doc(1)))
    assert r["outcome"] == "superseded" and r["error_code"] == "VERSION_SUPERSEDED"
    assert (await job(conn, jid))[:2] == ("superseded", "SUPERSEDED")
    await conn.execute("delete from usage_events where source_id=%s", (str(fid),))


async def test_concurrent_claims_respect_cap_and_never_double_claim(env):
    conn, wid = env
    jids = [(await make_job(conn, wid))[1] for _ in range(5)]
    conns = [await aconnect(DATABASE_URL_TEST) for _ in range(6)]
    try:
        results = await asyncio.gather(*[claim(c, max_live=3, lease_seconds=120, max_attempts=5) for c in conns])
    finally:
        for c in conns:
            await c.close()
    got = [r for r in results if not isinstance(r, str)]
    assert len(got) == 3 and len({r[0] for r in got}) == 3 and len({r[3] for r in got}) == 3
    assert sorted(r for r in results if isinstance(r, str)) == ["at_capacity"] * 3
    assert set(r[0] for r in got) <= set(jids)


async def test_http_step_endpoint_scope_and_outcome(env, monkeypatch):
    import time

    from scribe_intel import db, ingest_routes
    from scribe_intel.app import create_app
    from scribe_intel.config import get_settings

    conn, wid = env
    fid, jid = await make_job(conn, wid)
    monkeypatch.setenv("DATABASE_URL", DATABASE_URL_TEST)
    monkeypatch.setenv("SCRIBE_EMBEDDER", "fake")
    monkeypatch.setenv("SCRIBE_ALLOW_FAKE_EMBEDDINGS", "1")
    get_settings.cache_clear()
    real = ingest_steps.run_step

    async def with_dl(c, e, **k):
        return await real(c, e, **{**k, "download": dl(doc(1))})
    monkeypatch.setattr(ingest_steps, "run_step", with_dl)

    def tok(scope):
        now = int(time.time())
        return {"authorization": "Bearer " + jwt.encode(
            {"sub": "tick", "wid": str(uuid.UUID(int=0)), "scope": scope, "aud": "scribe-intel", "iat": now,
             "exp": now + 60}, SECRET, algorithm="HS256")}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://t") as c:
        assert (await c.post("/api/py/ingest/step", headers=tok("ingest"))).status_code == 403
        assert (await c.post("/api/py/ingest/step")).status_code == 401
        r = await c.post("/api/py/ingest/step", headers=tok("ingest_step"))
        assert r.status_code == 200 and r.json()["outcome"] == "completed"
        r = await c.post("/api/py/ingest/sweep", headers=tok("ingest_step"))
        assert r.status_code == 200 and r.json()["swept"] == 0
    await db.close_pool()
    get_settings.cache_clear()
    await conn.execute("delete from usage_events where source_id=%s", (str(fid),))
