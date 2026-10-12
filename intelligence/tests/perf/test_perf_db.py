"""T32: end-to-end at tiny scale (200 chunks) in a throwaway database created and dropped by the test."""
import asyncio
import uuid

import psycopg
import pytest
from conftest import DATABASE_URL_TEST, needs_db
from scribe_perf import measure
from scribe_perf.load import load_scale
from scribe_perf.pgdb import HALF_TABLE, create_database, database_exists, drop_database, ensure_schema, with_database
from scribe_perf.runner import cycle_to, measure_loaded
from scribe_perf.stats import recall_at_k
from scribe_perf.synth import lexical_queries, make_vocab

pytestmark = needs_db


@pytest.fixture(scope="module")
def perf_db():
    url = with_database(DATABASE_URL_TEST, f"perf_test_{uuid.uuid4().hex[:8]}")
    try:
        ensure_schema(url)
        loaded = load_scale(url, 200, seed=5)
        yield url, loaded
    finally:
        drop_database(url)
        assert not database_exists(url)


def test_load_counts_and_active_scope(perf_db):
    url, loaded = perf_db
    with psycopg.connect(url) as c:
        n = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
        assert n("select count(*) from chunks where parent_id is not null") == 200
        assert n("select count(*) from chunk_embeddings_1536") == 200 == n(f"select count(*) from {HALF_TABLE}")
        assert n("select count(*) from workspaces") == 5
        assert n("select count(*) from index_generations where status <> 'ACTIVE'") == 0
        assert n("select count(*) from active_indexes") == n("select count(*) from files")
        assert n("select count(*) from chunks where fts_en is null or fts_simple is null") == 0
        norms = c.execute("select max(abs(vector_norm(embedding) - 1)) from chunk_embeddings_1536").fetchone()[0]
        assert norms < 1e-4  # normalized
        assert n("select count(*) from pg_indexes where indexname = 'chunk_embeddings_1536_hnsw'") == 1
        assert set(loaded.build_s) == {"hnsw_vector_s", "halfvec_table_s", "hnsw_halfvec_s"}


async def test_exact_matches_independent_sql_and_ann_runs(perf_db):
    url, loaded = perf_db
    ws = loaded.workspaces[0]
    q = loaded.queries[str(ws.id)][0]
    async with await psycopg.AsyncConnection.connect(url, autocommit=True, prepare_threshold=None) as conn:
        ids, ms = await measure.run_one(conn, measure.sql_for("exact", "vector"),
                                        {"w": str(ws.id), "p": loaded.index_profile_id, "q": measure.vec_literal(q),
                                         "k": 10}, None, None)
        cur = await conn.execute("select chunk_id from chunk_embeddings_1536 where workspace_id = %s "
                                 "order by embedding <=> %s::vector, chunk_id limit 10", (str(ws.id), measure.vec_literal(q)))
        assert ids == [r[0] for r in await cur.fetchall()] and ms > 0
        truth, _ = await measure.run_set(conn, measure.sql_for("exact", "vector"), str(ws.id), loaded.index_profile_id, [q])
        ann, _ = await measure.run_set(conn, measure.sql_for("ann", "vector"), str(ws.id), loaded.index_profile_id, [q], 100,
                                       "relaxed_order")
        assert recall_at_k(truth[0], ann[0], 10) >= 0.9 and len(ann[0]) == len(truth[0])
        half = await measure.storage_section(conn, 200)
        assert half["avg_column_bytes_halfvec"] < half["avg_column_bytes_vector"]


async def test_measure_loaded_document_shape(perf_db):
    url, loaded = perf_db
    res = await measure_loaded(url, loaded, n_queries=6, concurrency_modes=["auto"])
    assert res["environment"]["iterative_scan_supported"] and res["environment"]["pgvector"]
    e = res["dense"]["ws0"]
    assert e["exact_vector"]["uses_hnsw"] is False and e["ann_planner"]["recall10"] >= 0.9
    forced = e["hnsw_forced"]  # pkey/workspace btree dropped in a rolled-back transaction -> the planner must use HNSW
    assert forced["uses_hnsw"] is True and forced["halfvec_ef100_relaxed"]["uses_hnsw"] is True
    assert len(forced["grid"]) == 6 and all(0 <= g["recall10"] <= 1 for g in forced["grid"])
    assert e["eligible_ids_ms"]["n"] == 20
    assert e["exact_halfvec"]["recall10"] >= 0.9
    assert res["lexical"]["ws0"]["n_queries"] == 6
    conc = res["concurrency"]["auto"]
    assert [r["clients"] for r in conc] == [1, 10, 50] and all(r["qps"] > 0 and r["mode_used"] == ["exact"] for r in conc)
    assert res["storage"]["bytes_per_embedding"]["hnsw_vector"] > res["storage"]["bytes_per_embedding"]["hnsw_halfvec"]
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as c:  # the rollback really rolled back
        cur = await c.execute("select count(*) from pg_indexes where indexname = 'chunk_embeddings_1536_pkey'")
        assert (await cur.fetchone())[0] == 1
    assert lexical_queries(5, make_vocab(5), 3) == lexical_queries(5, make_vocab(5), 3)
    assert cycle_to([1], 3) == [1, 1, 1]


def test_refuses_non_perf_database():
    with pytest.raises(ValueError):
        drop_database(DATABASE_URL_TEST)
    with pytest.raises(ValueError):
        create_database(DATABASE_URL_TEST)
    with pytest.raises(ValueError):
        load_scale(DATABASE_URL_TEST, 10, seed=1)
