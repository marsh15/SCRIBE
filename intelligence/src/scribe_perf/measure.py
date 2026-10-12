"""Measurements against a loaded perf database. Uses the PRODUCTION SQL builders (dense_sql, lexical_sql) so the
numbers describe the shipped query shapes; only the GUCs (ef_search / iterative_scan) are varied."""

from __future__ import annotations

import asyncio
import time
from typing import Sequence

import psycopg
from psycopg_pool import AsyncConnectionPool

from scribe_intel.retrieval.dense import DIMS, dense_search, dense_sql, pgvector_version, supports_iterative_scan
from scribe_intel.retrieval.lexical import lexical_search
from scribe_intel.retrieval.queries import SCOPE_JOINS, scope_params, scope_where, vec_literal

from .pgdb import HALF_TABLE, HNSW_INDEX
from .stats import latency_summary, mean_recall, throughput

K = 40            # production dense k (retrieval profiles)
WARMUP = 5
EF_VALUES = (40, 100, 200)
ITERATIVE = ("off", "relaxed_order")


def sql_for(mode: str, table: str) -> str:
    """Production dense_sql(mode) retargeted at the vector or halfvec embeddings table."""
    sql = dense_sql(mode, False)  # type: ignore[arg-type]
    if table == "vector":
        return sql
    return sql.replace("chunk_embeddings_1536", HALF_TABLE).replace("::vector", f"::halfvec({DIMS})")


async def run_one(conn: psycopg.AsyncConnection, sql: str, params: dict, ef: int | None, iterative: str | None
                  ) -> tuple[list, float]:
    """One timed dense query in its own transaction (GUC set included in the timing, as in production)."""
    t0 = time.perf_counter()
    async with conn.transaction():
        if ef is not None:
            await conn.execute("select set_config('hnsw.ef_search', %s, true), set_config('hnsw.iterative_scan', %s, true)",
                               (str(ef), iterative or "off"))
        cur = await conn.execute(sql, params)
        rows = await cur.fetchall()
    return [r[0] for r in rows], (time.perf_counter() - t0) * 1000


async def run_set(conn, sql, wid, profile_id, qvecs: Sequence[Sequence[float]], ef=None, iterative=None
                  ) -> tuple[list[list], list[float]]:
    lits = [vec_literal(q) for q in qvecs]
    base = scope_params(wid, profile_id, None)
    for lit in lits[:WARMUP]:
        await run_one(conn, sql, {**base, "q": lit, "k": K}, ef, iterative)
    ids, ms = [], []
    for lit in lits:
        r, t = await run_one(conn, sql, {**base, "q": lit, "k": K}, ef, iterative)
        ids.append(r)
        ms.append(t)
    return ids, ms


def _recall_block(truth: list[list], got: list[list]) -> dict:
    pairs = list(zip(truth, got))
    return {"recall10": round(mean_recall(pairs, 10), 4), "recall40": round(mean_recall(pairs, 40), 4),
            "mean_rows": round(sum(len(g) for g in got) / max(1, len(got)), 2)}


async def explain_uses_hnsw(conn, sql, wid, profile_id, qvec, ef=None, iterative=None) -> bool:
    params = {**scope_params(wid, profile_id, None), "q": vec_literal(qvec), "k": K}
    async with conn.transaction():
        if ef is not None:
            await conn.execute("select set_config('hnsw.ef_search', %s, true), set_config('hnsw.iterative_scan', %s, true)",
                               (str(ef), iterative or "off"))
        cur = await conn.execute("explain (costs off) " + sql, params)
        text = "\n".join(r[0] for r in await cur.fetchall())
    return "hnsw" in text.lower()


class _Rollback(Exception):
    pass


async def eligible_ids_ms(conn, wid: str, profile_id: str, reps: int = 20) -> dict:
    """Cost of building the eligibility array (the InitPlan of the production ann query), measured on its own."""
    sql = f"select array(select c.id from chunks c {SCOPE_JOINS} where {scope_where(None)})"
    params = scope_params(wid, profile_id, None)
    ms = []
    for _ in range(reps + 2):
        t0 = time.perf_counter()
        async with conn.transaction():
            await (await conn.execute(sql, params)).fetchone()
        ms.append((time.perf_counter() - t0) * 1000)
    return latency_summary(ms[2:])


