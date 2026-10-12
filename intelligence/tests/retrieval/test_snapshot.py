"""T13: snapshot pinning (D12), concurrency of embed/lexical, batched hydration."""
from __future__ import annotations

import asyncio
import time

import psycopg
import pytest
from conftest import DATABASE_URL_TEST, needs_db
from _t13 import MD, conn_for, corpus_workspace, ingest, reingest, structural_profile_id  # noqa: F401  (sets sys.path)
from _db import new_workspace
from scribe_ingest.embedding import EmbeddingError, FakeEmbedder
from scribe_intel.retrieval import search
from scribe_intel.retrieval.search import (hydrate_spans, resolve_index_profile, run_retrievers, snapshot_on,
                                           snapshot_tx)

pytestmark = needs_db
EMB = FakeEmbedder()
V1 = b"# Alpha\n\nThe OLDTOKEN belongs to the first version of this note.\n"
V2 = b"# Beta\n\nThe NEWTOKEN belongs to the second version of this note.\n"


@pytest.fixture(scope="module")
def corpus():
    return corpus_workspace(DATABASE_URL_TEST)


@pytest.fixture()
async def conn():
    c = await conn_for(DATABASE_URL_TEST)
    yield c
    await c.close()


@pytest.fixture()
async def ws(conn):
    wid = await new_workspace(conn)
    f = await ingest(conn, wid, "note.md", V1, EMB)
    return wid, f, await structural_profile_id(conn)


async def vec(text):
    return (await EMB.embed_queries([text]))[0]


async def test_snapshot_is_read_only_scoped_and_released(conn, ws):
    wid, _, _ = ws
    await snapshot_on(conn, wid)
    try:
        cur = await conn.execute("select current_setting('app.workspace_id'), current_setting('transaction_isolation'), "
                                 "current_setting('transaction_read_only')")
        assert await cur.fetchone() == (wid, "repeatable read", "on")
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            await conn.execute("insert into workspaces(kind, owner_user_id) values ('eval','x')")
    finally:
        await conn.execute("rollback")
    assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE


async def test_snapshot_tx_with_pool_commits_nothing_and_returns_connection():
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(DATABASE_URL_TEST, min_size=0, max_size=2, open=False,
                               kwargs={"autocommit": True, "prepare_threshold": None})
    await pool.open()
    try:
        wid = "00000000-0000-4000-8000-0000000000bb"
        async with snapshot_tx(pool, wid) as c:
            cur = await c.execute("select current_setting('app.workspace_id')")
            assert (await cur.fetchone())[0] == wid
        async with snapshot_tx(pool, wid) as c:  # connection is reusable afterwards
            assert c.info.transaction_status == psycopg.pq.TransactionStatus.INTRANS
        with pytest.raises(ValueError):
            async with snapshot_tx(pool, "not-a-uuid"):
                pass
    finally:
        await pool.close()


async def test_snapshot_sees_old_generation_after_concurrent_activation(conn, ws):
    wid, f, prof = ws
    other = await conn_for(DATABASE_URL_TEST)
    snap_conn = await conn_for(DATABASE_URL_TEST)
    try:
        await snapshot_on(snap_conn, wid)  # snapshot fixed here
        # another connection activates version 2 mid-snapshot (old generation -> SUPERSEDED, embeddings deleted)
        res = await reingest(other, wid, f["id"], "note.md", V2, EMB)
        new_gen = res.generation_id
        assert new_gen != f["gen"]
        out = await run_retrievers(snap_conn, wid, "OLDTOKEN or NEWTOKEN", query_vec=await vec("OLDTOKEN"),
                                   index_profile_id=prof, dense_mode="exact")
        assert {c.generation_id for c in out.lexical} == {f["gen"]} and out.lexical
        assert {c.generation_id for c in out.dense} == {f["gen"]} and out.dense  # old embeddings still visible
        assert all("OLDTOKEN" in c.content for c in out.lexical)
        await snap_conn.execute("rollback")
        await snapshot_on(snap_conn, wid)  # a fresh snapshot sees the new generation, only
        out = await run_retrievers(snap_conn, wid, "OLDTOKEN or NEWTOKEN", query_vec=await vec("OLDTOKEN"),
                                   index_profile_id=prof, dense_mode="exact")
        assert {c.generation_id for c in out.lexical} == {new_gen} and "NEWTOKEN" in out.lexical[0].content
        assert {c.generation_id for c in out.dense} == {new_gen}
    finally:
        await snap_conn.execute("rollback")
        await snap_conn.close()
        await other.close()


async def test_soft_delete_mid_snapshot_is_not_visible_until_next_snapshot(conn, ws):
    wid, f, prof = ws
    other = await conn_for(DATABASE_URL_TEST)
    try:
        await snapshot_on(conn, wid)
        await other.execute("update files set deleted_at = now() where id=%s", (f["id"],))
        out = await run_retrievers(conn, wid, "OLDTOKEN", query_vec=await vec("q"), index_profile_id=prof,
                                   dense_mode="exact")
        assert out.lexical and out.dense  # one consistent view
        await conn.execute("rollback")
        await snapshot_on(conn, wid)
        out = await run_retrievers(conn, wid, "OLDTOKEN", query_vec=await vec("q"), index_profile_id=prof,
                                   dense_mode="exact")
        assert out.lexical == [] and out.dense == []
    finally:
        await conn.execute("rollback")
        await other.close()


