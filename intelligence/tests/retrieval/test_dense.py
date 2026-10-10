"""T13: dense retriever (exact / ann / auto, degraded flag, EXPLAIN per D11, legacy-dense semantics)."""
from __future__ import annotations

import re

import pytest
from conftest import DATABASE_URL_TEST, needs_db
from _t13 import (LEGACY_PROFILE, conn_for, seed_sync, seed_legacy, unit3072)
from _db import new_workspace
from scribe_ingest.embedding import FakeEmbedder
from scribe_intel.retrieval import dense
from scribe_intel.retrieval.dense import (AUTO_ANN_THRESHOLD, FLAG_ANN_UNAVAILABLE, active_chunk_count, dense_search,
                                          dense_sql, legacy_dense_search, pgvector_version)
from scribe_intel.retrieval.queries import vec_literal

pytestmark = needs_db
EMB = FakeEmbedder()


@pytest.fixture(scope="module")
def seeded():
    return seed_sync(DATABASE_URL_TEST)


@pytest.fixture()
async def conn():
    c = await conn_for(DATABASE_URL_TEST)
    yield c
    await c.close()


async def qvec(text: str) -> list[float]:
    return (await EMB.embed_queries([text]))[0]


async def test_pgvector_version_is_08_or_later(conn):
    v = await pgvector_version(conn)
    assert v is not None and v[:2] >= (0, 8)


async def test_exact_shape_scores_and_children_only(conn, seeded):
    res = await dense_search(conn, seeded["wa"], await qvec("annual leave"), index_profile_id=seeded["profile_id"],
                             mode="exact", k=40)
    assert res.mode_used == "exact" and res.degraded == []
    cands = res.candidates
    assert cands and [c.rank for c in cands] == list(range(1, len(cands) + 1))
    assert all(c.method == "dense" for c in cands)
    scores = [c.score for c in cands]
    assert scores == sorted(scores, reverse=True)
    # score = 1 - cosine distance, verified independently in SQL
    cur = await conn.execute(
        "select 1 - (embedding <=> %s::vector) from chunk_embeddings_1536 where chunk_id=%s",
        (vec_literal(await qvec("annual leave")), cands[0].chunk_id))
    assert abs((await cur.fetchone())[0] - cands[0].score) < 1e-9
    cur = await conn.execute("select count(*) from chunks where id = any(%s) and parent_id is null",
                             ([c.chunk_id for c in cands],))
    assert (await cur.fetchone())[0] == 0
    assert all(c.parent_id is not None and c.doc_name and c.content for c in cands)


async def test_k_limits_and_empty_source_ids(conn, seeded):
    v = await qvec("anything")
    res = await dense_search(conn, seeded["wa"], v, index_profile_id=seeded["profile_id"], mode="exact", k=3)
    assert len(res.candidates) == 3
    res = await dense_search(conn, seeded["wa"], v, index_profile_id=seeded["profile_id"], source_ids=[], k=3)
    assert res.candidates == []
    with pytest.raises(ValueError):
        await dense_search(conn, seeded["wa"], v[:10], index_profile_id=seeded["profile_id"])


@pytest.mark.parametrize("q", ["annual leave", "ERR_AUTH_403", "totally unrelated words", "giraffes"])
async def test_ann_matches_exact(conn, seeded, q):
    v = await qvec(q)
    kw = dict(index_profile_id=seeded["profile_id"], k=40)
    exact = await dense_search(conn, seeded["wa"], v, mode="exact", **kw)
    ann = await dense_search(conn, seeded["wa"], v, mode="ann", **kw)
    assert ann.mode_used == "ann" and ann.degraded == []
    assert [c.chunk_id for c in ann.candidates] == [c.chunk_id for c in exact.candidates]
    assert [c.score for c in ann.candidates] == pytest.approx([c.score for c in exact.candidates])


async def test_source_ids_filter(conn, seeded):
    only = [seeded["a_ids"]["hr.md"]]
    res = await dense_search(conn, seeded["wa"], await qvec("x"), index_profile_id=seeded["profile_id"],
                             source_ids=only, mode="exact")
    assert res.candidates and {c.source_id for c in res.candidates} == set(only)
    res = await dense_search(conn, seeded["wa"], await qvec("x"), index_profile_id=seeded["profile_id"],
                             source_ids=only, mode="ann")
    assert res.candidates and {c.source_id for c in res.candidates} == set(only)