async def forced_hnsw_section(conn, workspaces, profile_id, queries, truths, iterative_ok: bool) -> dict:
    """The 'big table' situation (see tests/retrieval/test_dense.py): inside a ROLLED-BACK transaction the pkey and the
    workspace btree of both embedding tables are dropped and seq/bitmap scans disabled, so the ann SQL must walk HNSW.
    The planner's own choice is measured separately (ann_planner); this section shows what HNSW itself does."""
    out: dict = {}
    try:
        async with conn.transaction():
            for t in ("chunk_embeddings_1536", HALF_TABLE):
                await conn.execute(f"drop index {t}_workspace_id_index" if t == "chunk_embeddings_1536"
                                   else f"drop index {t}_workspace")
                await conn.execute(f"alter table {t} drop constraint {t}_pkey")
            await conn.execute("select set_config('enable_seqscan','off',true), set_config('enable_bitmapscan','off',true)")
            for ws in workspaces:
                wid = ws["id"]
                qv, truth = queries.get(wid) or [], truths.get(wid)
                if not qv or truth is None:
                    continue
                entry: dict = {"uses_hnsw": await explain_uses_hnsw(conn, sql_for("ann", "vector"), wid, profile_id, qv[0],
                                                                    100, "relaxed_order"), "grid": []}
                if iterative_ok:
                    for ef in EF_VALUES:
                        for it in ITERATIVE:
                            got, ms = await run_set(conn, sql_for("ann", "vector"), wid, profile_id, qv, ef, it)
                            entry["grid"].append({"ef_search": ef, "iterative_scan": it, "latency_ms": latency_summary(ms),
                                                  **_recall_block(truth, got)})
                    got, ms = await run_set(conn, sql_for("ann", "halfvec"), wid, profile_id, qv, 100, "relaxed_order")
                    entry["halfvec_ef100_relaxed"] = {"latency_ms": latency_summary(ms), **_recall_block(truth, got),
                                                      "uses_hnsw": await explain_uses_hnsw(
                                                          conn, sql_for("ann", "halfvec"), wid, profile_id, qv[0], 100, "relaxed_order")}
                out[f"ws{ws['index']}"] = entry
            raise _Rollback
    except _Rollback:
        pass
    return out


async def dense_section(conn, workspaces: list[dict], profile_id: str, queries: dict[str, list], iterative_ok: bool) -> dict:
    """Per workspace: exact vector (ground truth), exact halfvec, ANN as the planner runs it, eligible-array cost;
    then HNSW forced (grid over ef_search x iterative_scan, plus halfvec) in a rolled-back transaction."""
    out: dict = {}
    truths: dict[str, list] = {}
    for ws in workspaces:
        wid, qv = ws["id"], queries[ws["id"]]
        if not qv:
            continue
        entry: dict = {"n_chunks": ws["n_chunks"], "n_queries": len(qv)}
        truth, ms = await run_set(conn, sql_for("exact", "vector"), wid, profile_id, qv)
        truths[wid] = truth
        entry["exact_vector"] = {"latency_ms": latency_summary(ms), "uses_hnsw": await explain_uses_hnsw(
            conn, sql_for("exact", "vector"), wid, profile_id, qv[0])}
        got, ms = await run_set(conn, sql_for("exact", "halfvec"), wid, profile_id, qv)
        entry["exact_halfvec"] = {"latency_ms": latency_summary(ms), **_recall_block(truth, got)}
        if iterative_ok:
            got, ms = await run_set(conn, sql_for("ann", "vector"), wid, profile_id, qv, 100, "relaxed_order")
            entry["ann_planner"] = {"latency_ms": latency_summary(ms), **_recall_block(truth, got),
                                    "uses_hnsw": await explain_uses_hnsw(conn, sql_for("ann", "vector"), wid, profile_id,
                                                                         qv[0], 100, "relaxed_order")}
        entry["eligible_ids_ms"] = await eligible_ids_ms(conn, wid, profile_id)
        out[f"ws{ws['index']}"] = entry
    forced = await forced_hnsw_section(conn, workspaces, profile_id, queries, truths, iterative_ok)
    for k, v in forced.items():
        out[k]["hnsw_forced"] = v
    return out


