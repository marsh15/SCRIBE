"""T13 cross-tenant / inactive / deleted leak tests (INV-2, INV-3, INV-14) for BOTH retrievers.

Forbidden chunks: every chunk of workspace B (whose text matches the query perfectly, and whose
embedding is planted to be at distance 0 from the query), the SUPERSEDED generation of old.md, the
generation of the soft-deleted source, and a READY-but-never-activated generation. A positive control
proves each of them would be the nearest neighbour without the filters."""
from __future__ import annotations

import pytest
from conftest import DATABASE_URL_TEST, needs_db
from _t13 import LEAK_QUERIES, conn_for, plant, seed_sync
from scribe_ingest.embedding import FakeEmbedder
from scribe_intel.retrieval.dense import dense_search
from scribe_intel.retrieval.lexical import lexical_search
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


def forbidden(s) -> set:
    return set(s["b_chunks"]) | set(s["superseded_chunks"]) | set(s["deleted_chunks"]) | set(s["building_chunks"])


def a_sources(s) -> set[int]:
    return set(s["a_ids"].values())


@pytest.mark.parametrize("mode", ["exact", "ann", "auto"])
@pytest.mark.parametrize("q", LEAK_QUERIES)
async def test_dense_never_returns_foreign_inactive_or_deleted(conn, seeded, q, mode):
    s = seeded
    bad = forbidden(s)
    await plant(conn, sorted(bad, key=str), {}, q, EMB)
    (vec,) = await EMB.embed_queries([q])
    # control: with no scoping at all, every forbidden chunk sits at distance ~0 (would be top-ranked)
    cur = await conn.execute("select chunk_id from chunk_embeddings_1536 where (embedding <=> %s::vector) < 1e-6",
                             (vec_literal(vec),))
    assert {r[0] for r in await cur.fetchall()} >= bad
    res = await dense_search(conn, s["wa"], vec, index_profile_id=s["profile_id"], mode=mode, k=100)
    got = {c.chunk_id for c in res.candidates}
    assert got and not (got & bad)
    assert {c.source_id for c in res.candidates} <= a_sources(s)
    assert all(c.doc_name for c in res.candidates)
    # the same query for workspace B returns B's chunks only (symmetry)
    res_b = await dense_search(conn, s["wb"], vec, index_profile_id=s["profile_id"], mode=mode, k=100)
    assert res_b.candidates and {c.chunk_id for c in res_b.candidates} <= set(s["b_chunks"])
    assert not ({c.chunk_id for c in res_b.candidates} & set(s["building_chunks"]))


@pytest.mark.parametrize("q", LEAK_QUERIES)
async def test_lexical_never_returns_foreign_inactive_or_deleted(conn, seeded, q):
    s = seeded
    bad = forbidden(s)
    res = await lexical_search(conn, s["wa"], q, index_profile_id=s["profile_id"], k=100)
    got = {c.chunk_id for c in res}
    assert not (got & bad)
    assert {c.source_id for c in res} <= a_sources(s)
    res_b = await lexical_search(conn, s["wb"], q, index_profile_id=s["profile_id"], k=100)
    assert {c.chunk_id for c in res_b} <= set(s["b_chunks"])


async def test_lexical_control_forbidden_text_exists_and_matches(conn, seeded):
    """The leak test is only meaningful if forbidden chunks match the query at the SQL level."""
    s = seeded
    for q, ids in [("quokka", s["deleted_chunks"]), ("ZEBRAFISH_OLD", s["superseded_chunks"]),
                   ("AARDVARK_BUILDING", s["building_chunks"])]:
        cur = await conn.execute(
            "select count(*) from chunks where id = any(%s) and fts_simple @@ websearch_to_tsquery('simple', %s)",
            (ids, q))
        assert (await cur.fetchone())[0] >= 1, q
        assert await lexical_search(conn, s["wa"], q, index_profile_id=s["profile_id"]) == []
    # identical text in workspace B: A sees only its own copy
    res = await lexical_search(conn, s["wa"], "ERR_AUTH_403", index_profile_id=s["profile_id"])
    assert [c.source_id for c in res] == [s["a_ids"]["api-errors.md"]]
    resb = await lexical_search(conn, s["wb"], "ERR_AUTH_403", index_profile_id=s["profile_id"])
    assert {c.source_id for c in resb} <= set(s["b_ids"].values()) and resb


@pytest.mark.parametrize("mode", ["exact", "ann"])
async def test_source_ids_with_foreign_deleted_or_inactive_sources(conn, seeded, mode):
    s = seeded
    (vec,) = await EMB.embed_queries(["ERR_AUTH_403"])
    foreign = [s["b_ids"]["api-errors.md"], s["b_ids"]["b-only.md"]]
    others = [s["a_ids"]["gone.md"], s["a_ids"]["building.md"]]  # deleted / never-activated
    mine = s["a_ids"]["api-errors.md"]
    for ids, expect in [(foreign, set()), (others, set()), (foreign + others, set()), (foreign + [mine], {mine})]:
        d = await dense_search(conn, s["wa"], vec, index_profile_id=s["profile_id"], source_ids=ids, mode=mode)
        assert {c.source_id for c in d.candidates} == expect, (ids, mode)
        lx = await lexical_search(conn, s["wa"], "ERR_AUTH_403 quokka AARDVARK_BUILDING", index_profile_id=s["profile_id"],
                                  source_ids=ids)
        assert {c.source_id for c in lx} <= expect
    lx = await lexical_search(conn, s["wa"], "ERR_AUTH_403", index_profile_id=s["profile_id"], source_ids=foreign + [mine])
    assert {c.source_id for c in lx} == {mine}


async def test_dead_end_cases(conn, seeded):
    s = seeded
    (vec,) = await EMB.embed_queries(["x"])
    # workspace with nothing indexed / unknown workspace
    unknown = "00000000-0000-4000-8000-0000000000aa"
    assert (await dense_search(conn, unknown, vec, index_profile_id=s["profile_id"])).candidates == []
    assert await lexical_search(conn, unknown, "leave", index_profile_id=s["profile_id"]) == []
    # a different index profile (legacy) has no active generations for these sources
    legacy = "10000000-0000-4000-8000-000000000001"
    assert (await dense_search(conn, s["wa"], vec, index_profile_id=legacy)).candidates == []
    assert await lexical_search(conn, s["wa"], "leave", index_profile_id=legacy) == []