async def test_auto_exact_below_threshold_and_ann_above(conn, seeded, monkeypatch):
    n = await active_chunk_count(conn, seeded["wa"], seeded["profile_id"])
    assert 0 < n < AUTO_ANN_THRESHOLD
    v = await qvec("annual leave")
    res = await dense_search(conn, seeded["wa"], v, index_profile_id=seeded["profile_id"], mode="auto")
    assert res.mode_used == "exact" and res.degraded == []
    monkeypatch.setattr(dense, "AUTO_ANN_THRESHOLD", n)  # "not below" -> ann
    res = await dense_search(conn, seeded["wa"], v, index_profile_id=seeded["profile_id"], mode="auto")
    assert res.mode_used == "ann" and res.degraded == []


async def test_old_pgvector_falls_back_to_exact_with_flag(conn, seeded, monkeypatch):
    async def old(_conn):
        return (0, 7, 4)

    monkeypatch.setattr(dense, "pgvector_version", old)
    v = await qvec("annual leave")
    res = await dense_search(conn, seeded["wa"], v, index_profile_id=seeded["profile_id"], mode="ann")
    assert res.mode_used == "exact" and res.degraded == [FLAG_ANN_UNAVAILABLE] and res.candidates
    monkeypatch.setattr(dense, "AUTO_ANN_THRESHOLD", 1)
    res = await dense_search(conn, seeded["wa"], v, index_profile_id=seeded["profile_id"], mode="auto")
    assert res.mode_used == "exact" and res.degraded == [FLAG_ANN_UNAVAILABLE]
    res = await dense_search(conn, seeded["wa"], v, index_profile_id=seeded["profile_id"], mode="exact")
    assert res.degraded == []  # exact never reports the flag


async def test_ann_sets_transaction_local_gucs_exact_does_not(conn, seeded):
    v = await qvec("annual leave")
    async with conn.transaction():
        await dense_search(conn, seeded["wa"], v, index_profile_id=seeded["profile_id"], mode="ann")
        cur = await conn.execute("select current_setting('hnsw.ef_search'), current_setting('hnsw.iterative_scan')")
        assert await cur.fetchone() == ("100", "relaxed_order")
    cur = await conn.execute("select current_setting('hnsw.ef_search'), current_setting('hnsw.iterative_scan')")
    assert await cur.fetchone() == ("40", "off")  # SET LOCAL semantics: nothing leaks past the transaction
    async with conn.transaction():
        await dense_search(conn, seeded["wa"], v, index_profile_id=seeded["profile_id"], mode="exact")
        cur = await conn.execute("select current_setting('hnsw.iterative_scan')")
        assert (await cur.fetchone())[0] == "off"


# --------------------------------------------------------------------------- EXPLAIN (D11)

def _plan_text(rows) -> str:
    return re.sub(r"\[[-0-9.e,]{100,}\]", "[VEC]", "\n".join(r[0] for r in rows))


async def _explain(conn, mode, seeded, v, source_filter=False):
    params = {"w": seeded["wa"], "p": str(seeded["profile_id"]), "q": vec_literal(v), "k": 40}
    if source_filter:
        params["src"] = [seeded["a_ids"]["hr.md"]]
    cur = await conn.execute("explain " + dense_sql(mode, source_filter), params)
    return _plan_text(await cur.fetchall())


async def test_explain_exact_never_uses_hnsw_ann_can(conn, seeded):
    """On a tiny table the planner prefers btree/seq paths. Inside a rolled-back transaction we remove
    the competing access paths (workspace btree, pkey) and disable seq/bitmap scans, which is the
    situation of a large table. The ann form must then pick HNSW; the exact form must not, even so."""
    v = await qvec("annual leave")
    for with_filter in (False, True):
        try:
            async with conn.transaction():
                await conn.execute("drop index chunk_embeddings_1536_workspace_id_index")
                await conn.execute("alter table chunk_embeddings_1536 drop constraint chunk_embeddings_1536_pkey")
                await conn.execute("select set_config('enable_seqscan','off',true), "
                                   "set_config('enable_bitmapscan','off',true)")
                exact = await _explain(conn, "exact", seeded, v, with_filter)
                ann = await _explain(conn, "ann", seeded, v, with_filter)
                assert "chunk_embeddings_1536_hnsw" not in exact, exact
                assert "chunk_embeddings_1536_hnsw" in ann, ann
                assert "= ANY" in ann  # eligibility filter sits on the HNSW scan node (iterative scan applies it)
                # real execution through the forced HNSW plan returns exactly the exact-mode result
                params = {"w": seeded["wa"], "p": str(seeded["profile_id"]), "q": vec_literal(v), "k": 40}
                if with_filter:
                    params["src"] = [seeded["a_ids"]["hr.md"]]
                await conn.execute("select set_config('hnsw.ef_search','100',true), "
                                   "set_config('hnsw.iterative_scan','relaxed_order',true)")
                got_ann = [r[0] for r in await (await conn.execute(dense_sql("ann", with_filter), params)).fetchall()]
                got_exact = [r[0] for r in await (await conn.execute(dense_sql("exact", with_filter), params)).fetchall()]
                assert got_ann == got_exact and got_ann
                raise _Rollback
        except _Rollback:
            pass