class SlowEmbedder:
    def __init__(self, delay=0.4, fail=False):
        self.delay, self.fail, self.t_start, self.t_end = delay, fail, None, None

    async def embed(self, text):
        self.t_start = time.monotonic()
        await asyncio.sleep(self.delay)
        self.t_end = time.monotonic()
        if self.fail:
            raise EmbeddingError("RATE_LIMITED", "boom", retryable=True)
        return await vec(text)


async def test_lexical_runs_while_embedding_is_in_flight(conn, ws, monkeypatch):
    wid, _, prof = ws
    emb = SlowEmbedder()
    marks = {}
    real = search.lexical_search

    async def spy(*a, **k):
        marks["lex_start"] = time.monotonic()
        out = await real(*a, **k)
        marks["lex_end"] = time.monotonic()
        return out

    monkeypatch.setattr(search, "lexical_search", spy)
    task = asyncio.ensure_future(emb.embed("OLDTOKEN"))  # started BEFORE the snapshot (B4 step 3)
    async with search_snapshot(wid) as c:
        out = await run_retrievers(c, wid, "OLDTOKEN", query_vec=task, index_profile_id=prof, dense_mode="exact")
    assert marks["lex_start"] < emb.t_end and marks["lex_end"] < emb.t_end  # overlapped, not serial
    assert out.lexical and out.dense
    assert out.stage_ms["embed"] >= 300
    assert {"lexical_plus_embed_wait", "dense", "hydrate", "total"} <= set(out.stage_ms)
    assert out.counts == {"dense": len(out.dense), "lexical": len(out.lexical)}


class search_snapshot:
    def __init__(self, wid):
        self.wid = wid

    async def __aenter__(self):
        self.c = await conn_for(DATABASE_URL_TEST)
        await snapshot_on(self.c, self.wid)
        return self.c

    async def __aexit__(self, *exc):
        await self.c.execute("rollback")
        await self.c.close()


async def test_embedding_failure_propagates_never_silent(ws):
    wid, _, prof = ws
    emb = SlowEmbedder(delay=0.01, fail=True)
    async with search_snapshot(wid) as c:
        with pytest.raises(EmbeddingError):
            await run_retrievers(c, wid, "OLDTOKEN", query_vec=asyncio.ensure_future(emb.embed("x")),
                                 index_profile_id=prof)
        cur = await c.execute("select 1")  # connection is still usable: nothing was left running on it
        assert await cur.fetchone() == (1,)


async def test_lexical_only_and_dense_only_modes(conn, ws):
    wid, _, prof = ws
    async with search_snapshot(wid) as c:
        lo = await run_retrievers(c, wid, "OLDTOKEN", query_vec=None, index_profile_id=prof, dense_k=0)
        assert lo.lexical and lo.dense == [] and lo.dense_mode is None
        do = await run_retrievers(c, wid, "OLDTOKEN", query_vec=await vec("x"), index_profile_id=prof, lexical_k=0,
                                  dense_mode="exact")
        assert do.dense and do.lexical == [] and do.dense_mode == "exact"


class CountingConn:
    def __init__(self, c):
        self.c, self.n = c, 0

    async def execute(self, *a, **k):
        self.n += 1
        return await self.c.execute(*a, **k)


async def test_hydration_is_one_batched_query_and_spans_have_provenance(corpus):
    wid, prof = corpus["wid"], corpus["profile_id"]
    async with search_snapshot(wid) as c:
        out = await run_retrievers(c, wid, "report to reception badge", query_vec=await vec("badge"),
                                   index_profile_id=prof, dense_mode="exact")
        cc = CountingConn(c)
        for cand in out.dense + out.lexical:
            cand.spans = []
        await hydrate_spans(cc, wid, out.dense, out.lexical)
        assert cc.n == 1 and len(out.dense) + len(out.lexical) > 10
        assert all(x.spans for x in out.dense + out.lexical)
        pdf = [x for x in out.lexical if x.doc_name == "onboarding-handbook.pdf"]
        assert pdf and any(s.page is not None and s.bbox is not None and len(s.bbox) == 4 for x in pdf for s in x.spans)
        assert all(s.char_end >= s.char_start for x in out.lexical for s in x.spans)
        # same chunk in both lists gets the same spans
        d = {x.chunk_id: x.spans for x in out.dense}
        assert all(d[x.chunk_id] == x.spans for x in out.lexical if x.chunk_id in d)
        assert await resolve_index_profile(c, "structural-v1") == prof
        with pytest.raises(LookupError):
            await resolve_index_profile(c, "nope")