async def lexical_section(conn, workspaces: list[dict], profile_id: str, terms: list[str]) -> dict:
    out: dict = {}
    for ws in workspaces:
        if ws["index"] not in (0, len(workspaces) - 1):
            continue
        for q in terms[:WARMUP]:
            await lexical_search(conn, ws["id"], q, index_profile_id=profile_id, k=K)
        ms, hits = [], []
        for q in terms:
            t0 = time.perf_counter()
            res = await lexical_search(conn, ws["id"], q, index_profile_id=profile_id, k=K)
            ms.append((time.perf_counter() - t0) * 1000)
            hits.append(len(res))
        out[f"ws{ws['index']}"] = {"n_chunks": ws["n_chunks"], "latency_ms": latency_summary(ms),
                                   "mean_hits": round(sum(hits) / len(hits), 2), "n_queries": len(terms)}
    return out


async def concurrency_section(url: str, wid: str, profile_id: str, qvecs: list[list[float]], mode: str,
                              clients=(1, 10, 50), total_per_level: int = 200) -> list[dict]:
    """N client tasks, each borrowing a pooled connection per query and calling the production dense_search."""
    out = []
    for n in clients:
        per = max(5, total_per_level // n)
        async with AsyncConnectionPool(url, min_size=n, max_size=n, open=False,
                                       kwargs={"autocommit": True, "prepare_threshold": None}) as pool:
            await pool.wait()
            lat: list[float] = []
            modes: set[str] = set()

            async def client(i: int) -> None:
                for j in range(per):
                    q = qvecs[(i * per + j) % len(qvecs)]
                    async with pool.connection() as conn:
                        t0 = time.perf_counter()
                        res = await dense_search(conn, wid, q, index_profile_id=profile_id, mode=mode, k=K)  # type: ignore[arg-type]
                        lat.append((time.perf_counter() - t0) * 1000)
                        modes.add(res.mode_used)

            w0 = time.perf_counter()
            await asyncio.gather(*(client(i) for i in range(n)))
            wall = time.perf_counter() - w0
        out.append({"clients": n, "queries": n * per, "qps": round(throughput(n * per, wall), 2),
                    "latency_ms": latency_summary(lat), "mode_used": sorted(modes), "requested_mode": mode})
    return out


async def storage_section(conn, n_chunks: int) -> dict:
    async def one(sql: str):
        return (await (await conn.execute(sql)).fetchone())[0]

    vec_bytes = await one("select avg(pg_column_size(embedding))::float from chunk_embeddings_1536")
    half_bytes = await one(f"select avg(pg_column_size(embedding))::float from {HALF_TABLE}")
    sizes = {
        "table_vector": await one("select pg_table_size('chunk_embeddings_1536')"),  # heap + TOAST
        "table_pk_vector": await one("select pg_relation_size('chunk_embeddings_1536_pkey')"),
        "hnsw_vector": await one(f"select pg_relation_size('{HNSW_INDEX}')"),
        "table_halfvec": await one(f"select pg_table_size('{HALF_TABLE}')"),  # heap + TOAST
        "hnsw_halfvec": await one(f"select pg_relation_size('{HALF_TABLE}_hnsw')"),
        "chunks_total": await one("select pg_total_relation_size('chunks')"),
        "chunks_gin_fts_en": await one("select pg_relation_size('chunks_fts_en_gin')"),
        "chunks_gin_fts_simple": await one("select pg_relation_size('chunks_fts_simple_gin')"),
        "database": await one("select pg_database_size(current_database())"),
    }
    n_emb = await one("select count(*) from chunk_embeddings_1536")
    per = {k: round(v / n_emb, 1) for k, v in sizes.items() if k != "database"}
    return {"embeddings": n_emb, "children": n_chunks, "avg_column_bytes_vector": round(vec_bytes, 1),
            "avg_column_bytes_halfvec": round(half_bytes, 1), "relation_bytes": sizes, "bytes_per_embedding": per}


async def environment(conn) -> dict:
    ver = await pgvector_version(conn)
    pg = (await (await conn.execute("show server_version")).fetchone())[0]
    guc = {}
    for g in ("shared_buffers", "work_mem", "effective_cache_size", "max_parallel_workers_per_gather", "max_connections",
             "hnsw.ef_search", "hnsw.iterative_scan", "hnsw.max_scan_tuples"):
        try:
            guc[g] = (await (await conn.execute(f"show {g}")).fetchone())[0]
        except psycopg.Error:
            guc[g] = None
    return {"postgres": pg, "pgvector": ".".join(map(str, ver)) if ver else None,
            "iterative_scan_supported": supports_iterative_scan(ver), "settings": guc}