class _Rollback(Exception):
    pass


async def test_explain_default_plans_filter_before_limit(conn, seeded):
    v = await qvec("annual leave")
    for mode in ("exact", "ann"):
        plan = await _explain(conn, mode, seeded, v)
        # scope tables are all in the plan beneath the Limit
        for needle in ("index_generations", "active_indexes", "files"):
            assert needle in plan, (mode, plan)
        assert plan.index("Limit") < plan.index("index_generations")


# --------------------------------------------------------------------------- legacy-dense

@pytest.fixture()
async def legacy(conn):
    wa, wb = await new_workspace(conn), await new_workspace(conn)
    sims_a = [0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65, 0.6, 0.55, 0.5, 0.45, 0.4, 0.35, 0.3001, 0.2999, 0.1, -0.5]
    ids_a = await seed_legacy(conn, wa, "legacy-a.txt", sims_a)
    await seed_legacy(conn, wa, "legacy-scaled.txt", [0.99], scale=7.5)  # magnitude must not matter (cosine)
    await seed_legacy(conn, wa, "legacy-deleted.txt", [1.0], deleted=True)
    await seed_legacy(conn, wb, "legacy-foreign.txt", [1.0])
    return wa, wb, sims_a, ids_a


async def test_legacy_dense_reproduces_lib_search_semantics(conn, legacy):
    wa, wb, sims_a, ids_a = legacy
    q = unit3072(1.0, 1)  # e0 itself
    res = await legacy_dense_search(conn, wa, q, index_profile_id=LEGACY_PROFILE)
    assert all(c.method == "dense-legacy" for c in res)
    assert [c.rank for c in res] == list(range(1, len(res) + 1))
    assert len(res) == 10  # limit 10
    scores = [c.score for c in res]
    assert scores == sorted(scores, reverse=True)  # desc
    assert res[0].doc_name == "legacy-scaled.txt" and res[0].score == pytest.approx(0.99)
    assert [c.score for c in res[1:]] == pytest.approx(sims_a[:9])
    # threshold 0.3 filters (strictly greater): 0.3001 passes, 0.2999 / 0.1 / -0.5 do not
    allres = await legacy_dense_search(conn, wa, q, index_profile_id=LEGACY_PROFILE, limit=100)
    assert sorted(round(c.score, 4) for c in allres) == sorted([0.99] + [s for s in sims_a if s > 0.3])
    assert min(c.score for c in allres) > 0.3
    none = await legacy_dense_search(conn, wa, q, index_profile_id=LEGACY_PROFILE, threshold=0.999)
    assert none == []
    low = await legacy_dense_search(conn, wa, q, index_profile_id=LEGACY_PROFILE, threshold=-1.0, limit=100)
    assert len(low) == len(sims_a) + 1
    # never the soft-deleted source or the other workspace
    assert {c.doc_name for c in allres} == {"legacy-a.txt", "legacy-scaled.txt"}


async def test_legacy_dense_workspace_isolation_and_dims(conn, legacy):
    _, wb, *_ = legacy
    res = await legacy_dense_search(conn, wb, unit3072(1.0, 1), index_profile_id=LEGACY_PROFILE)
    assert [c.doc_name for c in res] == ["legacy-foreign.txt"]
    with pytest.raises(ValueError):
        await legacy_dense_search(conn, wb, [0.1] * 1536, index_profile_id=LEGACY_PROFILE)
